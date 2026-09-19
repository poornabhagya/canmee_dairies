from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("farmer_loans", "0008_farmerloan_existing_loan_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="farmerloanrepaymentschedule",
            name="payment_note",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
