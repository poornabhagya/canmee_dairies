from datetime import datetime, time, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from farmer_loans.models import FarmerLoan, FarmerLoanRepaymentSchedule
from masters.models import (
    Farmer,
    FarmerAdvancePayment,
)
from milk_collections.models import CollectionSource, MilkCollection
from suppliers.models import FarmerGoodsIssue

from .models import (
    CollectionPointPaymentAdvanceDeduction,
    CollectionPointPeriodPayment,
    CollectionPointPreviousOutstanding,
    FarmerPeriodPayment,
)


def _collection_point_payment_method_fields(collection_point, payment_method=None, bank_account=None):
    method = (payment_method or CollectionPointPeriodPayment.PaymentMethod.CASH).strip()
    if method not in CollectionPointPeriodPayment.PaymentMethod.values:
        method = CollectionPointPeriodPayment.PaymentMethod.CASH
    account = bank_account
    if method == CollectionPointPeriodPayment.PaymentMethod.BANK_TRANSFER:
        if account is not None and account.collection_point_id != collection_point.pk:
            account = None
        if account is None:
            account = collection_point.primary_bank_account
    else:
        account = None
    fields = {
        "payment_method": method,
        "bank_account": account,
        "bank_account_name": "",
        "bank_account_number": "",
        "bank_name": "",
        "bank_branch": "",
    }
    if account is not None:
        fields.update(
            {
                "bank_account_name": account.account_name or "",
                "bank_account_number": account.account_number or "",
                "bank_name": account.bank_name or "",
                "bank_branch": account.bank_branch or "",
            }
        )
    return fields


def _farmer_payment_method_fields(farmer, payment_method=None, bank_account=None):
    method = (payment_method or FarmerPeriodPayment.PaymentMethod.CASH).strip()
    if method not in FarmerPeriodPayment.PaymentMethod.values:
        method = FarmerPeriodPayment.PaymentMethod.CASH
    account = bank_account
    if method == FarmerPeriodPayment.PaymentMethod.BANK_TRANSFER:
        if account is not None and account.farmer_id != farmer.pk:
            account = None
        if account is None:
            account = farmer.primary_bank_account
    else:
        account = None
    fields = {
        "payment_method": method,
        "bank_account": account,
        "bank_account_name": "",
        "bank_account_number": "",
        "bank_name": "",
        "bank_branch": "",
    }
    if account is not None:
        fields.update(
            {
                "bank_account_name": account.account_name or "",
                "bank_account_number": account.account_number or "",
                "bank_name": account.bank_name or "",
                "bank_branch": account.bank_branch or "",
            }
        )
    return fields


from .deduction_skips import (
    collection_point_advance_deduct_map,
    collection_point_advance_remaining,
    collection_point_advances_available_for_period,
    goods_issue_is_skipped,
    payment_period_deduction_skip_map,
    planned_collection_point_advance_deduct,
)


def _period_datetime_window(period_start, period_end):
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(period_start, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(period_end + timedelta(days=1), time.min), tz)
    return start_dt, end_dt


def farmer_period_payment_map(farmer_ids, period_start, period_end):
    if not farmer_ids:
        return {}
    rows = FarmerPeriodPayment.objects.filter(
        farmer_id__in=farmer_ids,
        period_start=period_start,
        period_end=period_end,
    ).select_related("recorded_by")
    return {row.farmer_id: row for row in rows}


def collection_point_period_payment_map(point_ids, period_start, period_end):
    if not point_ids:
        return {}
    rows = CollectionPointPeriodPayment.objects.filter(
        collection_point_id__in=point_ids,
        period_start=period_start,
        period_end=period_end,
    ).select_related("recorded_by")
    return {row.collection_point_id: row for row in rows}


