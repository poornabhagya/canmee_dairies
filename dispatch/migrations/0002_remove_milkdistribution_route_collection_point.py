from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("dispatch", "0001_initial"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="milkdistribution",
            name="route",
        ),
        migrations.RemoveField(
            model_name="milkdistribution",
            name="collection_point",
        ),
    ]
