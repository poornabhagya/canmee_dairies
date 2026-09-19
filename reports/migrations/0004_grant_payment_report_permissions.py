from django.db import migrations


def grant_payment_report_permissions(apps, schema_editor):
  Permission = apps.get_model("auth", "Permission")
  Group = apps.get_model("auth", "Group")
  User = apps.get_model("auth", "User")

  try:
    milk_perm = Permission.objects.get(
      codename="view_milkcollection",
      content_type__app_label="collections",
    )
    sheet_perm = Permission.objects.get(
      codename="view_farmerpaymentsheet",
      content_type__app_label="reports",
    )
    advance_perm = Permission.objects.get(
      codename="view_farmeradvancepayment",
      content_type__app_label="masters",
    )
  except Permission.DoesNotExist:
    return

  for group in Group.objects.filter(permissions=milk_perm):
    group.permissions.add(sheet_perm, advance_perm)

  for user in User.objects.filter(user_permissions=milk_perm):
    user.user_permissions.add(sheet_perm, advance_perm)


def revoke_payment_report_permissions(apps, schema_editor):
  Permission = apps.get_model("auth", "Permission")
  Group = apps.get_model("auth", "Group")
  User = apps.get_model("auth", "User")

  try:
    sheet_perm = Permission.objects.get(
      codename="view_farmerpaymentsheet",
      content_type__app_label="reports",
    )
    advance_perm = Permission.objects.get(
      codename="view_farmeradvancepayment",
      content_type__app_label="masters",
    )
  except Permission.DoesNotExist:
    return

  for group in Group.objects.filter(permissions=sheet_perm):
    group.permissions.remove(sheet_perm, advance_perm)

  for user in User.objects.filter(user_permissions=sheet_perm):
    user.user_permissions.remove(sheet_perm, advance_perm)


class Migration(migrations.Migration):

  dependencies = [
    ("reports", "0003_farmerpaymentsheet_permission"),
    ("masters", "0010_farmeradvancepayment"),
    ("collections", "0001_initial"),
  ]

  operations = [
    migrations.RunPython(
      grant_payment_report_permissions,
      revoke_payment_report_permissions,
    ),
  ]