def paid_at_for_payment_date(payment_date):
    """Build a timezone-aware paid_at from a calendar date (now if that date is today)."""
    if payment_date is None:
        return timezone.now()
    today = timezone.localdate()
    if payment_date == today:
        return timezone.now()
    tz = timezone.get_current_timezone()
    naive = datetime.combine(payment_date, time(12, 0, 0))
    return timezone.make_aware(naive, tz)


def mark_farmer_milk_collections_paid(
    farmer, period_start, period_end, branch_ids, paid_at=None
):
    stamp = paid_at or timezone.now()
    qs = MilkCollection.objects.filter(
        source=CollectionSource.FARMER,
        farmer=farmer,
        date__gte=period_start,
        date__lte=period_end,
        is_paid=False,
    )
    if branch_ids:
        qs = qs.filter(branch_id__in=branch_ids)
    return qs.update(is_paid=True, paid_at=stamp)


def mark_collection_point_milk_collections_paid(
    collection_point, period_start, period_end, branch_ids, paid_at=None
):
    stamp = paid_at or timezone.now()
    qs = MilkCollection.objects.filter(
        source=CollectionSource.POINT,
        collection_point=collection_point,
        date__gte=period_start,
        date__lte=period_end,
        is_paid=False,
    )
    if branch_ids:
        qs = qs.filter(branch_id__in=branch_ids)
    return qs.update(is_paid=True, paid_at=stamp)


def sync_farmer_milk_paid_from_period_payments(
    farmer_ids=None, sheet_start=None, sheet_end=None, branch_ids=None
):
    """Ensure milk rows are marked paid for any recorded farmer period payment."""
    qs = FarmerPeriodPayment.objects.select_related("farmer")
    if farmer_ids:
        qs = qs.filter(farmer_id__in=farmer_ids)
    if sheet_start and sheet_end:
        qs = qs.filter(period_start__lte=sheet_end, period_end__gte=sheet_start)
    sheet_branch_ids = [int(b) for b in (branch_ids or []) if str(b).isdigit()]
    updated = 0
    for payment in qs:
        pay_branches = [int(b) for b in (payment.branch_ids or []) if str(b).isdigit()]
        if sheet_branch_ids and pay_branches:
            if not set(pay_branches) & set(sheet_branch_ids):
                continue
        effective_branches = pay_branches or sheet_branch_ids
        updated += mark_farmer_milk_collections_paid(
            payment.farmer,
            payment.period_start,
            payment.period_end,
            effective_branches,
        )
    return updated


def sync_collection_point_milk_paid_from_period_payments(
    point_ids=None, sheet_start=None, sheet_end=None, branch_ids=None
):
    """Ensure point milk rows are marked paid for any recorded period payment."""
    qs = CollectionPointPeriodPayment.objects.select_related("collection_point")
    if point_ids:
        qs = qs.filter(collection_point_id__in=point_ids)
    if sheet_start and sheet_end:
        qs = qs.filter(period_start__lte=sheet_end, period_end__gte=sheet_start)
    sheet_branch_ids = [int(b) for b in (branch_ids or []) if str(b).isdigit()]
    updated = 0
    for payment in qs:
        pay_branches = [int(b) for b in (payment.branch_ids or []) if str(b).isdigit()]
        if sheet_branch_ids and pay_branches:
            if not set(pay_branches) & set(sheet_branch_ids):
                continue
        effective_branches = pay_branches or sheet_branch_ids
        updated += mark_collection_point_milk_collections_paid(
            payment.collection_point,
            payment.period_start,
            payment.period_end,
            effective_branches,
        )
    return updated


def recover_farmer_advances_for_period(farmer, period_start, period_end, skip_advance_ids=None):
    now = timezone.now()
    qs = FarmerAdvancePayment.objects.filter(
        farmer=farmer,
        date__gte=period_start,
        date__lte=period_end,
        is_recovered=False,
    )
    if skip_advance_ids:
        qs = qs.exclude(id__in=skip_advance_ids)
    return qs.update(is_recovered=True, recovered_at=now)


