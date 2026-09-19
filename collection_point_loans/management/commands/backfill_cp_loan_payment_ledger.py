from collections import defaultdict
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from collection_point_loans.models import CollectionPointLoan, CollectionPointLoanPayment
from collection_point_loans.payment_ledger import record_collection_point_loan_payment
from reports.models import CollectionPointPaymentLoanDeduction, CollectionPointPeriodPayment


class Command(BaseCommand):
    help = (
        "Backfill historical collection-point loan payment transactions from "
        "payment-sheet deductions and existing paid schedule balances."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be created without writing any payment transactions.",
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
            help="Delete existing payment transactions for the targeted loans before rebuilding.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        loan_id = options["loan"]
        rewrite = options["rewrite"]

        loans = CollectionPointLoan.objects.prefetch_related("repayment_schedules").order_by("id")
        if loan_id:
            loans = loans.filter(pk=loan_id)

        payments_by_period = {
            (row.collection_point_id, row.period_start, row.period_end): row
            for row in CollectionPointPeriodPayment.objects.select_related("recorded_by").all()
        }

        deductions_by_loan_period = defaultdict(list)
        for row in CollectionPointPaymentLoanDeduction.objects.select_related(
            "schedule", "schedule__loan", "collection_point", "updated_by"
        ).filter(deduct_amount__gt=0):
            key = (
                row.schedule.loan_id,
                row.collection_point_id,
                row.period_start,
                row.period_end,
            )
            deductions_by_loan_period[key].append(row)

        created_payments = 0
        created_allocations = 0
        skipped_loans = 0

        for loan in loans.iterator(chunk_size=200):
            if not loan.repayment_schedules.exists():
                skipped_loans += 1
                continue

            existing_qs = loan.payment_transactions.all()
            if existing_qs.exists() and not rewrite:
                skipped_loans += 1
                continue

            sheet_transactions = []
            schedule_sheet_totals = defaultdict(lambda: Decimal("0.00"))
            for key, rows in deductions_by_loan_period.items():
                if key[0] != loan.id:
                    continue
                payment = payments_by_period.get((key[1], key[2], key[3]))
                payment_date = (
                    payment.paid_at.date() if payment and payment.paid_at else key[3]
                )
                allocations = []
                for row in rows:
                    amount = (row.deduct_amount or Decimal("0.00")).quantize(Decimal("0.01"))
                    if amount <= Decimal("0.00"):
                        continue
                    allocations.append((row.schedule, amount))
                    schedule_sheet_totals[row.schedule_id] += amount
                if allocations:
                    sheet_transactions.append(
                        {
                            "allocations": allocations,
                            "payment_date": payment_date,
                            "note": (
                                (payment.note or "").strip()
                                if payment and payment.note
                                else "Historical payment sheet settlement backfill."
                            ),
                            "created_by": payment.recorded_by if payment else None,
                            "source_period_payment": payment,
                        }
                    )

            direct_groups = defaultdict(list)
            for schedule in loan.repayment_schedules.all().order_by("installment_number"):
                paid_amount = (schedule.paid_amount or Decimal("0.00")).quantize(Decimal("0.01"))
                sheet_total = schedule_sheet_totals[schedule.id].quantize(Decimal("0.01"))
                residual = (paid_amount - sheet_total).quantize(Decimal("0.01"))
                if residual <= Decimal("0.00"):
                    continue
                payment_date = schedule.paid_on or schedule.due_date
                note = (
                    "Historical direct settlement backfill."
                    if schedule.paid_on
                    else "Historical direct settlement backfill (estimated date)."
                )
                direct_groups[(payment_date, note)].append((schedule, residual))

            if not sheet_transactions and not direct_groups and not rewrite:
                skipped_loans += 1
                continue

            if dry_run:
                created_payments += len(sheet_transactions) + len(direct_groups)
                created_allocations += sum(
                    len(item["allocations"]) for item in sheet_transactions
                ) + sum(len(items) for items in direct_groups.values())
                continue

            with transaction.atomic():
                if rewrite:
                    existing_qs.delete()

                for item in sheet_transactions:
                    payment = record_collection_point_loan_payment(
                        loan=loan,
                        allocations=item["allocations"],
                        payment_date=item["payment_date"],
                        method=CollectionPointLoanPayment.Method.MILK_PAYMENT_SHEET,
                        note=item["note"],
                        created_by=item["created_by"],
                        source_period_payment=item["source_period_payment"],
                    )
                    if payment:
                        created_payments += 1
                        created_allocations += len(item["allocations"])

                for (payment_date, note), allocations in direct_groups.items():
                    payment = record_collection_point_loan_payment(
                        loan=loan,
                        allocations=allocations,
                        payment_date=payment_date,
                        method=CollectionPointLoanPayment.Method.DIRECT,
                        note=note,
                    )
                    if payment:
                        created_payments += 1
                        created_allocations += len(allocations)

        verb = "Would create" if dry_run else "Created"
        scope = f" for loan #{loan_id}" if loan_id else ""
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {created_payments} payment transaction(s){scope} and "
                f"{created_allocations} allocation row(s); skipped {skipped_loans} loan(s)."
            )
        )
