from django.db import migrations, models


def copy_vehicle_driver_labels(apps, schema_editor):
    Route = apps.get_model("masters", "Route")
    Vehicle = apps.get_model("masters", "Vehicle")
    Employee = apps.get_model("hrm", "Employee")

    vehicle_map = {}
    for vehicle in Vehicle.objects.all():
        name = (vehicle.name or "").strip()
        if name:
            vehicle_map[vehicle.pk] = f"{vehicle.registration_number} — {name}"
        else:
            vehicle_map[vehicle.pk] = vehicle.registration_number
    employee_map = {}
    for employee in Employee.objects.all():
        parts = [employee.first_name, employee.middle_name, employee.last_name]
        employee_map[employee.pk] = " ".join(
            p.strip() for p in parts if p and str(p).strip()
        ) or str(employee)

    for route in Route.objects.all().iterator():
        updates = {}
        if route.vehicle_id and not (route.vehicle_name or "").strip():
            updates["vehicle_name"] = vehicle_map.get(route.vehicle_id, "")
        if route.driver_id and not (route.driver_name or "").strip():
            updates["driver_name"] = employee_map.get(route.driver_id, "")
        if updates:
            Route.objects.filter(pk=route.pk).update(**updates)


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0018_route_driver_access"),
    ]

    operations = [
        migrations.AddField(
            model_name="route",
            name="driver_name",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.AddField(
            model_name="route",
            name="vehicle_name",
            field=models.CharField(blank=True, max_length=120),
        ),
        migrations.RunPython(copy_vehicle_driver_labels, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="route",
            name="driver",
        ),
        migrations.RemoveField(
            model_name="route",
            name="vehicle",
        ),
        migrations.DeleteModel(
            name="Vehicle",
        ),
    ]
