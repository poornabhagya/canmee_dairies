from django.db import migrations


def grant_milk_reconcile_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    try:
        view_reconcile = Permission.objects.get(
            codename="view_milkcollectionreconcile",
            content_type__app_label="collections",
        )
        change_reconcile = Permission.objects.get(
            codename="change_milkcollectionreconcile",
            content_type__app_label="collections",
        )
        view_milk = Permission.objects.get(
            codename="view_milkcollection",
            content_type__app_label="collections",
        )
        change_milk = Permission.objects.get(
            codename="change_milkcollection",
            content_type__app_label="collections",
        )
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(permissions=view_milk):
        group.permissions.add(view_reconcile)
    for user in User.objects.filter(user_permissions=view_milk):
        user.user_permissions.add(view_reconcile)

    for group in Group.objects.filter(permissions=change_milk):
        group.permissions.add(view_reconcile, change_reconcile)
    for user in User.objects.filter(user_permissions=change_milk):
        user.user_permissions.add(view_reconcile, change_reconcile)


def revoke_milk_reconcile_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    codenames = ("view_milkcollectionreconcile", "change_milkcollectionreconcile")
    perms = list(
        Permission.objects.filter(
            codename__in=codenames,
            content_type__app_label="collections",
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
        ("collections", "0009_collectionpointmilkreconcilechoice"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="milkcollection",
            options={
                "ordering": [
                    "-date",
                    "route__code",
                    "collection_point__number",
                    "farmer__full_name",
                ],
                "permissions": [
                    (
                        "view_milkcollectionpaymentstatus",
                        "Can view milk collection payment status",
                    ),
                    (
                        "view_milkcollectionreconcile",
                        "Can view milk collection point reconcile",
                    ),
                    (
                        "change_milkcollectionreconcile",
                        "Can reconcile milk collection points",
                    ),
                ],
            },
        ),
        migrations.RunPython(
            grant_milk_reconcile_permissions,
            revoke_milk_reconcile_permissions,
        ),
    ]
