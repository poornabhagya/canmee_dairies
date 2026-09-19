from decimal import Decimal

from django.db import migrations, models


def backfill_ts(apps, schema_editor):
    MilkDistribution = apps.get_model("dispatch", "MilkDistribution")
    for row in MilkDistribution.objects.all().only("pk", "fat", "snf"):
        fat = row.fat or Decimal("0")
        snf = row.snf or Decimal("0")
        MilkDistribution.objects.filter(pk=row.pk).update(ts=(fat + snf).quantize(Decimal("0.01")))


class Migration(migrations.Migration):

    dependencies = [
        ("dispatch", "0016_buyer_rates_and_account"),
    ]

    operations = [
        migrations.AddField(
            model_name="milkdistribution",
            name="is_rate_manual",
            field=models.BooleanField(
                default=False,
                help_text="If true, unit_rate is kept as entered instead of the period rate.",
            ),
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="ts",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Total solids (FAT + SNF).",
                max_digits=6,
                verbose_name="TS",
            ),
        ),
        migrations.RunPython(backfill_ts, migrations.RunPython.noop),
    ]
