from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("farmer_loans", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="farmerloan",
            old_name="repayment_period_days",
            new_name="repayment_period_months",
        ),
        migrations.AlterField(
            model_name="farmerloan",
            name="repayment_period_months",
            field=models.PositiveIntegerField(help_text="Total repayment period in months."),
        ),
    ]
