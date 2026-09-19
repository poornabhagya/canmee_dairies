import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0016_payment_settlement_flags"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("collections", "0008_milkcollection_payment_status_permission"),
    ]

    operations = [
        migrations.CreateModel(
            name="CollectionPointMilkReconcileChoice",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("date", models.DateField()),
                (
                    "source",
                    models.CharField(
                        choices=[("point", "Point"), ("farmer", "Farmer")],
                        default="point",
                        max_length=10,
                    ),
                ),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "collection_point",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="milk_reconcile_choices",
                        to="masters.collectionpoint",
                    ),
                ),
                (
                    "route",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="milk_reconcile_choices",
                        to="masters.route",
                    ),
                ),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="collection_point_milk_reconcile_choices",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-date", "collection_point_id"],
            },
        ),
        migrations.AddConstraint(
            model_name="collectionpointmilkreconcilechoice",
            constraint=models.UniqueConstraint(
                fields=("collection_point", "route", "date"),
                name="uniq_collection_point_milk_reconcile_day",
            ),
        ),
    ]