def recover_collection_point_advances_for_period(
    collection_point, period_start, period_end, skip_advance_ids=None
):
    now = timezone.now()
    skip_advance_ids = set(skip_advance_ids or [])
    deduct_map = collection_point_advance_deduct_map(
        [collection_point.id], period_start, period_end
    ).get(collection_point.id, {})
    next_available = period_end + timedelta(days=1)
    updated = 0
    for advance in collection_point_advances_available_for_period(
        collection_point.id, period_start, period_end, pending_only=True
    ):
        remaining = collection_point_advance_remaining(advance)
        deduct = planned_collection_point_advance_deduct(
            advance.id, remaining, skip_advance_ids, deduct_map
        )
        CollectionPointPaymentAdvanceDeduction.objects.update_or_create(
            collection_point=collection_point,
            period_start=period_start,
            period_end=period_end,
            advance=advance,
            defaults={"deduct_amount": deduct},
        )
        recovered_so_far = (Decimal(advance.recovered_amount or 0) + deduct).quantize(
            Decimal("0.01")
        )
        if recovered_so_far > Decimal(advance.amount or 0):
            recovered_so_far = Decimal(advance.amount or 0).quantize(Decimal("0.01"))
        leftover = (Decimal(advance.amount or 0) - recovered_so_far).quantize(Decimal("0.01"))
        advance.recovered_amount = recovered_so_far
        if leftover <= 0:
            advance.is_recovered = True
            advance.recovered_at = now
            advance.recovered_amount = Decimal(advance.amount or 0).quantize(Decimal("0.01"))
        else:
            advance.is_recovered = False
            if deduct > 0:
                advance.recovered_at = now
            advance.available_on = next_available
        advance.save(
            update_fields=["recovered_amount", "is_recovered", "recovered_at", "available_on"]
        )
        updated += 1
    return updated


def recover_collection_point_previous_outstanding_for_period(
    collection_point, period_start, period_end
):
    now = timezone.now()
    return CollectionPointPreviousOutstanding.objects.filter(
        collection_point=collection_point,
        available_on__lte=period_end,
        is_recovered=False,
    ).update(
        is_recovered=True,
        recovered_at=now,
        recovered_period_start=period_start,
        recovered_period_end=period_end,
    )


def create_collection_point_previous_outstanding(
    *,
    collection_point,
    period_start,
    period_end,
    amount,
    recorded_by,
    originated_payment=None,
):
    """Post the period deficit as previous outstanding for later payment sheets."""
    deficit = Decimal(amount or 0).quantize(Decimal("0.01"))
    if deficit <= Decimal("0"):
        return None
    defaults = {
        "amount": deficit,
        "available_on": period_end + timedelta(days=1),
        "originated_payment": originated_payment,
        "created_by": recorded_by,
        "is_recovered": False,
        "recovered_at": None,
        "recovered_period_start": None,
        "recovered_period_end": None,
    }
    obj, _created = CollectionPointPreviousOutstanding.objects.update_or_create(
        collection_point=collection_point,
        originated_period_start=period_start,
        originated_period_end=period_end,
        defaults=defaults,
    )
    return obj


def settle_farmer_goods_for_period(
    farmer, period_start, period_end, skip_bucket=None
):
    start_dt, end_dt = _period_datetime_window(period_start, period_end)
    now = timezone.now()
    qs = FarmerGoodsIssue.objects.filter(
        issue_to_type=FarmerGoodsIssue.IssueToType.FARMER,
        issue_to_id=farmer.id,
        date__gte=start_dt,
        date__lt=end_dt,
        is_settled=False,
    )
    skip_bucket = skip_bucket or {}
    to_settle = []
    for issue in qs:
        if goods_issue_is_skipped(issue, skip_bucket):
            continue
        to_settle.append(issue.id)
    if not to_settle:
        return 0
    return FarmerGoodsIssue.objects.filter(id__in=to_settle).update(
        is_settled=True, settled_at=now
    )


