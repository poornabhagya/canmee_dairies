from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0021_collectionpoint_additional"),
        ("branches", "0007_branch_details"),
        ("stock_management", "0007_stocktransferline_unit_price"),
        ("suppliers", "0014_farmergoodsissue_driver_status"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DriverFarmerGoodsRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("issued", "Issued"),
                            ("cancelled", "Cancelled"),
                        ],
                        db_index=True,
                        default="pending",
                        max_length=20,
                    ),
                ),
                ("note", models.CharField(blank=True, max_length=255)),
                ("requested_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("fulfilled_at", models.DateTimeField(blank=True, null=True)),
                ("issued_batch_ref", models.CharField(blank=True, db_index=True, max_length=36)),
                ("fulfill_note", models.CharField(blank=True, max_length=255)),
                (
                    "skipped_out_of_stock",
                    models.TextField(
                        blank=True,
                        help_text="Products skipped because stock was insufficient when fulfilling.",
                    ),
                ),
                (
                    "collection_point",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="farmer_goods_requests",
                        to="masters.collectionpoint",
                    ),
                ),
                (
                    "from_branch",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="driver_farmer_goods_requests",
                        to="branches.branch",
                    ),
                ),
                (
                    "fulfilled_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="fulfilled_driver_farmer_goods_requests",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "route",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="farmer_goods_requests",
                        to="masters.route",
                    ),
                ),
            ],
            options={
                "ordering": ["-requested_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="DriverFarmerGoodsRequestLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("quantity", models.DecimalField(decimal_places=2, max_digits=14)),
                ("issued_quantity", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                (
                    "product",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="driver_farmer_goods_request_lines",
                        to="stock_management.product",
                    ),
                ),
                (
                    "request",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="lines",
                        to="suppliers.driverfarmergoodsrequest",
                    ),
                ),
            ],
            options={
                "ordering": ["id"],
            },
        ),
    ]
