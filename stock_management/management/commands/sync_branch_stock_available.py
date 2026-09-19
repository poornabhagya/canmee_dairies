from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum

from branches.models import Branch
from stock_management.models import (
    BranchStock,
    Product,
    StockIssue,
    StockTransfer,
    StockTransferLine,
)
from suppliers.models import FarmerGoodsIssue, GRN, GRNItem


def branch_product_available(branch_id, product_id):
    """Match Stock Overview Branch Stock Available formula."""
    grn_total = (
        GRNItem.objects.filter(
            product_id=product_id,
            grn__status=GRN.Status.CONFIRMED,
            grn__branch_id=branch_id,
        ).aggregate(total=Sum("quantity"))["total"]
        or Decimal("0")
    )
    in_transfer = (
        StockTransferLine.objects.filter(
            product_id=product_id, note__to_branch_id=branch_id
        ).aggregate(total=Sum("quantity"))["total"]
        or Decimal("0")
    )
    in_legacy = (
        StockTransfer.objects.filter(
            product_id=product_id, to_branch_id=branch_id
        ).aggregate(total=Sum("quantity"))["total"]
        or Decimal("0")
    )
    out_transfer = (
        StockTransferLine.objects.filter(
            product_id=product_id, note__from_branch_id=branch_id
        ).aggregate(total=Sum("quantity"))["total"]
        or Decimal("0")
    )
    stock_issue = (
        StockIssue.objects.filter(product_id=product_id, branch_id=branch_id).aggregate(
            total=Sum("quantity")
        )["total"]
        or Decimal("0")
    )
    fg_issue = (
        FarmerGoodsIssue.objects.filter(
            product_id=product_id,
            from_branch_id=branch_id,
            issue_to_type__in=[
                FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
                FarmerGoodsIssue.IssueToType.FARMER,
            ],
        )
        .exclude(driver_status=FarmerGoodsIssue.DriverStatus.REJECTED)
        .aggregate(total=Sum("quantity"))["total"]
        or Decimal("0")
    )
    point_farmer = stock_issue + fg_issue
    received = grn_total + in_transfer + in_legacy
    return received - (out_transfer + point_farmer)


class Command(BaseCommand):
    help = (
        "Set BranchStock.quantity to the Branch Stock 'Available' movement formula "
        "so on-hand matches Received − Transfer − Point/Farmer."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Preview changes without writing BranchStock rows.",
        )
        parser.add_argument(
            "--product",
            type=int,
            default=None,
            help="Limit to one product id.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        product_id = options["product"]

        branches = list(Branch.objects.order_by("id").values_list("id", "name"))
        products = Product.objects.order_by("id")
        if product_id:
            products = products.filter(id=product_id)

        updated = 0
        created = 0
        unchanged = 0
        for product in products.iterator(chunk_size=200):
            for branch_id, branch_name in branches:
                available = branch_product_available(branch_id, product.id)
                # Never write negative BranchStock from sync — floor at zero.
                if available < Decimal("0"):
                    available = Decimal("0")
                existing = BranchStock.objects.filter(
                    branch_id=branch_id, product_id=product.id
                ).first()
                current = existing.quantity if existing else None
                if current is not None and current == available:
                    unchanged += 1
                    continue
                if current is None and available == 0:
                    unchanged += 1
                    continue
                self.stdout.write(
                    f"{product.name} @ {branch_name}: "
                    f"{current if current is not None else '-'} -> {available}"
                )
                if dry_run:
                    if existing:
                        updated += 1
                    else:
                        created += 1
                    continue
                with transaction.atomic():
                    row, was_created = BranchStock.objects.select_for_update().get_or_create(
                        branch_id=branch_id,
                        product_id=product.id,
                        defaults={"quantity": available},
                    )
                    if was_created:
                        created += 1
                    else:
                        if row.quantity != available:
                            row.quantity = available
                            row.save(update_fields=["quantity", "updated_at"])
                            updated += 1
                        else:
                            unchanged += 1

        action = "Would update" if dry_run else "Updated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} {updated} row(s), create {created}, unchanged {unchanged}."
            )
        )
