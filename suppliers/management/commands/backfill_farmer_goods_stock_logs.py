from django.core.management.base import BaseCommand
from django.db import transaction

from stock_management.models import StockLog
from suppliers.models import FarmerGoodsIssue
from suppliers.services import log_farmer_goods_issue_stock, sync_farmer_goods_issue_stock_log_dates


class Command(BaseCommand):
    help = (
        "Create StockLog rows for historical farmer-goods issues that already "
        "affected stock (accepted / not_required). Skips pending, rejected, "
        "and issues that already have a fg-issue reference log."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count eligible issues without writing StockLog rows.",
        )
        parser.add_argument(
            "--product",
            type=int,
            default=None,
            help="Limit backfill to one product id.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        product_id = options["product"]

        qs = (
            FarmerGoodsIssue.objects.select_related("product", "from_branch")
            .exclude(
                driver_status__in=[
                    FarmerGoodsIssue.DriverStatus.PENDING,
                    FarmerGoodsIssue.DriverStatus.REJECTED,
                ]
            )
            .order_by("id")
        )
        if product_id:
            qs = qs.filter(product_id=product_id)

        existing_refs = set(
            StockLog.objects.filter(reference__startswith="fg-issue:").values_list(
                "reference", flat=True
            )
        )

        created = 0
        skipped = 0
        for issue in qs.iterator(chunk_size=500):
            ref = f"fg-issue:{issue.pk}"
            if ref in existing_refs:
                skipped += 1
                continue
            if dry_run:
                created += 1
                continue
            with transaction.atomic():
                log = log_farmer_goods_issue_stock(issue, at=issue.date)
            if log:
                created += 1
                existing_refs.add(ref)
            else:
                skipped += 1

        repaired = 0
        if not dry_run:
            repaired = sync_farmer_goods_issue_stock_log_dates(product_id=product_id)

        action = "Would create" if dry_run else "Created"
        self.stdout.write(
            self.style.SUCCESS(
                f"{action} {created} StockLog row(s); skipped {skipped} already logged; "
                f"repaired {repaired} log date(s) to the receipt date."
            )
        )
