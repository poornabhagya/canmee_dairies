from django.db import migrations


def grant_collection_point_payment_sheet_permission(apps, schema_editor):
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
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(permissions=farmer_sheet_perm):
        group.permissions.add(point_sheet_perm)

    for user in User.objects.filter(user_permissions=farmer_sheet_perm):
        user.user_permissions.add(point_sheet_perm)


def revoke_collection_point_payment_sheet_permission(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    try:
        point_sheet_perm = Permission.objects.get(
            codename="view_collectionpointpaymentsheet",
            content_type__app_label="reports",
        )
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(permissions=point_sheet_perm):
        group.permissions.remove(point_sheet_perm)

    for user in User.objects.filter(user_permissions=point_sheet_perm):
        user.user_permissions.remove(point_sheet_perm)


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0004_grant_payment_report_permissions"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="farmerpaymentloandeductionsetting",
            options={
                "ordering": ["-period_end", "-period_start", "farmer_id"],
                "permissions": [
                    ("view_farmerpaymentsheet", "Can view farmer payment sheet"),
                    (
                        "view_collectionpointpaymentsheet",
                        "Can view collection point payment sheet",
                    ),
                ],
            },
        ),
        migrations.RunPython(
            grant_collection_point_payment_sheet_permission,
            revoke_collection_point_payment_sheet_permission,
        ),
    ]
