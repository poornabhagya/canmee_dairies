from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


def mark_existing_branch_dispatches_collected(apps, schema_editor):
    MilkDistribution = apps.get_model("dispatch", "MilkDistribution")
    MilkDistribution.objects.filter(
        destination_branch_id__isnull=False,
    ).exclude(status="cancelled").update(branch_destination_response="collected")


class Migration(migrations.Migration):

    dependencies = [
        ("branches", "0005_distribution_return_status"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("masters", "0001_initial"),
        ("dispatch", "0007_remove_dispatched_status"),
    ]

    operations = [
        migrations.CreateModel(
            name="DispatchNotification",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("incoming_dispatch", "Incoming dispatch"),
                            ("destination_responded", "Destination responded"),
                            ("return_requested", "Return requested"),
                            ("return_resolved", "Return resolved"),
                        ],
                        max_length=32,
                    ),
                ),
                ("title", models.CharField(max_length=160)),
                ("body", models.TextField()),
                ("action_url", models.CharField(blank=True, max_length=255)),
                ("is_read", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                (
                    "dispatch",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="notifications",
                        to="dispatch.milkdistribution",
                    ),
                ),
                (
                    "recipient",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="dispatch_notifications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="branch_destination_response",
            field=models.CharField(
                blank=True,
                choices=[
                    ("pending", "Pending"),
                    ("collected", "Collected"),
                    ("return_requested", "Return requested"),
                    ("diverted", "Diverted"),
                ],
                default="pending",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="branch_response_notes",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="branch_responded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="branch_return_quantity_kg",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="branch_source_return_response",
            field=models.CharField(
                blank=True,
                choices=[
                    ("pending", "Pending acceptance"),
                    ("accepted", "Accepted"),
                    ("rejected", "Rejected"),
                ],
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="divert_to_branch",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="diverted_incoming_dispatches",
                to="branches.branch",
            ),
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="divert_to_buyer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="diverted_incoming_dispatches",
                to="masters.buyer",
            ),
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="source_return_responded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="branch_responded_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="branch_dispatch_responses",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="milkdistribution",
            name="source_return_responded_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="branch_dispatch_return_responses",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RunPython(
            mark_existing_branch_dispatches_collected,
            migrations.RunPython.noop,
        ),
    ]
