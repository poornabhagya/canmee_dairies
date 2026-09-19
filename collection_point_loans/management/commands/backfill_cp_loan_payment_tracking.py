from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction

from collection_point_loans.models import CollectionPointLoanRepaymentSchedule
from reports.models import CollectionPointPaymentLoanDeduction, CollectionPointPeriodPayment


class Command(BaseCommand):
    help = (
        "Backfill payment date/method on historical collection-point loan instalments. "
        "Uses payment-sheet loan deductions when available; otherwise marks paid rows as Direct."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would change without writing updates.",
        )
        parser.add_argument(
            "--loan",
            type=int,
            default=None,
            help="Limit the backfill to one collection-point loan id.",
        )
        parser.add_argument(
            "--rewrite",
            action="store_true",
            help="Rewrite rows even when a payment method is already set.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        loan_id = options["loan"]
        rewrite = options["rewrite"]

        schedules = CollectionPointLoanRepaymentSchedule.objects.select_related(
            "loan", "loan__collection_point"
        ).order_by("loan_id", "installment_number")
        if loan_id:
            schedules = schedules.filter(loan_id=loan_id)
        if not rewrite:
            schedules = schedules.filter(payment_method="")

        payment_date_map = {
            (
                payment.collection_point_id,
                payment.period_start,
                payment.period_end,
            ): payment.paid_at.date() if payment.paid_at else None
            for payment in CollectionPointPeriodPayment.objects.all().only(
                "collection_point_id", "period_start", "period_end", "paid_at"
            )
        }

        deduction_periods_by_schedule = defaultdict(list)
        for row in CollectionPointPaymentLoanDeduction.objects.select_related("collection_point").filter(
            deduct_amount__gt=0
        ):
            deduction_periods_by_schedule[row.schedule_id].append(
                (row.collection_point_id, row.period_start, row.period_end)
            )

        updated = 0
        skipped = 0

        for schedule in schedules.iterator(chunk_size=500):
            if not schedule.is_paid:
                skipped += 1
                continue

            method = ""
            paid_on = schedule.paid_on
            note = schedule.payment_note

            deduction_periods = deduction_periods_by_schedule.get(schedule.id, [])
            payment_dates = [
                payment_date_map.get(key)
                for key in deduction_periods
                if payment_date_map.get(key)
            ]

            if payment_dates:
                method = schedule.PaymentMethod.MILK_PAYMENT_SHEET
                paid_on = max(payment_dates)
                if not note:
                    note = "Settled from milk payment sheet."
            else:
                method = schedule.PaymentMethod.DIRECT

            if (
                schedule.payment_method == method
                and schedule.paid_on == paid_on
                and schedule.payment_note == note
            ):
                skipped += 1
                continue

            updated += 1
            if dry_run:
                continue

            with transaction.atomic():
                schedule.payment_method = method
                if paid_on is not None:
                    schedule.paid_on = paid_on
                schedule.payment_note = note
                schedule.save(update_fields=["payment_method", "paid_on", "payment_note"])

        verb = "Would update" if dry_run else "Updated"
        scope = f" loan #{loan_id}" if loan_id else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {updated} paid instalment(s){scope}; skipped {skipped}."
            )
        )