def settle_collection_point_goods_for_period(collection_point, period_start, period_end):
    from .deduction_skips import (
        collection_point_payment_period_deduction_skip_map,
        goods_issue_is_skipped,
    )
    from .services import _collection_point_issue_id_candidates

    start_dt, end_dt = _period_datetime_window(period_start, period_end)
    now = timezone.now()
    skip_bucket = collection_point_payment_period_deduction_skip_map(
        [collection_point.id], period_start, period_end
    ).get(collection_point.id, {})
    qs = FarmerGoodsIssue.objects.filter(
        issue_to_type=FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
        issue_to_id__in=_collection_point_issue_id_candidates(collection_point),
        date__gte=start_dt,
        date__lt=end_dt,
        is_settled=False,
        driver_status=FarmerGoodsIssue.DriverStatus.ACCEPTED,
    )
    to_settle = []
    for issue in qs:
        if goods_issue_is_skipped(issue, skip_bucket):
            continue
        to_settle.append(issue.id)
    if not to_settle:
        return 0
    return FarmerGoodsIssue.objects.filter(id__in=to_settle).update(
        is_settled=True, settled_at=now
    )


def apply_farmer_loan_deductions_for_period(
    farmer, period_start, period_end, skip_bucket=None, deduct_all=True
):
    """Mark due loan instalments as paid when included in a period payment."""
    if not deduct_all:
        return 0
    skip_bucket = skip_bucket or {}
    skip_schedule_ids = skip_bucket.get("loan_schedule_ids", set())

    schedules = (
        FarmerLoanRepaymentSchedule.objects.select_for_update()
        .filter(
            loan__farmer=farmer,
            loan__status=FarmerLoan.Status.APPROVED,
            due_date__lte=period_end,
            due_date__gte=period_start,
            is_paid=False,
        )
        .order_by("due_date", "loan_id", "installment_number")
    )
    updated = 0
    for schedule in schedules:
        if schedule.id in skip_schedule_ids:
            continue
        pending = (
            (schedule.installment_amount or Decimal("0")) - (schedule.paid_amount or Decimal("0"))
        ).quantize(Decimal("0.01"))
        if pending <= 0:
            continue
        schedule.paid_amount = ((schedule.paid_amount or Decimal("0")) + pending).quantize(
            Decimal("0.01")
        )
        schedule.sync_paid_status()
        schedule.save(update_fields=["paid_amount", "is_paid", "paid_on"])
        updated += 1
    return updated


def apply_farmer_period_settlements(farmer, period_start, period_end):
    """Mark advances recovered, goods settled, and loan instalments paid for a farmer."""
    from .services import loan_deduction_settings_map

    skip_bucket = payment_period_deduction_skip_map(
        [farmer.id], period_start, period_end
    ).get(farmer.id, {})
    loan_settings = loan_deduction_settings_map([farmer.id], period_start, period_end)
    recover_farmer_advances_for_period(
        farmer,
        period_start,
        period_end,
        skip_advance_ids=skip_bucket.get("advance_ids"),
    )
    settle_farmer_goods_for_period(
        farmer, period_start, period_end, skip_bucket=skip_bucket
    )
    apply_farmer_loan_deductions_for_period(
        farmer,
        period_start,
        period_end,
        skip_bucket=skip_bucket,
        deduct_all=loan_settings.get(farmer.id, True),
    )


