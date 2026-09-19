from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("suppliers", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="grn",
            name="discount_scope",
            field=models.CharField(
                choices=[
                    ("line", "Per product (free qty & line discount)"),
                    ("document", "On GRN total only"),
                ],
                default="line",
                help_text="Use per-line discounts, or a single discount on the GRN total (not both).",
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name="grn",
            name="document_discount_percent",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0"),
                help_text="When scope is “on total”, percent off the subtotal (use this or amount, not both).",
                max_digits=5,
            ),
        ),
        migrations.AddField(
            model_name="grn",
            name="document_discount_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0"),
                help_text="When scope is “on total”, flat amount off the subtotal.",
                max_digits=14,
            ),
        ),
        migrations.AddField(
            model_name="grnitem",
            name="free_quantity",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0"),
                help_text="Units received free; not charged at the purchase rate.",
                max_digits=14,
            ),
        ),
        migrations.AddField(
            model_name="grnitem",
            name="issuing_price",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0"),
                help_text="Issue/selling price per unit for downstream use (informational).",
                max_digits=14,
            ),
        ),
        migrations.AddField(
            model_name="grnitem",
            name="line_discount_amount",
            field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=14),
        ),
        migrations.AddField(
            model_name="grnitem",
            name="line_discount_percent",
            field=models.DecimalField(decimal_places=2, default=Decimal("0"), max_digits=5),
        ),
        migrations.AlterField(
            model_name="grnitem",
            name="quantity",
            field=models.DecimalField(
                decimal_places=2,
                help_text="Total physical quantity received (including free units).",
                max_digits=14,
            ),
        ),
        migrations.AlterField(
            model_name="grnitem",
            name="total_price",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0"),
                help_text="Line cost before any GRN-level discount (stored subtotal for the line).",
                max_digits=14,
            ),
        ),
        migrations.AlterField(
            model_name="grnitem",
            name="unit_price",
            field=models.DecimalField(
                decimal_places=2,
                help_text="Purchase rate per billable unit (after excluding free quantity).",
                max_digits=14,
            ),
        ),
    ]
