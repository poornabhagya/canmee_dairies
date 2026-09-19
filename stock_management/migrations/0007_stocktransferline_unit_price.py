from decimal import Decimal

from django.db import migrations, models
from django.db.models import Q


def backfill_transfer_line_unit_prices(apps, schema_editor):
    StockTransferLine = apps.get_model("stock_management", "StockTransferLine")
    GRNItem = apps.get_model("suppliers", "GRNItem")

    def selling_price(item):
        issuing = item.issuing_price if item.issuing_price is not None else Decimal("0")
        if issuing > Decimal("0"):
            return issuing.quantize(Decimal("0.01"))
        purchase = item.unit_price if item.unit_price is not None else Decimal("0")
        return purchase.quantize(Decimal("0.01"))

    for line in StockTransferLine.objects.select_related("note").iterator():
        if line.unit_price and line.unit_price > Decimal("0"):
            continue
        branch_id = line.note.from_branch_id
        qs = GRNItem.objects.filter(
            grn__status="confirmed",
            grn__grn_type__in=["farmer_goods", "corporate"],
            product_id=line.product_id,
        ).select_related("grn")
        if branch_id:
            qs = qs.filter(Q(grn__branch_id=branch_id) | Q(grn__branch_id__isnull=True))
        item = qs.order_by("-grn__date", "-id").first()
        if not item:
            continue
        price = selling_price(item)
        if price > Decimal("0"):
            StockTransferLine.objects.filter(pk=line.pk).update(unit_price=price)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("stock_management", "0006_product_category_dynamic"),
        ("suppliers", "0014_farmergoodsissue_driver_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="stocktransferline",
            name="unit_price",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                help_text="Selling/unit price from the related GRN at the source branch.",
                max_digits=14,
            ),
        ),
        migrations.RunPython(backfill_transfer_line_unit_prices, noop_reverse),
    ]
