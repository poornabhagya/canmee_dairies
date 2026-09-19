"""Sync FarmerGoodsStock.quantity from confirmed receipts minus applied issues."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from stock_management.models import Product
from suppliers.models import FarmerGoodsIssue, FarmerGoodsStock, GRN, GRNItem

# Stock corrections restore farmer-goods that were issued; they must credit the pool.
POOL_RECEIPT_GRN_TYPES = (GRN.GRNType.FARMER_GOODS, GRN.GRNType.STOCK_CORRECTION)


def farmer_goods_pool_ledger(product_id):
    """
    Pool ledger for one product:
      received = confirmed farmer-goods GRNs + stock-correction GRNs
      issued = applied issues (not pending / rejected)
      target = max(received − issued, 0)
    """
    received_fg = Decimal("0")
    received_correction = Decimal("0")
    for row in (
        GRNItem.objects.filter(
            product_id=product_id,
            grn__status=GRN.Status.CONFIRMED,
            grn__grn_type__in=POOL_RECEIPT_GRN_TYPES,
        )
        .values("grn__grn_type")
        .annotate(total=Sum("quantity"))
    ):
        qty = row["total"] or Decimal("0")
        if row["grn__grn_type"] == GRN.GRNType.STOCK_CORRECTION:
            received_correction += qty
        else:
            received_fg += qty
    received = (received_fg + received_correction).quantize(Decimal("0.01"))
    issued = (
        FarmerGoodsIssue.objects.filter(product_id=product_id)
        .exclude(
            driver_status__in=[
                FarmerGoodsIssue.DriverStatus.PENDING,
                FarmerGoodsIssue.DriverStatus.REJECTED,
            ]
        )
        .aggregate(total=Sum("quantity"))["total"]
        or Decimal("0")
    )
    issued = issued.quantize(Decimal("0.01"))
    target = (received - issued).quantize(Decimal("0.01"))
    if target < Decimal("0"):
        target = Decimal("0.00")
    return {
        "received_fg": received_fg.quantize(Decimal("0.01")),
        "received_correction": received_correction.quantize(Decimal("0.01")),
        "received": received,
        "issued": issued,
        "target": target,
    }


def farmer_goods_pool_target(product_id):
    """Return (target, received, issued) for issue_goods / sync."""
    led = farmer_goods_pool_ledger(product_id)
    return led["target"], led["received"], led["issued"]


def find_farmer_goods_pool_sync(*, product_id=None):
    """Return rows where stored pool differs from target."""
    products = Product.objects.order_by("name")
    if product_id:
        products = products.filter(pk=product_id)

    rows = []
    for product in products.iterator(chunk_size=200):
        led = farmer_goods_pool_ledger(product.id)
        target = led["target"]
        stock = FarmerGoodsStock.objects.filter(product_id=product.id).first()
        current = stock.quantity if stock else Decimal("0")
        if current == target:
            continue
        # Skip empty products with no stock row and zero target.
        if stock is None and target == 0:
            continue
        rows.append(
            {
                "product": product,
                "product_id": product.id,
                "product_name": product.name,
                "current": current,
                "target": target,
                "received": led["received"],
                "received_fg": led["received_fg"],
                "received_correction": led["received_correction"],
                "issued": led["issued"],
                "delta": (target - current).quantize(Decimal("0.01")),
            }
        )
    return rows


@transaction.atomic
def apply_farmer_goods_pool_sync(*, product_id=None, actor=None):
    if actor is not None and (
        not getattr(actor, "is_superuser", False) or not actor.is_active
    ):
        raise ValidationError("Only an active superuser can sync farmer goods pool.")

    rows = find_farmer_goods_pool_sync(product_id=product_id)
    updated = 0
    created = 0
    for row in rows:
        stock, was_created = FarmerGoodsStock.objects.select_for_update().get_or_create(
            product_id=row["product_id"],
            defaults={"quantity": row["target"]},
        )
        if was_created:
            created += 1
            continue
        if stock.quantity != row["target"]:
            stock.quantity = row["target"]
            stock.save(update_fields=["quantity", "updated_at"])
            updated += 1
    return {
        "rows": rows,
        "updated": updated,
        "created": created,
        "count": len(rows),
    }
