import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0010_farmeradvancepayment"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("reports", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="FarmerPaymentLoanDeductionSetting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                (
                    "deduct_loan",
                    models.BooleanField(
                        default=True,
                        help_text="When unchecked, loan instalments are calculated but not deducted from this payment.",
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "farmer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="payment_loan_deduction_settings",
                        to="masters.farmer",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="farmer_payment_loan_deduction_settings",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-period_end", "-period_start", "farmer_id"],
            },
        ),
        migrations.AddConstraint(
            model_name="farmerpaymentloandeductionsetting",
            constraint=models.UniqueConstraint(
                fields=("farmer", "period_start", "period_end"),
                name="uniq_farmer_payment_loan_deduction_period",
            ),
        ),
    ]
