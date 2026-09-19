import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("branches", "0005_distribution_return_status"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="BranchMilkStockAdjustment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField(default=django.utils.timezone.localdate)),
                (
                    "adjustment_type",
                    models.CharField(
                        choices=[("shortage", "Shortage"), ("excess", "Excess")],
                        max_length=20,
                    ),
                ),
                ("kg", models.DecimalField(decimal_places=2, max_digits=10)),
                ("liters", models.DecimalField(decimal_places=2, max_digits=10)),
                ("remarks", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "branch",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="milk_stock_adjustments",
                        to="branches.branch",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="branch_milk_stock_adjustments_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-date", "-id"],
            },
        ),
    ]
