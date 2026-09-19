"""Shared helpers to find and correct negative branch stock via adjustment GRNs."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from branches.models import Branch
from stock_management.models import (
    BranchStock,
    Product,
    StockIssue,
    StockTransfer,
    StockTransferLine,
)

from .models import FarmerGoodsIssue, GRN, GRNItem, Supplier
from .services import confirm_grn, finalize_grn_totals, latest_grn_line_prices


def branch_product_available(branch_id, product_id):
    """Match Stock Overview Branch Stock Available (excludes rejected FG issues)."""
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
    received = grn_total + in_transfer + in_legacy
    return received - (out_transfer + stock_issue + fg_issue)


def resolve_adjustment_supplier(supplier_id=None, grn_type="farmer_goods"):
    if supplier_id:
        supplier = Supplier.objects.filter(pk=supplier_id).first()
        if supplier is None:
            raise ValidationError(f"Supplier #{supplier_id} not found.")
        if supplier.status != Supplier.Status.ACTIVE:
            raise ValidationError(f"Supplier #{supplier_id} is not active.")
        if supplier.category == Supplier.Category.RAW_MILK_SUPPLIER:
            raise ValidationError("Cannot use a raw milk supplier for GRNs.")
        return supplier

    preferred = (
        Supplier.Category.FARMER_GOODS_SUPPLIER
        if grn_type == GRN.GRNType.FARMER_GOODS or grn_type == "farmer_goods"
        else Supplier.Category.CORPORATE_GOODS_SUPPLIER
    )
    supplier = (
        Supplier.objects.filter(status=Supplier.Status.ACTIVE, category=preferred)
        .order_by("id")
        .first()
    )
    if supplier is None:
        supplier = (
            Supplier.objects.filter(status=Supplier.Status.ACTIVE)
            .exclude(category=Supplier.Category.RAW_MILK_SUPPLIER)
            .order_by("id")
            .first()
        )
    if supplier is None:
        raise ValidationError("No active non-raw-milk supplier found.")
    return supplier


def find_negative_branch_stock(*, branch_id=None, product_id=None):
    """
    Return (corrections_by_branch, sync_only, flat_rows).

    corrections_by_branch: {branch_id: [row dicts needing GRN top-up]}
    sync_only: rows where on-hand is negative but movement available is not
    flat_rows: combined list for UI tables
    """
    branches = Branch.objects.order_by("name")
    if branch_id:
        branches = branches.filter(pk=branch_id)
    products = Product.objects.order_by("name")
    if product_id:
        products = products.filter(pk=product_id)

    corrections_by_branch = {}
    sync_only = []
    for branch in branches:
        for product in products.iterator(chunk_size=200):
            available = branch_product_available(branch.id, product.id)
            stock = BranchStock.objects.filter(
                branch_id=branch.id, product_id=product.id
            ).first()
            on_hand = stock.quantity if stock else Decimal("0")

            if available < Decimal("0"):
                shortfall = (-available).quantize(Decimal("0.01"))
                row = {
                    "branch": branch,
                    "product": product,
                    "available": available,
                    "on_hand": on_hand,
                    "shortfall": shortfall,
                    "action": "grn",
                }
                corrections_by_branch.setdefault(branch.id, []).append(row)
            elif on_hand < Decimal("0"):
                sync_only.append(
                    {
                        "branch": branch,
                        "product": product,
                        "available": available,
                        "on_hand": on_hand,
                        "shortfall": Decimal("0"),
                        "action": "sync",
                        "note": (
                            "Movement Available is already non-negative "
                            "(rejected issues are not counted). "
                            "Only BranchStock on-hand is stale and will be synced."
                        ),
                    }
                )

    flat_rows = []
    for rows in corrections_by_branch.values():
        flat_rows.extend(rows)
    flat_rows.extend(sync_only)
    flat_rows.sort(key=lambda r: (r["branch"].name, r["product"].name))
    return corrections_by_branch, sync_only, flat_rows


@transaction.atomic
def apply_negative_branch_stock_corrections(
    *,
    actor,
    supplier,
    grn_type=GRN.GRNType.FARMER_GOODS,
    branch_id=None,
    product_id=None,
):
    """
    Create confirmed STOCK CORRECTION GRNs (rate/issue from last purchase GRN)
    and sync BranchStock. Returns dict with created_grns, synced, line_count.

    ``grn_type`` is accepted for caller compatibility (supplier channel preference)
    but created documents are always ``GRN.GRNType.STOCK_CORRECTION``.
    """
    if actor is None or not getattr(actor, "is_superuser", False) or not actor.is_active:
        raise ValidationError("Only an active superuser can apply stock corrections.")

    corrections_by_branch, sync_only, _ = find_negative_branch_stock(
        branch_id=branch_id, product_id=product_id
    )
    if not corrections_by_branch and not sync_only:
        return {"created_grns": [], "synced": 0, "line_count": 0}

    synced = 0
    for row in sync_only:
        bs, _ = BranchStock.objects.select_for_update().get_or_create(
            branch_id=row["branch"].id,
            product_id=row["product"].id,
            defaults={"quantity": row["available"]},
        )
        if bs.quantity != row["available"]:
            bs.quantity = row["available"]
            bs.save(update_fields=["quantity", "updated_at"])
            synced += 1

    created_grns = []
    line_count = 0
    for _branch_id, rows in corrections_by_branch.items():
        branch = rows[0]["branch"]
        grn = GRN.objects.create(
            supplier=supplier,
            branch=branch,
            grn_type=GRN.GRNType.STOCK_CORRECTION,
            date=timezone.localdate(),
            status=GRN.Status.DRAFT,
            discount_scope=GRN.DiscountScope.LINE,
            created_by=actor if getattr(actor, "pk", None) else None,
        )
        for row in rows:
            unit_price, issuing_price = latest_grn_line_prices(
                row["product"].id, branch.id
            )
            GRNItem.objects.create(
                grn=grn,
                product=row["product"],
                quantity=row["shortfall"],
                free_quantity=Decimal("0"),
                unit_price=unit_price,
                issuing_price=issuing_price,
                line_discount_percent=Decimal("0"),
                line_discount_amount=Decimal("0"),
                total_price=Decimal("0.00"),
            )
            line_count += 1
        finalize_grn_totals(grn)
        confirm_grn(grn=grn, actor=actor)
        for row in rows:
            new_available = branch_product_available(branch.id, row["product"].id)
            if new_available < Decimal("0"):
                raise ValidationError(
                    f"Correction failed for {row['product'].name} @ {branch.name}: "
                    f"available still {new_available}."
                )
            bs, _ = BranchStock.objects.select_for_update().get_or_create(
                branch_id=branch.id,
                product_id=row["product"].id,
                defaults={"quantity": new_available},
            )
            if bs.quantity != new_available:
                bs.quantity = new_available
                bs.save(update_fields=["quantity", "updated_at"])
        created_grns.append(grn.grn_number)

    return {
        "created_grns": created_grns,
        "synced": synced,
        "line_count": line_count,
        "grn_count": len(created_grns),
    }
