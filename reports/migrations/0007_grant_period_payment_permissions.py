from django.db import migrations


def grant_period_payment_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    try:
        farmer_sheet_perm = Permission.objects.get(
            codename="view_farmerpaymentsheet",
            content_type__app_label="reports",
        )
        point_sheet_perm = Permission.objects.get(
            codename="view_collectionpointpaymentsheet",
            content_type__app_label="reports",
        )
        farmer_pay_perm = Permission.objects.get(
            codename="add_farmerperiodpayment",
            content_type__app_label="reports",
        )
        point_pay_perm = Permission.objects.get(
            codename="add_collectionpointperiodpayment",
            content_type__app_label="reports",
        )
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(permissions=farmer_sheet_perm):
        group.permissions.add(farmer_pay_perm)
    for group in Group.objects.filter(permissions=point_sheet_perm):
        group.permissions.add(point_pay_perm)

    for user in User.objects.filter(user_permissions=farmer_sheet_perm):
        user.user_permissions.add(farmer_pay_perm)
    for user in User.objects.filter(user_permissions=point_sheet_perm):
        user.user_permissions.add(point_pay_perm)


def revoke_period_payment_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    codenames = ("add_farmerperiodpayment", "add_collectionpointperiodpayment")
    perms = list(
        Permission.objects.filter(
            codename__in=codenames,
            content_type__app_label="reports",
        )
    )
    if not perms:
        return

    for group in Group.objects.filter(permissions__in=perms).distinct():
        group.permissions.remove(*perms)
    for user in User.objects.filter(user_permissions__in=perms).distinct():
        user.user_permissions.remove(*perms)


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0006_period_payments"),
    ]

    operations = [
        migrations.RunPython(
            grant_period_payment_permissions,
            revoke_period_payment_permissions,
        ),
    ]
