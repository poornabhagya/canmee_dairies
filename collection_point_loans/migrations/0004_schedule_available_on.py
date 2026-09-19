from django.db import migrations, models
from django.db.models import F
import django.utils.timezone


def backfill_available_on(apps, schema_editor):
    Schedule = apps.get_model("collection_point_loans", "CollectionPointLoanRepaymentSchedule")
    Schedule.objects.filter(available_on__isnull=True).update(available_on=F("due_date"))
    Schedule.objects.update(available_on=F("due_date"))


class Migration(migrations.Migration):

    dependencies = [
        ("collection_point_loans", "0003_installment_method"),
    ]

    operations = [
        migrations.AddField(
            model_name="collectionpointloanrepaymentschedule",
            name="available_on",
            field=models.DateField(
                default=django.utils.timezone.localdate,
                help_text="First date the remaining instalment can be deducted on a payment sheet.",
            ),
        ),
        migrations.RunPython(backfill_available_on, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="collectionpointloanrepaymentschedule",
            name="available_on",
            field=models.DateField(
                help_text="First date the remaining instalment can be deducted on a payment sheet.",
            ),
        ),
        migrations.AddIndex(
            model_name="collectionpointloanrepaymentschedule",
            index=models.Index(
                fields=["is_paid", "available_on"],
                name="cp_loan_sched_open_avail_idx",
            ),
        ),
    ]
