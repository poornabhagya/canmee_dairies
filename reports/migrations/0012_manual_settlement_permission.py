from django.db import migrations


def grant_manual_settlement_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    try:
        manual_perm = Permission.objects.get(
            codename="record_manual_settlement",
            content_type__app_label="reports",
        )
        settlement_perm = Permission.objects.get(
            codename="view_paymentsettlement",
            content_type__app_label="reports",
        )
        pay_perm = Permission.objects.get(
            codename="add_farmerperiodpayment",
            content_type__app_label="reports",
        )
    except Permission.DoesNotExist:
        return

    source_perms = (settlement_perm, pay_perm)
    for group in Group.objects.filter(permissions__in=source_perms).distinct():
        group.permissions.add(manual_perm)
    for user in User.objects.filter(user_permissions__in=source_perms).distinct():
        user.user_permissions.add(manual_perm)


def revoke_manual_settlement_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    try:
        manual_perm = Permission.objects.get(
            codename="record_manual_settlement",
            content_type__app_label="reports",
        )
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(permissions=manual_perm):
        group.permissions.remove(manual_perm)
    for user in User.objects.filter(user_permissions=manual_perm):
        user.user_permissions.remove(manual_perm)


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0011_grant_deduction_skip_permissions"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="farmerperiodpayment",
            options={
                "ordering": ["-paid_at", "-id"],
                "permissions": [
                    (
                        "record_manual_settlement",
                        "Can record manual advance/goods/loan settlement",
                    ),
                ],
            },
        ),
        migrations.RunPython(
            grant_manual_settlement_permissions,
            revoke_manual_settlement_permissions,
        ),
    ]
