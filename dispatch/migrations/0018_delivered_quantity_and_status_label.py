from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dispatch", "0017_dispatch_manual_rate_and_ts"),
    ]

    operations = [
        migrations.AddField(
            model_name="milkdistribution",
            name="delivered_quantity",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Quantity received by the buyer when status is Delivered. Stored in kg.",
                max_digits=10,
                null=True,
                verbose_name="Delivered quantity (kg)",
            ),
        ),
        migrations.AlterField(
            model_name="milkdistribution",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("completed", "Delivered"),
                    ("return", "Return"),
                    ("cancelled", "Cancelled"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]
