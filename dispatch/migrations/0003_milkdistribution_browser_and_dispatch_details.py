from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dispatch", "0002_remove_milkdistribution_route_collection_point"),
    ]

    operations = [
        migrations.RenameField(
            model_name="milkdistribution",
            old_name="lorry_no",
            new_name="browser_number",
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="temperature",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=5,
                null=True,
                verbose_name="Temperature (°C)",
            ),
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="kq",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=6),
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="sealing_numbers",
            field=models.CharField(blank=True, max_length=255, verbose_name="Sealing numbers"),
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="in_time",
            field=models.TimeField(blank=True, null=True, verbose_name="In time"),
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="out_time",
            field=models.TimeField(blank=True, null=True, verbose_name="Out time"),
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="remarks",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("dispatched", "Dispatched"),
                    ("completed", "Completed"),
                    ("cancelled", "Cancelled"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="buyer_result_quantity",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=10,
                null=True,
                verbose_name="Buyer result (quantity)",
            ),
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="payment_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("partial", "Partial"),
                    ("paid", "Paid"),
                ],
                default="pending",
                max_length=20,
                verbose_name="Payment status",
            ),
        ),
    ]
