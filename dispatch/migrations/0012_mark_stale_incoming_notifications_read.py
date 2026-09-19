from django.db import migrations


def mark_stale_incoming_notifications_read(apps, schema_editor):
    DispatchNotification = apps.get_model("dispatch", "DispatchNotification")
    MilkDistribution = apps.get_model("dispatch", "MilkDistribution")

    stale_dispatch_ids = MilkDistribution.objects.exclude(
        branch_destination_response="pending"
    ).values_list("pk", flat=True)

    DispatchNotification.objects.filter(
        kind="incoming_dispatch",
        dispatch_id__in=stale_dispatch_ids,
        is_read=False,
    ).update(is_read=True)


class Migration(migrations.Migration):

    dependencies = [
        ("dispatch", "0011_superuser_dispatch_notifications"),
    ]

    operations = [
        migrations.RunPython(
            mark_stale_incoming_notifications_read,
            migrations.RunPython.noop,
        ),
    ]
