from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0024_collectionpoint_additional_farmers"),
    ]

    operations = [
        migrations.AddField(
            model_name="farmeradvancepayment",
            name="advance_type",
            field=models.CharField(
                choices=[
                    ("advance", "Advance Payment"),
                    ("bank_loan", "Bank Loan Deduction"),
                ],
                default="advance",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="collectionpointadvancepayment",
            name="advance_type",
            field=models.CharField(
                choices=[
                    ("advance", "Advance Payment"),
                    ("bank_loan", "Bank Loan Deduction"),
                ],
                default="advance",
                max_length=20,
            ),
        ),
    ]