def apply_collection_point_loan_deductions_for_period(
    collection_point,
    period_start,
    period_end,
    skip_bucket=None,
    deduct_all=True,
    settled_on=None,
    source_period_payment=None,
    payment_note="",
):
    """Apply selected collection point loan instalment amounts for a period payment."""
    from collection_point_loans.models import CollectionPointLoanRepaymentSchedule
    from collection_point_loans.payment_ledger import record_collection_point_loan_payment
    from .deduction_skips import (
        collection_point_loan_deduct_map,
        collection_point_loan_remaining,
        collection_point_loan_schedules_available_for_period,
        planned_collection_point_loan_deduct,
    )
    from .models import CollectionPointPaymentLoanDeduction

    if not deduct_all:
        return 0
    skip_bucket = skip_bucket or {}
    skip_schedule_ids = set(skip_bucket.get("loan_schedule_ids", set()) or [])
    deduct_map = collection_point_loan_deduct_map(
        [collection_point.id], period_start, period_end
    ).get(collection_point.id, {})
    next_available = period_end + timedelta(days=1)
    schedules = {
        schedule.id: schedule
        for schedule in collection_point_loan_schedules_available_for_period(
            collection_point.id, period_start, period_end, pending_only=True
        ).select_for_update()
    }
    extra_ids = set(deduct_map.keys()) - set(schedules)
    if extra_ids:
        for schedule in (
            CollectionPointLoanRepaymentSchedule.objects.select_for_update()
            .filter(
                id__in=extra_ids,
                loan__collection_point=collection_point,
                is_paid=False,
            )
        ):
            schedules[schedule.id] = schedule

    updated = 0
    payment_allocations_by_loan = defaultdict(list)
    for schedule in schedules.values():
        remaining = collection_point_loan_remaining(schedule)
        deduct = planned_collection_point_loan_deduct(
            schedule.id, remaining, skip_schedule_ids, deduct_map
        )
        CollectionPointPaymentLoanDeduction.objects.update_or_create(
            collection_point=collection_point,
            period_start=period_start,
            period_end=period_end,
            schedule=schedule,
            defaults={"deduct_amount": deduct},
        )
        if deduct > 0:
            schedule.paid_amount = ((schedule.paid_amount or Decimal("0")) + deduct).quantize(
                Decimal("0.01")
            )
            schedule.sync_paid_status(paid_on=settled_on or period_end)
            payment_allocations_by_loan[schedule.loan_id].append((schedule, deduct))
            if schedule.is_paid:
                schedule.payment_method = schedule.PaymentMethod.MILK_PAYMENT_SHEET
            if schedule.is_paid and not schedule.payment_note:
                schedule.payment_note = "Settled from milk payment sheet."
        leftover = collection_point_loan_remaining(schedule)
        if leftover > 0:
            schedule.available_on = next_available
            schedule.is_paid = False
        schedule.save(
            update_fields=[
                "paid_amount",
                "is_paid",
                "paid_on",
                "payment_method",
                "payment_note",
                "available_on",
            ]
        )
        updated += 1
    for _loan_id, allocations in payment_allocations_by_loan.items():
        if not allocations:
            continue
        loan = allocations[0][0].loan
        record_collection_point_loan_payment(
            loan=loan,
            allocations=allocations,
            payment_date=settled_on or period_end,
            method=CollectionPointLoanRepaymentSchedule.PaymentMethod.MILK_PAYMENT_SHEET,
            note=(payment_note or "Settled from milk payment sheet.")[:255],
            created_by=getattr(source_period_payment, "recorded_by", None),
            source_period_payment=source_period_payment,
        )
    return updated


