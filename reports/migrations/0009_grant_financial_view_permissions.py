from django.db import migrations


def grant_financial_view_permissions(apps, schema_editor):
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
        settlement_perm = Permission.objects.get(
            codename="view_paymentsettlement",
            content_type__app_label="reports",
        )
        milk_status_perm = Permission.objects.get(
            codename="view_milkcollectionpaymentstatus",
            content_type__app_label="collections",
        )
    except Permission.DoesNotExist:
        return

    sheet_perms = (farmer_sheet_perm, point_sheet_perm)
    for group in Group.objects.filter(permissions__in=sheet_perms).distinct():
        group.permissions.add(settlement_perm, milk_status_perm)
    for user in User.objects.filter(user_permissions__in=sheet_perms).distinct():
        user.user_permissions.add(settlement_perm, milk_status_perm)


def revoke_financial_view_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    codenames = ("view_paymentsettlement", "view_milkcollectionpaymentstatus")
    perms = list(
        Permission.objects.filter(
            codename__in=codenames,
            content_type__app_label__in=("reports", "collections"),
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
        ("reports", "0008_payment_settlement_permission"),
        ("collections", "0008_milkcollection_payment_status_permission"),
    ]

    operations = [
        migrations.RunPython(
            grant_financial_view_permissions,
            revoke_financial_view_permissions,
        ),
    ]
