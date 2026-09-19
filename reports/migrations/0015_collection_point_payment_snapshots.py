from decimal import Decimal

from django.db import migrations, models


def payment_sheet_branch_key(branch_ids):
    ids = sorted({int(b) for b in (branch_ids or []) if str(b).isdigit()})
    return ",".join(str(branch_id) for branch_id in ids)


def backfill_collection_point_payment_snapshots(apps, schema_editor):
    CollectionPointPeriodPayment = apps.get_model("reports", "CollectionPointPeriodPayment")
    CollectionPointPaymentSheetRecord = apps.get_model("reports", "CollectionPointPaymentSheetRecord")
    CollectionPoint = apps.get_model("masters", "CollectionPoint")

    points = {point.id: point for point in CollectionPoint.objects.all().only("id", "collector_fee")}
    sheet_groups = {}
    for payment in CollectionPointPeriodPayment.objects.order_by("paid_at", "id"):
        if payment.rate is None:
            point = points.get(payment.collection_point_id)
            payment.collector_fee_rate = (point.collector_fee if point else Decimal("0")).quantize(
                Decimal("0.01")
            )
            if payment.net_amount is None and payment.expected_amount is not None:
                payment.net_amount = payment.expected_amount
            payment.save(update_fields=["collector_fee_rate", "net_amount"])

        branch_key = payment_sheet_branch_key(payment.branch_ids)
        group_key = (payment.period_start, payment.period_end, branch_key)
        group = sheet_groups.setdefault(
            group_key,
            {
                "period_start": payment.period_start,
                "period_end": payment.period_end,
                "branch_ids": payment.branch_ids or [],
                "branch_key": branch_key,
                "point_count": 0,
                "total_paid": Decimal("0"),
                "total_gross": Decimal("0"),
                "last_paid_at": payment.paid_at,
            },
        )
        group["point_count"] += 1
        group["total_paid"] += payment.amount or Decimal("0")
        if payment.gross_amount is not None:
            group["total_gross"] += payment.gross_amount
        if payment.paid_at and (
            group["last_paid_at"] is None or payment.paid_at > group["last_paid_at"]
        ):
            group["last_paid_at"] = payment.paid_at

    for group in sheet_groups.values():
        CollectionPointPaymentSheetRecord.objects.update_or_create(
            period_start=group["period_start"],
            period_end=group["period_end"],
            branch_key=group["branch_key"],
            defaults={
                "branch_ids": group["branch_ids"],
                "point_count": group["point_count"],
                "total_paid": group["total_paid"].quantize(Decimal("0.01")),
                "total_gross": group["total_gross"].quantize(Decimal("0.01")),
                "last_paid_at": group["last_paid_at"],
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0014_farmer_payment_snapshots"),
    ]

    operations = [
        migrations.AddField(
            model_name="collectionpointperiodpayment",
            name="advance_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="collectionpointperiodpayment",
            name="collector_fee_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="collectionpointperiodpayment",
            name="collector_fee_rate",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Collector fee per unit when this payment was recorded.",
                max_digits=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="collectionpointperiodpayment",
            name="goods_deduction",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="collectionpointperiodpayment",
            name="gross_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="collectionpointperiodpayment",
            name="loan_deduction",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="collectionpointperiodpayment",
            name="net_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="collectionpointperiodpayment",
            name="rate",
            field=models.DecimalField(
                blank=True,
                decimal_places=2,
                help_text="Average linked farmer rate when this payment was recorded.",
                max_digits=10,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="collectionpointperiodpayment",
            name="total_quantity",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.CreateModel(
            name="CollectionPointPaymentSheetRecord",
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
                ("point_count", models.PositiveIntegerField(default=0)),
                ("total_paid", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("total_gross", models.DecimalField(decimal_places=2, default=0, max_digits=14)),
                ("last_paid_at", models.DateTimeField(blank=True, null=True)),
            ],
            options={
                "ordering": ["-last_paid_at", "-period_end", "-period_start"],
            },
        ),
        migrations.AddConstraint(
            model_name="collectionpointpaymentsheetrecord",
            constraint=models.UniqueConstraint(
                fields=("period_start", "period_end", "branch_key"),
                name="uniq_collection_point_payment_sheet_record",
            ),
        ),
        migrations.RunPython(
            backfill_collection_point_payment_snapshots,
            migrations.RunPython.noop,
        ),
    ]
