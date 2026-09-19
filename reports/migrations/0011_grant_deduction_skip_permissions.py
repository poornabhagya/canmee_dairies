from django.db import migrations


def grant_deduction_skip_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    try:
        pay_perm = Permission.objects.get(
            codename="add_farmerperiodpayment",
            content_type__app_label="reports",
        )
        skip_perm = Permission.objects.get(
            codename="change_farmerpaymentperioddeductionskip",
            content_type__app_label="reports",
        )
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(permissions=pay_perm):
        group.permissions.add(skip_perm)
    for user in User.objects.filter(user_permissions=pay_perm):
        user.user_permissions.add(skip_perm)


def revoke_deduction_skip_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    try:
        skip_perm = Permission.objects.get(
            codename="change_farmerpaymentperioddeductionskip",
            content_type__app_label="reports",
        )
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(permissions=skip_perm):
        group.permissions.remove(skip_perm)
    for user in User.objects.filter(user_permissions=skip_perm):
        user.user_permissions.remove(skip_perm)


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0010_farmerpaymentperioddeductionskip"),
    ]

    operations = [
        migrations.RunPython(
            grant_deduction_skip_permissions,
            revoke_deduction_skip_permissions,
        ),
    ]
