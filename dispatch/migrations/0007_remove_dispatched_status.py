from django.db import migrations, models


def migrate_dispatched_to_completed(apps, schema_editor):
    MilkDistribution = apps.get_model("dispatch", "MilkDistribution")
    MilkDistribution.objects.filter(status="dispatched").update(status="completed")


class Migration(migrations.Migration):

    dependencies = [
        ("dispatch", "0006_branch_to_branch_dispatch"),
    ]

    operations = [
        migrations.RunPython(migrate_dispatched_to_completed, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="milkdistribution",
            name="status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("completed", "Completed"),
                    ("return", "Return"),
                    ("cancelled", "Cancelled"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
    ]
