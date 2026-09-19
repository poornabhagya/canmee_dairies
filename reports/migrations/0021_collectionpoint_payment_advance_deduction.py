import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0031_collectionpointadvance_partial_recovery"),
        ("reports", "0020_collectionpoint_previous_outstanding"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CollectionPointPaymentAdvanceDeduction",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                ("deduct_amount", models.DecimalField(decimal_places=2, max_digits=14)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "advance",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="period_deductions",
                        to="masters.collectionpointadvancepayment",
                    ),
                ),
                (
                    "collection_point",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="payment_advance_deductions",
                        to="masters.collectionpoint",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="collection_point_payment_advance_deductions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-period_end", "-period_start", "collection_point_id", "-id"],
            },
        ),
        migrations.AddConstraint(
            model_name="collectionpointpaymentadvancededuction",
            constraint=models.UniqueConstraint(
                fields=["collection_point", "period_start", "period_end", "advance"],
                name="uniq_cp_payment_advance_deduction",
            ),
        ),
    ]
