import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("collections", "0002_milkcollection_source_farmer"),
    ]

    operations = [
        migrations.CreateModel(
            name="MilkCollectionAdjustment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("delta_kg", models.DecimalField(decimal_places=2, max_digits=10)),
                (
                    "created_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "milk_collection",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="kg_adjustments",
                        to="collections.milkcollection",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "id"],
            },
        ),
    ]
