from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0016_payment_settlement_flags"),
    ]

    operations = [
        migrations.AddField(
            model_name="farmeradvancepayment",
            name="recovery_note",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
