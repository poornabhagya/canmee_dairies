from django.db import migrations


def grant_point_goods_summary_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    try:
        point_goods_perm = Permission.objects.get(
            codename="view_pointgoodssummary",
            content_type__app_label="reports",
        )
        point_sheet_perm = Permission.objects.get(
            codename="view_collectionpointpaymentsheet",
            content_type__app_label="reports",
        )
        goods_issue_perm = Permission.objects.get(
            codename="view_farmergoodsissue",
            content_type__app_label="suppliers",
        )
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(
        permissions=point_sheet_perm,
    ).filter(permissions=goods_issue_perm):
        group.permissions.add(point_goods_perm)
    for user in User.objects.filter(
        user_permissions=point_sheet_perm,
    ).filter(user_permissions=goods_issue_perm):
        user.user_permissions.add(point_goods_perm)


def revoke_point_goods_summary_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    try:
        point_goods_perm = Permission.objects.get(
            codename="view_pointgoodssummary",
            content_type__app_label="reports",
        )
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(permissions=point_goods_perm):
        group.permissions.remove(point_goods_perm)
    for user in User.objects.filter(user_permissions=point_goods_perm):
        user.user_permissions.remove(point_goods_perm)


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0015_collection_point_payment_snapshots"),
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
                    ("view_pointgoodssummary", "Can view point goods summary"),
                    (
                        "view_paymentsettlement",
                        "Can view payment settlement details",
                    ),
                ],
            },
        ),
        migrations.RunPython(
            grant_point_goods_summary_permissions,
            revoke_point_goods_summary_permissions,
        ),
    ]