def apply_collection_point_period_settlements(
    collection_point,
    period_start,
    period_end,
    branch_ids=None,
    settled_on=None,
    source_period_payment=None,
    payment_note="",
):
    """Settle linked farmer deductions, CP advances/goods/loans for a balance payment."""
    from .deduction_skips import collection_point_payment_period_deduction_skip_map
    from .services import cp_loan_deduction_settings_map

    branch_ids = branch_ids or []
    farmer_ids = list(
        Farmer.objects.filter(collection_point=collection_point).values_list("id", flat=True)
    )
    for farmer in Farmer.objects.filter(id__in=farmer_ids):
        apply_farmer_period_settlements(farmer, period_start, period_end)
        mark_farmer_milk_collections_paid(
            farmer,
            period_start,
            period_end,
            branch_ids,
            paid_at=paid_at_for_payment_date(settled_on),
        )
    skip_bucket = collection_point_payment_period_deduction_skip_map(
        [collection_point.id], period_start, period_end
    ).get(collection_point.id, {})
    recover_collection_point_advances_for_period(
        collection_point,
        period_start,
        period_end,
        skip_advance_ids=skip_bucket.get("advance_ids"),
    )
    recover_collection_point_previous_outstanding_for_period(
        collection_point, period_start, period_end
    )
    settle_collection_point_goods_for_period(collection_point, period_start, period_end)
    loan_settings = cp_loan_deduction_settings_map(
        [collection_point.id], period_start, period_end
    )
    apply_collection_point_loan_deductions_for_period(
        collection_point,
        period_start,
        period_end,
        skip_bucket=skip_bucket,
        deduct_all=loan_settings.get(collection_point.id, True),
        settled_on=settled_on,
        source_period_payment=source_period_payment,
        payment_note=payment_note,
    )


@transaction.atomic
def record_farmer_period_payment(
    *,
    farmer,
    period_start,
    period_end,
    amount,
    expected_amount,
    recorded_by,
    branch_ids,
    note="",
    payment_method=None,
    bank_account=None,
    payment_date=None,
):
    if FarmerPeriodPayment.objects.filter(
        farmer=farmer,
        period_start=period_start,
        period_end=period_end,
    ).exists():
        raise ValidationError("Payment already recorded for this farmer and period.")

    pay_amount = Decimal(amount).quantize(Decimal("0.01"))
    if pay_amount <= 0:
        raise ValidationError("Payment amount must be greater than zero.")

    from .services import (
        farmer_balance_for_period,
        farmer_payment_snapshot_fields,
        refresh_farmer_payment_sheet_record,
    )

    balance = farmer_balance_for_period(
        farmer.id, branch_ids, period_start, period_end
    )
    if balance <= 0:
        raise ValidationError(
            "Nothing remaining to pay for this farmer in the selected period."
        )

    snapshot = farmer_payment_snapshot_fields(
        farmer, branch_ids, period_start, period_end
    )

    payment = FarmerPeriodPayment.objects.create(
        farmer=farmer,
        period_start=period_start,
        period_end=period_end,
        amount=pay_amount,
        expected_amount=Decimal(expected_amount).quantize(Decimal("0.01")),
        rate=snapshot["rate"],
        apply_rate_paid=snapshot["apply_rate_paid"],
        total_liters=snapshot["total_liters"],
        gross_amount=snapshot["gross_amount"],
        advance_amount=snapshot["advance_amount"],
        goods_deduction=snapshot["goods_deduction"],
        loan_deduction=snapshot["loan_deduction"],
        net_amount=snapshot["net_amount"],
        note=(note or "").strip(),
        branch_ids=[int(b) for b in branch_ids if str(b).isdigit()],
        recorded_by=recorded_by,
        paid_at=paid_at_for_payment_date(payment_date),
        **_farmer_payment_method_fields(
            farmer,
            payment_method=payment_method,
            bank_account=bank_account,
        ),
    )
    apply_farmer_period_settlements(farmer, period_start, period_end)
    mark_farmer_milk_collections_paid(
        farmer,
        period_start,
        period_end,
        branch_ids,
        paid_at=payment.paid_at,
    )
    refresh_farmer_payment_sheet_record(period_start, period_end, branch_ids)
    return payment


