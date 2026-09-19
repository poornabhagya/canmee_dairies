from django.db import migrations


def grant_supplier_rate_permission(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    try:
        source_perm = Permission.objects.get(
            codename="change_supplier",
            content_type__app_label="suppliers",
        )
        rate_perm = Permission.objects.get(
            codename="change_supplierrate",
            content_type__app_label="suppliers",
        )
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(permissions=source_perm):
        group.permissions.add(rate_perm)

    for user in User.objects.filter(user_permissions=source_perm):
        user.user_permissions.add(rate_perm)


def revoke_supplier_rate_permission(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    try:
        rate_perm = Permission.objects.get(
            codename="change_supplierrate",
            content_type__app_label="suppliers",
        )
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(permissions=rate_perm):
        group.permissions.remove(rate_perm)

    for user in User.objects.filter(user_permissions=rate_perm):
        user.user_permissions.remove(rate_perm)


class Migration(migrations.Migration):

    dependencies = [
        ("suppliers", "0017_raw_milk_supplier_rates_and_payments"),
    ]

    operations = [
        migrations.RunPython(grant_supplier_rate_permission, revoke_supplier_rate_permission),
    ]
