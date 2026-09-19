from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dispatch", "0019_change_dispatch_billing_rate_permission"),
    ]

    operations = [
        migrations.AddField(
            model_name="milkdistribution",
            name="delivered_ts",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Total solids measured at the buyer for delivered milk.",
                max_digits=6,
                null=True,
                verbose_name="Delivered TS",
            ),
        ),
    ]