def record_farmer_bulk_period_payments(
    *,
    farmers,
    period_start,
    period_end,
    branch_ids,
    recorded_by,
    note="",
    payment_method=None,
    payment_date=None,
):
    """Record full balance payments for multiple farmers in one request."""
    from .services import farmer_balance_for_period

    paid = []
    skipped = []
    failed = []
    shared_note = (note or "").strip()
    for farmer in farmers:
        display_name = farmer.common_name or farmer.full_name or str(farmer.pk)
        if FarmerPeriodPayment.objects.filter(
            farmer=farmer,
            period_start=period_start,
            period_end=period_end,
        ).exists():
            skipped.append((display_name, "Payment already recorded for this period."))
            continue
        try:
            expected = farmer_balance_for_period(
                farmer.id, branch_ids, period_start, period_end
            )
            balance = Decimal(expected).quantize(Decimal("0.01"))
            if balance <= 0:
                skipped.append((display_name, "Nothing remaining to pay."))
                continue
            method = payment_method
            if method is None:
                method = (
                    FarmerPeriodPayment.PaymentMethod.BANK_TRANSFER
                    if farmer.primary_bank_account
                    else FarmerPeriodPayment.PaymentMethod.CASH
                )
            record_farmer_period_payment(
                farmer=farmer,
                period_start=period_start,
                period_end=period_end,
                amount=balance,
                expected_amount=expected,
                recorded_by=recorded_by,
                branch_ids=branch_ids,
                note=shared_note,
                payment_method=method,
                payment_date=payment_date,
            )
            paid.append(display_name)
        except ValidationError as exc:
            msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            failed.append((display_name, msg))
    return {"paid": paid, "skipped": skipped, "failed": failed}


@transaction.atomic
def record_collection_point_bulk_period_payments(
    *,
    collection_points,
    period_start,
    period_end,
    branch_ids,
    recorded_by,
    note="",
    settle_deductions=True,
    payment_method=None,
    payment_date=None,
):
    """Record full balance payments for multiple collection points in one request."""
    from .services import collection_point_pending_payable_amount

    paid = []
    skipped = []
    failed = []
    shared_note = (note or "").strip()
    for point in collection_points:
        display_name = f"{point.number} — {point.name}"
        if CollectionPointPeriodPayment.objects.filter(
            collection_point=point,
            period_start=period_start,
            period_end=period_end,
        ).exists():
            skipped.append((display_name, "Payment already recorded for this period."))
            continue
        try:
            pending = collection_point_pending_payable_amount(
                point,
                branch_ids,
                period_start,
                period_end,
                balance_payment=settle_deductions,
            )
            if pending > Decimal("0"):
                expected = pending
                amount = pending
            elif pending < Decimal("0") and settle_deductions:
                expected = Decimal("0.00")
                amount = Decimal("0.00")
            else:
                skipped.append((display_name, "Nothing remaining to pay."))
                continue
            record_collection_point_period_payment(
                collection_point=point,
                period_start=period_start,
                period_end=period_end,
                amount=amount,
                expected_amount=expected,
                recorded_by=recorded_by,
                branch_ids=branch_ids,
                note=shared_note,
                settle_deductions=settle_deductions,
                payment_method=payment_method,
                payment_date=payment_date,
            )
            paid.append(display_name)
        except ValidationError as exc:
            msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            failed.append((display_name, msg))
    return {"paid": paid, "skipped": skipped, "failed": failed}


