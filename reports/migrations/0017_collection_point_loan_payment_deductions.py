from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("reports", "0016_point_goods_summary_permission"),
    ]

    operations = [
        migrations.CreateModel(
            name="CollectionPointPaymentLoanDeductionSetting",
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
                (
                    "deduct_loan",
                    models.BooleanField(
                        default=True,
                        help_text="When unchecked, collection point loan instalments are calculated but not deducted.",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "collection_point",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="payment_loan_deduction_settings",
                        to="masters.collectionpoint",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="collection_point_payment_loan_deduction_settings",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-period_end", "-period_start", "collection_point_id"],
            },
        ),
        migrations.AddConstraint(
            model_name="collectionpointpaymentloandeductionsetting",
            constraint=models.UniqueConstraint(
                fields=("collection_point", "period_start", "period_end"),
                name="uniq_cp_payment_loan_deduction_period",
            ),
        ),
        migrations.AlterField(
            model_name="collectionpointpaymentperioddeductionskip",
            name="kind",
            field=models.CharField(
                choices=[
                    ("goods_line", "Goods line"),
                    ("goods_receipt", "Goods receipt"),
                    ("loan_instalment", "Loan instalment"),
                ],
                max_length=20,
            ),
        ),
    ]
