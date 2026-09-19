# Generated manually for route driver access

import secrets
import string

from django.db import migrations, models
import django.db.models.deletion

_ACCESS_ALPHABET = string.ascii_uppercase + string.digits


def _generate_code(existing):
    for _ in range(200):
        code = "".join(secrets.choice(_ACCESS_ALPHABET) for _ in range(8))
        if code not in existing:
            existing.add(code)
            return code
    raise RuntimeError("Could not allocate driver access codes.")


def assign_driver_access_codes(apps, schema_editor):
    Route = apps.get_model("masters", "Route")
    existing = set(
        Route.objects.exclude(driver_access_code__isnull=True)
        .exclude(driver_access_code="")
        .values_list("driver_access_code", flat=True)
    )
    for route in Route.objects.filter(driver_access_code__isnull=True):
        route.driver_access_code = _generate_code(existing)
        route.save(update_fields=["driver_access_code"])
    for route in Route.objects.filter(driver_access_code=""):
        route.driver_access_code = _generate_code(existing)
        route.save(update_fields=["driver_access_code"])


class Migration(migrations.Migration):

    dependencies = [
        ("hrm", "0001_initial"),
        ("masters", "0017_farmeradvancepayment_recovery_note"),
    ]

    operations = [
        migrations.CreateModel(
            name="Vehicle",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("registration_number", models.CharField(max_length=40, unique=True)),
                ("name", models.CharField(blank=True, max_length=120)),
            ],
            options={
                "ordering": ["registration_number"],
            },
        ),
        migrations.AddField(
            model_name="route",
            name="driver",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="assigned_routes",
                to="hrm.employee",
            ),
        ),
        migrations.AddField(
            model_name="route",
            name="driver_access_code",
            field=models.CharField(
                blank=True,
                help_text="Alphanumeric code for driver portal login.",
                max_length=32,
                null=True,
                unique=True,
            ),
        ),
        migrations.AddField(
            model_name="route",
            name="driver_can_view_farmer_goods",
            field=models.BooleanField(
                default=True,
                help_text="Driver may view issued farmer goods for this route.",
            ),
        ),
        migrations.AddField(
            model_name="route",
            name="driver_can_view_point_summary",
            field=models.BooleanField(
                default=True,
                help_text="Driver may view point milk collection summary.",
            ),
        ),
        migrations.AddField(
            model_name="route",
            name="driver_can_view_route_summary",
            field=models.BooleanField(
                default=True,
                help_text="Driver may view route milk collection summary.",
            ),
        ),
        migrations.AddField(
            model_name="route",
            name="vehicle",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="routes",
                to="masters.vehicle",
            ),
        ),
        migrations.RunPython(assign_driver_access_codes, migrations.RunPython.noop),
    ]
