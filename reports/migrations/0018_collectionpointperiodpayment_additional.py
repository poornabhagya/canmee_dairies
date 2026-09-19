from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0017_collection_point_loan_payment_deductions"),
        ("masters", "0021_collectionpoint_additional"),
    ]

    operations = [
        migrations.AddField(
            model_name="collectionpointperiodpayment",
            name="additional_rate",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Additional per unit when this payment was recorded.",
                max_digits=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="collectionpointperiodpayment",
            name="additional_amount",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                max_digits=14,
                null=True,
            ),
        ),
    ]
