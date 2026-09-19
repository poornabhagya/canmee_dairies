from django.db import migrations


def grant_raw_milk_bulk_permission(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    try:
        source_perm = Permission.objects.get(
            codename="view_rawmilksuppliercollection",
            content_type__app_label="suppliers",
        )
        bulk_perm = Permission.objects.get(
            codename="view_rawmilkbulkcollection",
            content_type__app_label="suppliers",
        )
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(permissions=source_perm):
        group.permissions.add(bulk_perm)

    for user in User.objects.filter(user_permissions=source_perm):
        user.user_permissions.add(bulk_perm)


def revoke_raw_milk_bulk_permission(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    try:
        bulk_perm = Permission.objects.get(
            codename="view_rawmilkbulkcollection",
            content_type__app_label="suppliers",
        )
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(permissions=bulk_perm):
        group.permissions.remove(bulk_perm)

    for user in User.objects.filter(user_permissions=bulk_perm):
        user.user_permissions.remove(bulk_perm)


class Migration(migrations.Migration):

    dependencies = [
        ("suppliers", "0009_separate_feature_permissions"),
    ]

    operations = [
        migrations.RunPython(
            grant_raw_milk_bulk_permission,
            revoke_raw_milk_bulk_permission,
        ),
    ]
