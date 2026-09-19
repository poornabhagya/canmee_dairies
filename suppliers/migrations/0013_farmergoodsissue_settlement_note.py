from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("suppliers", "0012_payment_settlement_flags"),
    ]

    operations = [
        migrations.AddField(
            model_name="farmergoodsissue",
            name="settlement_note",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
