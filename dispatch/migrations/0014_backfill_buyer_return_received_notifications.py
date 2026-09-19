from decimal import Decimal

from django.db import migrations


def backfill_buyer_return_received_notifications(apps, schema_editor):
    MilkDistribution = apps.get_model("dispatch", "MilkDistribution")
    DispatchNotification = apps.get_model("dispatch", "DispatchNotification")
    User = apps.get_model("auth", "User")
    Branch = apps.get_model("branches", "Branch")

    try:
        from canmee_dairies.constants import MILK_LITER_FACTOR
    except ImportError:
        MILK_LITER_FACTOR = Decimal("1.03")

    for dispatch in MilkDistribution.objects.filter(
        buyer_id__isnull=False,
        status="return",
        returned_branch_id__isnull=False,
    ).iterator():
        if DispatchNotification.objects.filter(
            dispatch_id=dispatch.pk,
            kind="return_received",
        ).exists():
            continue
        qty_kg = (dispatch.buyer_result_quantity or dispatch.kg or Decimal("0")).quantize(
            Decimal("0.01")
        )
        if qty_kg <= 0:
            continue
        branch = Branch.objects.filter(pk=dispatch.returned_branch_id).first()
        if not branch:
            continue
        liters = (qty_kg * MILK_LITER_FACTOR).quantize(Decimal("0.01"))
        title = f"Return received for dispatch #{dispatch.dispatch_no}"
        body = (
            f"{liters} L ({qty_kg} kg) credited to {branch.name} "
            f"from buyer dispatch #{dispatch.dispatch_no}."
        )
        action_url = (
            f"/dispatch/?filter_tab=buyer&branches={dispatch.returned_branch_id}"
        )
        branch_users = branch.users.filter(is_active=True)
        superusers = User.objects.filter(is_active=True, is_superuser=True)
        recipients = (branch_users | superusers).distinct()
        rows = [
            DispatchNotification(
                recipient=user,
                dispatch_id=dispatch.pk,
                kind="return_received",
                title=title,
                body=body,
                action_url=action_url,
            )
            for user in recipients
        ]
        if rows:
            DispatchNotification.objects.bulk_create(rows)


class Migration(migrations.Migration):

    dependencies = [
        ("dispatch", "0013_milkdistribution_diverted_by_branch"),
    ]

    operations = [
        migrations.RunPython(
            backfill_buyer_return_received_notifications,
            migrations.RunPython.noop,
        ),
    ]
