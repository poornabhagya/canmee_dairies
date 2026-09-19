from django.db import migrations, models


def backfill_driver_status(apps, schema_editor):
    FarmerGoodsIssue = apps.get_model("suppliers", "FarmerGoodsIssue")
    FarmerGoodsIssue.objects.filter(issue_to_type="collection_point").update(
        driver_status="accepted"
    )
    FarmerGoodsIssue.objects.exclude(issue_to_type="collection_point").update(
        driver_status="not_required"
    )


class Migration(migrations.Migration):

    dependencies = [
        ("suppliers", "0013_farmergoodsissue_settlement_note"),
    ]

    operations = [
        migrations.AddField(
            model_name="farmergoodsissue",
            name="driver_status",
            field=models.CharField(
                choices=[
                    ("not_required", "Not required"),
                    ("pending", "Pending driver"),
                    ("accepted", "Accepted"),
                    ("rejected", "Rejected"),
                ],
                db_index=True,
                default="not_required",
                help_text="Collection-point issues require route-driver accept/reject before payment.",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="farmergoodsissue",
            name="driver_responded_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="farmergoodsissue",
            name="driver_response_note",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.RunPython(backfill_driver_status, migrations.RunPython.noop),
    ]
