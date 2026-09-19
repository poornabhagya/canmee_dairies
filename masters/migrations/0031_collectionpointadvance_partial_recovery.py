from django.db import migrations, models
from django.db.models import F
import django.utils.timezone


def backfill_partial_recovery(apps, schema_editor):
    CollectionPointAdvancePayment = apps.get_model("masters", "CollectionPointAdvancePayment")
    CollectionPointAdvancePayment.objects.filter(is_recovered=True).update(
        recovered_amount=F("amount")
    )
    CollectionPointAdvancePayment.objects.update(available_on=F("date"))


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0030_remove_advance_previous_outstanding"),
    ]

    operations = [
        migrations.AddField(
            model_name="collectionpointadvancepayment",
            name="recovered_amount",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Amount already deducted from collection point payments.",
                max_digits=14,
            ),
        ),
        migrations.AddField(
            model_name="collectionpointadvancepayment",
            name="available_on",
            field=models.DateField(
                default=django.utils.timezone.localdate,
                help_text="First date the remaining balance can be deducted on a payment sheet.",
            ),
        ),
        migrations.RunPython(backfill_partial_recovery, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="collectionpointadvancepayment",
            name="available_on",
            field=models.DateField(
                help_text="First date the remaining balance can be deducted on a payment sheet.",
            ),
        ),
        migrations.AddIndex(
            model_name="collectionpointadvancepayment",
            index=models.Index(
                fields=["collection_point", "is_recovered", "available_on"],
                name="cp_adv_open_avail_idx",
            ),
        ),
    ]
