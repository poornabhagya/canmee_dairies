from django.db import migrations


def backfill_superuser_dispatch_notifications(apps, schema_editor):
    User = apps.get_model("auth", "User")
    DispatchNotification = apps.get_model("dispatch", "DispatchNotification")
    MilkDistribution = apps.get_model("dispatch", "MilkDistribution")

    superusers = list(User.objects.filter(is_active=True, is_superuser=True))
    if not superusers:
        return

    dispatches = MilkDistribution.objects.filter(
        destination_branch_id__isnull=False,
        branch_destination_response="pending",
    ).select_related("branch", "destination_branch")

    for dispatch in dispatches.iterator():
        title = f"Incoming dispatch #{dispatch.dispatch_no}"
        from_name = dispatch.branch.name if dispatch.branch_id else "—"
        to_name = (
            dispatch.destination_branch.name if dispatch.destination_branch_id else "—"
        )
        body = (
            f"{dispatch.kg} kg from {from_name} is on the way to {to_name}. "
            "Please collect, return, or divert."
        )
        action_url = f"/dispatch/{dispatch.pk}/branch-respond/"
        for user in superusers:
            exists = DispatchNotification.objects.filter(
                recipient_id=user.pk,
                dispatch_id=dispatch.pk,
                kind="incoming_dispatch",
            ).exists()
            if exists:
                continue
            DispatchNotification.objects.create(
                recipient_id=user.pk,
                dispatch_id=dispatch.pk,
                kind="incoming_dispatch",
                title=title,
                body=body,
                action_url=action_url,
            )


class Migration(migrations.Migration):

    dependencies = [
        ("dispatch", "0010_refresh_dispatch_notification_urls"),
    ]

    operations = [
        migrations.RunPython(
            backfill_superuser_dispatch_notifications,
            migrations.RunPython.noop,
        ),
    ]
