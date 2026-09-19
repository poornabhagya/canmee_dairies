import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("farmer_loans", "0005_alter_farmerloan_installment_count_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="FarmerLoanActionLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("loan_label", models.CharField(blank=True, max_length=40)),
                (
                    "action",
                    models.CharField(
                        choices=[
                            ("created", "Created"),
                            ("updated", "Updated"),
                            ("deleted", "Deleted"),
                            ("submitted", "Submitted for approval"),
                            ("approved", "Approved"),
                            ("rejected", "Rejected"),
                            ("reverted_to_draft", "Reverted to draft"),
                        ],
                        max_length=30,
                    ),
                ),
                ("performed_at", models.DateTimeField(auto_now_add=True)),
                ("notes", models.TextField(blank=True)),
                ("details", models.JSONField(blank=True, default=dict)),
                (
                    "loan",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="action_logs",
                        to="farmer_loans.farmerloan",
                    ),
                ),
                (
                    "performed_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="farmer_loan_action_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-performed_at", "-id"],
            },
        ),
    ]
