from django.db import migrations


def grant_buyer_rate_permission(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    try:
        source_perm = Permission.objects.get(
            codename="change_buyer",
            content_type__app_label="masters",
        )
        rate_perm = Permission.objects.get(
            codename="change_buyerrate",
            content_type__app_label="masters",
        )
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(permissions=source_perm):
        group.permissions.add(rate_perm)

    for user in User.objects.filter(user_permissions=source_perm):
        user.user_permissions.add(rate_perm)


def revoke_buyer_rate_permission(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    try:
        rate_perm = Permission.objects.get(
            codename="change_buyerrate",
            content_type__app_label="masters",
        )
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(permissions=rate_perm):
        group.permissions.remove(rate_perm)

    for user in User.objects.filter(user_permissions=rate_perm):
        user.user_permissions.remove(rate_perm)


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0026_buyer_rates_and_account"),
    ]

    operations = [
        migrations.RunPython(grant_buyer_rate_permission, revoke_buyer_rate_permission),
    ]
