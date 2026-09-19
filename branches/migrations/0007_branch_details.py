from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("hrm", "0001_initial"),
        ("branches", "0006_branchmilkstockadjustment"),
    ]

    operations = [
        migrations.AddField(
            model_name="branch",
            name="maximum_milk_storage",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Maximum milk storage capacity for this branch (kg).",
                max_digits=14,
                null=True,
                verbose_name="Maximum milk storage",
            ),
        ),
        migrations.AddField(
            model_name="branch",
            name="branch_manager",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="managed_branches",
                to="hrm.employee",
                verbose_name="Branch manager",
            ),
        ),
        migrations.AddField(
            model_name="branch",
            name="manager_contact",
            field=models.CharField(blank=True, max_length=50, verbose_name="Manager contact"),
        ),
        migrations.AddField(
            model_name="branch",
            name="branch_contact",
            field=models.CharField(blank=True, max_length=50, verbose_name="Branch contact"),
        ),
        migrations.AddField(
            model_name="branch",
            name="location",
            field=models.CharField(blank=True, max_length=255, verbose_name="Location"),
        ),
    ]