@transaction.atomic
def record_collection_point_period_payment(
    *,
    collection_point,
    period_start,
    period_end,
    amount,
    expected_amount,
    recorded_by,
    branch_ids,
    note="",
    settle_deductions=False,
    payment_method=None,
    bank_account=None,
    payment_date=None,
):
    if CollectionPointPeriodPayment.objects.filter(
        collection_point=collection_point,
        period_start=period_start,
        period_end=period_end,
    ).exists():
        raise ValidationError("Payment already recorded for this collection point and period.")

    pay_amount = Decimal(amount).quantize(Decimal("0.01"))

    from .services import (
        collection_point_has_zero_cash_settlement_items,
        collection_point_balance_for_period,
        collection_point_payment_snapshot_fields,
        collection_point_pending_payable_amount,
        refresh_collection_point_payment_sheet_record,
    )

    snapshot = collection_point_payment_snapshot_fields(
        collection_point, branch_ids, period_start, period_end
    )
    allow_zero_settlement = settle_deductions and collection_point_has_zero_cash_settlement_items(
        advance_amount=snapshot.get("advance_amount"),
        goods_deduction=snapshot.get("goods_deduction"),
        loan_deduction=snapshot.get("loan_deduction"),
        previous_outstanding=snapshot.get("previous_outstanding_amount"),
    )
    pending = collection_point_pending_payable_amount(
        collection_point,
        branch_ids,
        period_start,
        period_end,
        balance_payment=settle_deductions,
    )
    carry_forward_amount = Decimal("0.00")
    if pending > Decimal("0"):
        if pay_amount <= 0:
            raise ValidationError("Payment amount must be greater than zero.")
        balance = collection_point_balance_for_period(
            collection_point,
            branch_ids,
            period_start,
            period_end,
            balance_payment=settle_deductions,
        )
        if balance <= 0:
            raise ValidationError(
                "Nothing remaining to pay for this collection point in the selected period."
            )
    elif pending < Decimal("0") and settle_deductions:
        if pay_amount < Decimal("0"):
            raise ValidationError("Payment amount cannot be negative.")
        pay_amount = Decimal("0.00")
        carry_forward_amount = (-pending).quantize(Decimal("0.01"))
    elif pending == Decimal("0.00") and allow_zero_settlement:
        if pay_amount != Decimal("0.00"):
            raise ValidationError("Payment amount must be zero for this settlement.")
        pay_amount = Decimal("0.00")
    else:
        raise ValidationError(
            "Nothing remaining to pay for this collection point in the selected period."
        )

    payment_note = (note or "").strip()
    if carry_forward_amount > Decimal("0"):
        carry_note = (
            f"Carried forward {carry_forward_amount} as previous outstanding."
        )
        payment_note = f"{payment_note} {carry_note}".strip() if payment_note else carry_note

    payment = CollectionPointPeriodPayment.objects.create(
        collection_point=collection_point,
        period_start=period_start,
        period_end=period_end,
        amount=pay_amount,
        expected_amount=Decimal(expected_amount).quantize(Decimal("0.01")),
        rate=snapshot["rate"],
        collector_fee_rate=snapshot["collector_fee_rate"],
        additional_rate=snapshot["additional_rate"],
        total_quantity=snapshot["total_quantity"],
        gross_amount=snapshot["gross_amount"],
        advance_amount=snapshot["advance_amount"],
        goods_deduction=snapshot["goods_deduction"],
        loan_deduction=snapshot["loan_deduction"],
        collector_fee_amount=snapshot["collector_fee_amount"],
        additional_amount=snapshot["additional_amount"],
        previous_outstanding_amount=snapshot.get("previous_outstanding_amount"),
        payment_correction_amount=snapshot.get("payment_correction_amount"),
        net_amount=snapshot["net_amount"],
        note=payment_note[:255],
        branch_ids=[int(b) for b in branch_ids if str(b).isdigit()],
        recorded_by=recorded_by,
        paid_at=paid_at_for_payment_date(payment_date),
        **_collection_point_payment_method_fields(
            collection_point,
            payment_method=payment_method,
            bank_account=bank_account,
        ),
    )
    if settle_deductions:
        apply_collection_point_period_settlements(
            collection_point,
            period_start,
            period_end,
            branch_ids,
            settled_on=(payment.paid_at.date() if payment.paid_at else timezone.localdate()),
            source_period_payment=payment,
            payment_note=payment.note,
        )
    mark_collection_point_milk_collections_paid(
        collection_point,
        period_start,
        period_end,
        branch_ids,
        paid_at=payment.paid_at,
    )
    if carry_forward_amount > Decimal("0"):
        create_collection_point_previous_outstanding(
            collection_point=collection_point,
            period_start=period_start,
            period_end=period_end,
            amount=carry_forward_amount,
            recorded_by=recorded_by,
            originated_payment=payment,
        )
    refresh_collection_point_payment_sheet_record(period_start, period_end, branch_ids)
    return payment
