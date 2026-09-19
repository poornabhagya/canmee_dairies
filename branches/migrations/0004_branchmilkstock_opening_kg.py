from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("branches", "0003_branch_collection_unit"),
    ]

    operations = [
        migrations.AddField(
            model_name="branchmilkstock",
            name="opening_kg",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=14),
        ),
    ]
