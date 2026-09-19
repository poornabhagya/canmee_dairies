from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0019_route_manual_vehicle_driver"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="farmer",
            name="resignation_note",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="farmer",
            name="resigned_at",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="farmer",
            name="resigned_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="farmer_resignations_recorded",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="farmer",
            name="status",
            field=models.CharField(
                choices=[("active", "Active"), ("resigned", "Resigned")],
                default="active",
                max_length=10,
            ),
        ),
    ]
