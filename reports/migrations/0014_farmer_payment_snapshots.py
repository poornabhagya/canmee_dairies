from decimal import Decimal

from django.db import migrations, models


def payment_sheet_branch_key(branch_ids):
    ids = sorted({int(b) for b in (branch_ids or []) if str(b).isdigit()})
    return ",".join(str(b) for b in ids)


def backfill_payment_snapshots(apps, schema_editor):
    FarmerPeriodPayment = apps.get_model("reports", "FarmerPeriodPayment")
    FarmerPaymentSheetRecord = apps.get_model("reports", "FarmerPaymentSheetRecord")
    FarmerRateHistory = apps.get_model("masters", "FarmerRateHistory")
    Farmer = apps.get_model("masters", "Farmer")

    farmers = {f.id: f for f in Farmer.objects.all().only("id", "rate", "apply_rate_paid")}
    history_by_farmer = {}
    for row in FarmerRateHistory.objects.order_by("farmer_id", "-effective_at", "-id").values(
        "farmer_id", "rate", "apply_rate_paid", "effective_at"
    ):
        history_by_farmer.setdefault(row["farmer_id"], []).append(row)

    def rate_at(farmer_id, paid_at):
        farmer = farmers.get(farmer_id)
        rows = history_by_farmer.get(farmer_id) or []
        for row in rows:
            if row["effective_at"] <= paid_at:
                return row["rate"], row["apply_rate_paid"] or "yes"
        if farmer:
            return farmer.rate, farmer.apply_rate_paid or "yes"
        return Decimal("0.00"), "yes"

    sheet_groups = {}
    for payment in FarmerPeriodPayment.objects.order_by("paid_at", "id"):
        if payment.rate is None:
            rate, apply_rate_paid = rate_at(payment.farmer_id, payment.paid_at)
            payment.rate = rate
            payment.apply_rate_paid = apply_rate_paid
            if payment.gross_amount is None and payment.expected_amount is not None:
                payment.net_amount = payment.expected_amount
            payment.save(
                update_fields=["rate", "apply_rate_paid", "net_amount"]
            )

        branch_key = payment_sheet_branch_key(payment.branch_ids)
        group_key = (payment.period_start, payment.period_end, branch_key)
        group = sheet_groups.setdefault(
            group_key,
            {
                "period_start": payment.period_start,
                "period_end": payment.period_end,
                "branch_ids": payment.branch_ids or [],
                "branch_key": branch_key,
                "farmer_count": 0,
                "total_paid": Decimal("0"),
                "total_gross": Decimal("0"),
                "last_paid_at": payment.paid_at,
            },
        )
        group["farmer_count"] += 1
        group["total_paid"] += payment.amount or Decimal("0")
        if payment.gross_amount is not None:
            group["total_gross"] += payment.gross_amount
        if payment.paid_at and (
            group["last_paid_at"] is None or payment.paid_at > group["last_paid_at"]
        ):
            group["last_paid_at"] = payment.paid_at

    for group in sheet_groups.values():
        FarmerPaymentSheetRecord.objects.update_or_create(
            period_start=group["period_start"],
            period_end=group["period_end"],
            branch_key=group["branch_key"],
            defaults={
                "branch_ids": group["branch_ids"],
                "farmer_count": group["farmer_count"],
                "total_paid": group["total_paid"].quantize(Decimal("0.01")),
                "total_gross": group["total_gross"].quantize(Decimal("0.01")),
                "last_paid_at": group["last_paid_at"],
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0013_collectionpointpaymentperioddeductionskip"),
    ]

    operations = [
        migrations.AddField(
            model_name="farmerperiodpayment",
            name="advance_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="farmerperiodpayment",
            name="apply_rate_paid",
            field=models.CharField(blank=True, default="", max_length=3),
        ),
        migrations.AddField(
            model_name="farmerperiodpayment",
            name="goods_deduction",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="farmerperiodpayment",
            name="gross_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="farmerperiodpayment",
            name="loan_deduction",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="farmerperiodpayment",
            name="net_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="farmerperiodpayment",
            name="rate",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Farmer rate used when this payment was recorded.",
                max_digits=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="farmerperiodpayment",
            name="total_liters",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.CreateModel(
            name="FarmerPaymentSheetRecord",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                ("branch_ids", models.JSONField(blank=True, default=list)),
                ("branch_key", models.CharField(db_index=True, max_length=128)),
                ("farmer_count", models.PositiveIntegerField(default=0)),
                ("total_paid", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("total_gross", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("last_paid_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ["-last_paid_at", "-period_end", "-period_start"],
            },
        ),
        migrations.AddConstraint(
            model_name="farmerpaymentsheetrecord",
            constraint=models.UniqueConstraint(
                fields=("period_start", "period_end", "branch_key"),
                name="uniq_farmer_payment_sheet_record",
            ),
        ),
        migrations.RunPython(backfill_payment_snapshots, migrations.RunPython.noop),
    ]
