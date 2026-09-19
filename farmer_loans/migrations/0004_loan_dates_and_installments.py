from datetime import date, timedelta

from django.db import migrations, models
from django.utils import timezone


def forwards_copy_repayment_fields(apps, schema_editor):
    FarmerLoan = apps.get_model("farmer_loans", "FarmerLoan")
    FarmerLoanRepaymentSchedule = apps.get_model("farmer_loans", "FarmerLoanRepaymentSchedule")
    today = timezone.localdate()
    for loan in FarmerLoan.objects.all().iterator():
        loan.installment_count = max(1, (loan.repayment_period_months or 1) * 2)
        loan.loan_date = (loan.created_at.date() if loan.created_at else today)
        first_schedule = (
            FarmerLoanRepaymentSchedule.objects.filter(loan_id=loan.pk)
            .order_by("installment_number")
            .values_list("due_date", flat=True)
            .first()
        )
        if first_schedule:
            loan.first_repayment_date = first_schedule
        else:
            loan.first_repayment_date = loan.loan_date + timedelta(days=15)
        loan.save(update_fields=["installment_count", "loan_date", "first_repayment_date"])


class Migration(migrations.Migration):
    dependencies = [
        ("farmer_loans", "0003_farmerloan_approved_at_farmerloan_approved_by_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="farmerloan",
            name="loan_date",
            field=models.DateField(default=date.today),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="farmerloan",
            name="first_repayment_date",
            field=models.DateField(default=date.today),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="farmerloan",
            name="installment_count",
            field=models.PositiveIntegerField(default=2, help_text="Total number of instalments."),
            preserve_default=False,
        ),
        migrations.RunPython(forwards_copy_repayment_fields, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="farmerloan",
            name="repayment_period_months",
        ),
    ]
