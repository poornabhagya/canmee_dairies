from django.db import migrations


def grant_tally_sheet_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    try:
        view_tally = Permission.objects.get(
            codename="view_milkcollectiontallysheet",
            content_type__app_label="collections",
        )
        change_tally = Permission.objects.get(
            codename="change_milkcollectiontallysheet",
            content_type__app_label="collections",
        )
        change_factor = Permission.objects.get(
            codename="change_milkcollectiontallyfactor",
            content_type__app_label="collections",
        )
        add_milk = Permission.objects.get(
            codename="add_milkcollection",
            content_type__app_label="collections",
        )
        add_factor = Permission.objects.get(
            codename="add_milkfactor",
            content_type__app_label="collections",
        )
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(permissions=add_milk):
        group.permissions.add(view_tally, change_tally)
    for user in User.objects.filter(user_permissions=add_milk):
        user.user_permissions.add(view_tally, change_tally)

    for group in Group.objects.filter(permissions=add_factor):
        group.permissions.add(change_factor)
    for user in User.objects.filter(user_permissions=add_factor):
        user.user_permissions.add(change_factor)


def revoke_tally_sheet_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    codenames = (
        "view_milkcollectiontallysheet",
        "change_milkcollectiontallysheet",
        "change_milkcollectiontallyfactor",
    )
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

    Permission.objects.filter(
        codename__in=codenames,
        content_type__app_label="collections",
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("collections", "0011_split_milk_reconcile_permissions"),
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
                        "change_milkcollectionreconcilechoice",
                        "Can set milk collection point reconcile choice",
                    ),
                    (
                        "sync_milkcollectionreconcile",
                        "Can sync milk collection point reconcile totals",
                    ),
                    (
                        "view_milkcollectiontallysheet",
                        "Can view collection point tally sheet",
                    ),
                    (
                        "change_milkcollectiontallysheet",
                        "Can save milk quantities on collection point tally sheet",
                    ),
                    (
                        "change_milkcollectiontallyfactor",
                        "Can save fat/LR on collection point tally sheet",
                    ),
                ],
            },
        ),
        migrations.RunPython(
            grant_tally_sheet_permissions,
            revoke_tally_sheet_permissions,
        ),
    ]
