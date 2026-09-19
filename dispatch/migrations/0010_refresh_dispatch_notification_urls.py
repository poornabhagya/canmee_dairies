from django.db import migrations


def refresh_dispatch_notification_urls(apps, schema_editor):
    DispatchNotification = apps.get_model("dispatch", "DispatchNotification")
    for note in DispatchNotification.objects.filter(dispatch_id__isnull=False).iterator():
        dispatch_id = note.dispatch_id
        if note.kind == "incoming_dispatch":
            note.action_url = f"/dispatch/{dispatch_id}/branch-respond/"
        elif note.kind == "return_requested":
            note.action_url = f"/dispatch/{dispatch_id}/branch-return/"
        elif note.kind in ("destination_responded", "return_resolved"):
            note.action_url = "/dispatch/?filter_tab=branch&branch_scope=incoming"
        if note.action_url:
            note.save(update_fields=["action_url"])


class Migration(migrations.Migration):

    dependencies = [
        ("dispatch", "0009_branch_dispatch_respond_permission"),
    ]

    operations = [
        migrations.RunPython(refresh_dispatch_notification_urls, migrations.RunPython.noop),
    ]
