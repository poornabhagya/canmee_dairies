import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0002_farmer_contact_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="farmer",
            name="route",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="farmers",
                to="masters.route",
            ),
        ),
    ]
