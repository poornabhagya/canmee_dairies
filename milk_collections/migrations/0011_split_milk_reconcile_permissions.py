from django.db import migrations


def split_reconcile_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")

    try:
        old_change = Permission.objects.get(
            codename="change_milkcollectionreconcile",
            content_type__app_label="collections",
        )
        choice_perm = Permission.objects.get(
            codename="change_milkcollectionreconcilechoice",
            content_type__app_label="collections",
        )
        sync_perm = Permission.objects.get(
            codename="sync_milkcollectionreconcile",
            content_type__app_label="collections",
        )
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(permissions=old_change):
        group.permissions.add(choice_perm, sync_perm)
        group.permissions.remove(old_change)
    for user in User.objects.filter(user_permissions=old_change):
        user.user_permissions.add(choice_perm, sync_perm)
        user.user_permissions.remove(old_change)

    old_change.delete()


def unsplit_reconcile_permissions(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")
    ContentType = apps.get_model("contenttypes", "ContentType")

    ct = ContentType.objects.get(app_label="collections", model="milkcollection")
    old_change, _created = Permission.objects.get_or_create(
        codename="change_milkcollectionreconcile",
        content_type=ct,
        defaults={"name": "Can reconcile milk collection points"},
    )

    codenames = ("change_milkcollectionreconcilechoice", "sync_milkcollectionreconcile")
    new_perms = list(
        Permission.objects.filter(
            codename__in=codenames,
            content_type__app_label="collections",
        )
    )
    if not new_perms:
        return

    for group in Group.objects.filter(permissions__in=new_perms).distinct():
        group.permissions.add(old_change)
        group.permissions.remove(*new_perms)
    for user in User.objects.filter(user_permissions__in=new_perms).distinct():
        user.user_permissions.add(old_change)
        user.user_permissions.remove(*new_perms)

    Permission.objects.filter(
        codename__in=codenames,
        content_type__app_label="collections",
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("collections", "0010_milkcollection_reconcile_permissions"),
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
                ],
            },
        ),
        migrations.RunPython(
            split_reconcile_permissions,
            unsplit_reconcile_permissions,
        ),
    ]
