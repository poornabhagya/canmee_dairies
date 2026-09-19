from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("farmer_loans", "0009_farmerloanrepaymentschedule_payment_note"),
    ]

    operations = [
        migrations.AddField(
            model_name="farmerloan",
            name="installment_method",
            field=models.CharField(
                choices=[
                    ("semi_monthly", "15th & last day (about 2 per month)"),
                    ("monthly", "Monthly instalments"),
                ],
                default="semi_monthly",
                help_text="How instalment due dates are scheduled after the first repayment date.",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="farmerloan",
            name="installment_count",
            field=models.PositiveIntegerField(help_text="Total number of instalments."),
        ),
    ]
