from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from collection_point_loans.models import CollectionPointLoan, CollectionPointLoanRepaymentSchedule


def _redistribute_schedule_overpayments(schedules, requested_paid_amounts):
    """Cap each instalment at its due amount and spill excess backward, then forward."""
    schedule_list = list(schedules)
    amount_by_id = {
        row.id: (row.installment_amount or Decimal("0.00")).quantize(Decimal("0.01"))
        for row in schedule_list
    }
    assigned = {
        row.id: min(
            (requested_paid_amounts.get(row.id) or Decimal("0.00")).quantize(Decimal("0.01")),
            amount_by_id[row.id],
        )
        for row in schedule_list
    }
    overflow_by_id = {
        row.id: max(
            Decimal("0.00"),
            (requested_paid_amounts.get(row.id) or Decimal("0.00")).quantize(Decimal("0.01"))
            - amount_by_id[row.id],
        ).quantize(Decimal("0.01"))
        for row in schedule_list
    }

    for index, row in enumerate(schedule_list):
        overflow = overflow_by_id[row.id]
        if overflow <= Decimal("0.00"):
            continue

        for prev_row in reversed(schedule_list[:index]):
            space = (amount_by_id[prev_row.id] - assigned[prev_row.id]).quantize(Decimal("0.01"))
            if space <= Decimal("0.00"):
                continue
            moved = min(space, overflow)
            assigned[prev_row.id] = (assigned[prev_row.id] + moved).quantize(Decimal("0.01"))
            overflow = (overflow - moved).quantize(Decimal("0.01"))
            if overflow <= Decimal("0.00"):
                break

        if overflow > Decimal("0.00"):
            for next_row in schedule_list[index + 1 :]:
                space = (amount_by_id[next_row.id] - assigned[next_row.id]).quantize(
                    Decimal("0.01")
                )
                if space <= Decimal("0.00"):
                    continue
                moved = min(space, overflow)
                assigned[next_row.id] = (assigned[next_row.id] + moved).quantize(
                    Decimal("0.01")
                )
                overflow = (overflow - moved).quantize(Decimal("0.01"))
                if overflow <= Decimal("0.00"):
                    break

    return assigned


class Command(BaseCommand):
    help = (
        "Normalize historical collection-point loan schedules so instalment overpayments "
        "settle previous unpaid instalments first, then future unpaid instalments."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show how many loans/schedules would be updated without saving.",
        )
        parser.add_argument(
            "--loan",
            type=int,
            default=None,
            help="Limit normalization to one collection-point loan id.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        loan_id = options["loan"]

        loans = CollectionPointLoan.objects.prefetch_related("repayment_schedules").order_by("id")
        if loan_id:
            loans = loans.filter(pk=loan_id)

        updated_loans = 0
        updated_schedules = 0
        skipped_loans = 0

        for loan in loans.iterator(chunk_size=200):
            schedules = list(loan.repayment_schedules.order_by("installment_number"))
            if not schedules:
                skipped_loans += 1
                continue
            if not any(
                (row.paid_amount or Decimal("0.00")) > (row.installment_amount or Decimal("0.00"))
                for row in schedules
            ):
                skipped_loans += 1
                continue

            requested = {
                row.id: (row.paid_amount or Decimal("0.00")).quantize(Decimal("0.01"))
                for row in schedules
            }
            redistributed = _redistribute_schedule_overpayments(schedules, requested)

            changed_rows = []
            for row in schedules:
                new_paid = redistributed.get(row.id, Decimal("0.00")).quantize(Decimal("0.01"))
                old_paid = (row.paid_amount or Decimal("0.00")).quantize(Decimal("0.01"))
                if new_paid != old_paid:
                    changed_rows.append((row, new_paid))

            if not changed_rows:
                skipped_loans += 1
                continue

            updated_loans += 1
            updated_schedules += len(changed_rows)
            if dry_run:
                continue

            with transaction.atomic():
                for row, new_paid in changed_rows:
                    row.paid_amount = new_paid
                    row.sync_paid_status()
                    if not row.is_paid:
                        row.payment_note = ""
                    row.save(
                        update_fields=[
                            "paid_amount",
                            "is_paid",
                            "paid_on",
                            "payment_method",
                            "payment_note",
                        ]
                    )

                total_paid = sum(
                    (redistributed.get(row.id, Decimal("0.00")) for row in schedules),
                    Decimal("0.00"),
                ).quantize(Decimal("0.01"))
                loan.prior_paid_amount = total_paid
                loan.save(update_fields=["prior_paid_amount", "updated_at"])

        verb = "Would normalize" if dry_run else "Normalized"
        scope = f" loan #{loan_id}" if loan_id else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {updated_loans} loan(s){scope} and {updated_schedules} instalment row(s); "
                f"skipped {skipped_loans} loan(s)."
            )
        )
