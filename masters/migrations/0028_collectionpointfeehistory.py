# Generated manually for CollectionPoint fee audit history

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


def seed_collection_point_fee_history(apps, schema_editor):
    CollectionPoint = apps.get_model("masters", "CollectionPoint")
    CollectionPointFeeHistory = apps.get_model("masters", "CollectionPointFeeHistory")
    now = django.utils.timezone.now()
    rows = []
    for point in CollectionPoint.objects.all().only("id", "collector_fee", "additional"):
        rows.append(
            CollectionPointFeeHistory(
                collection_point_id=point.id,
                collector_fee=point.collector_fee,
                additional=point.additional,
                effective_at=now,
            )
        )
    if rows:
        CollectionPointFeeHistory.objects.bulk_create(rows, batch_size=1000)


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("masters", "0027_grant_buyer_rate_permission"),
    ]

    operations = [
        migrations.CreateModel(
            name="CollectionPointFeeHistory",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("collector_fee", models.DecimalField(decimal_places=2, max_digits=10)),
                ("additional", models.DecimalField(decimal_places=2, max_digits=10)),
                ("effective_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                (
                    "changed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="collection_point_fee_changes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "collection_point",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="fee_history",
                        to="masters.collectionpoint",
                    ),
                ),
            ],
            options={
                "ordering": ["-effective_at", "-id"],
            },
        ),
        migrations.RunPython(seed_collection_point_fee_history, migrations.RunPython.noop),
    ]
