from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("farmer_loans", "0006_farmerloanactionlog"),
    ]

    operations = [
        migrations.AddField(
            model_name="farmerloan",
            name="requested_installment_amount",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="When set, each instalment uses this amount except the last (remainder).",
                max_digits=14,
                null=True,
            ),
        ),
    ]
