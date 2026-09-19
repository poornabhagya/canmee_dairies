from django.db import migrations


def grant_farmer_rate_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    try:
        source_perm = Permission.objects.get(
            codename="change_farmer",
            content_type__app_label="masters",
        )
        rate_perm = Permission.objects.get(
            codename="change_farmerrate",
            content_type__app_label="masters",
        )
        bulk_perm = Permission.objects.get(
            codename="bulk_update_farmerrate",
            content_type__app_label="masters",
        )
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(permissions=source_perm):
        group.permissions.add(rate_perm, bulk_perm)

    for user in User.objects.filter(user_permissions=source_perm):
        user.user_permissions.add(rate_perm, bulk_perm)


def revoke_farmer_rate_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    try:
        rate_perm = Permission.objects.get(
            codename="change_farmerrate",
            content_type__app_label="masters",
        )
        bulk_perm = Permission.objects.get(
            codename="bulk_update_farmerrate",
            content_type__app_label="masters",
        )
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(permissions=rate_perm):
        group.permissions.remove(rate_perm, bulk_perm)

    for user in User.objects.filter(user_permissions=rate_perm):
        user.user_permissions.remove(rate_perm, bulk_perm)


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0011_separate_feature_permissions"),
    ]

    operations = [
        migrations.RunPython(
            grant_farmer_rate_permissions,
            revoke_farmer_rate_permissions,
        ),
    ]
