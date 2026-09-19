from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0025_farmer_bank_accounts_and_payment_method"),
    ]

    operations = [
        migrations.AlterField(
            model_name="farmerperiodpayment",
            name="paid_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
        migrations.AlterField(
            model_name="collectionpointperiodpayment",
            name="paid_at",
            field=models.DateTimeField(default=django.utils.timezone.now),
        ),
    ]
