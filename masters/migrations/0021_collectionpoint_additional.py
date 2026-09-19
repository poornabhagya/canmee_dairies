from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0020_farmer_status_resignation"),
    ]

    operations = [
        migrations.AddField(
            model_name="collectionpoint",
            name="additional",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                max_digits=10,
                verbose_name="Additional",
            ),
        ),
    ]
