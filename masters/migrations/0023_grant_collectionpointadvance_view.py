from django.db import migrations


def grant_cp_advance_view(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")
    try:
        sheet_perm = Permission.objects.get(
            codename="view_collectionpointpaymentsheet",
            content_type__app_label="reports",
        )
        advance_perm = Permission.objects.get(
            codename="view_collectionpointadvancepayment",
            content_type__app_label="masters",
        )
    except Permission.DoesNotExist:
        return

    for group in Group.objects.filter(permissions=sheet_perm):
        group.permissions.add(advance_perm)
    for user in User.objects.filter(user_permissions=sheet_perm):
        user.user_permissions.add(advance_perm)


def revoke_cp_advance_view(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")
    try:
        advance_perm = Permission.objects.get(
            codename="view_collectionpointadvancepayment",
            content_type__app_label="masters",
        )
    except Permission.DoesNotExist:
        return
    for group in Group.objects.filter(permissions=advance_perm):
        group.permissions.remove(advance_perm)
    for user in User.objects.filter(user_permissions=advance_perm):
        user.user_permissions.remove(advance_perm)


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0022_collectionpointadvancepayment"),
        ("reports", "0019_collectionpoint_deduction_skip_advance"),
    ]

    operations = [
        migrations.RunPython(grant_cp_advance_view, revoke_cp_advance_view),
    ]
