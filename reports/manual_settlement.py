from datetime import datetime, timedelta
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from farmer_loans.models import FarmerLoan, FarmerLoanRepaymentSchedule
from masters.models import FarmerAdvancePayment
from suppliers.models import FarmerGoodsIssue

from .models import CollectionPointPaymentPeriodDeductionSkip


def parse_settlement_date(raw):
    if not raw or not str(raw).strip():
        return timezone.localdate()
    try:
        return datetime.strptime(str(raw).strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValidationError("Enter a valid date (YYYY-MM-DD).") from exc


def parse_settlement_note(raw):
    return (raw or "").strip()[:255]


def settlement_datetime_from_date(value):
    return timezone.make_aware(datetime.combine(value, datetime.min.time()))


def settlement_from_request(request):
    settlement_date = parse_settlement_date(request.POST.get("settlement_date"))
    settlement_note = parse_settlement_note(request.POST.get("settlement_note"))
    return settlement_date, settlement_note


def clear_collection_point_advance_skips_from(advance, *, from_date):
    """Remove skip flags so a remaining balance can deduct again on open sheets."""
    CollectionPointPaymentPeriodDeductionSkip.objects.filter(
        collection_point_id=advance.collection_point_id,
        kind=CollectionPointPaymentPeriodDeductionSkip.Kind.ADVANCE,
        reference_id=advance.id,
        period_end__gte=from_date,
    ).delete()


@transaction.atomic
def set_advance_paid_on(advance, *, paid_on, recovery_note=None):
    recovered_amount = Decimal(getattr(advance, "recovered_amount", 0) or 0)
    if not getattr(advance, "recovered_at", None) and recovered_amount <= 0:
        raise ValidationError("This advance has no payment date to update yet.")
    if advance.date and paid_on < advance.date:
        raise ValidationError("Paid on date cannot be before the advance date.")
    advance.recovered_at = settlement_datetime_from_date(paid_on)
    update_fields = ["recovered_at"]
    remaining = (Decimal(advance.amount or 0) - recovered_amount).quantize(Decimal("0.01"))
    if remaining > 0 and hasattr(advance, "available_on"):
        next_available = paid_on + timedelta(days=1)
        if advance.date and next_available < advance.date:
            next_available = advance.date
        advance.available_on = next_available
        update_fields.append("available_on")
    if recovery_note is not None:
        advance.recovery_note = recovery_note
        update_fields.append("recovery_note")
    advance.save(update_fields=update_fields)
    if remaining > 0 and hasattr(advance, "available_on"):
        clear_collection_point_advance_skips_from(advance, from_date=advance.available_on)
    return advance


@transaction.atomic
def set_advance_recovered(advance, *, recovered: bool, recovered_at=None, recovery_note=None):
    update_fields = ["is_recovered", "recovered_at", "recovery_note"]
    if recovered:
        if advance.is_recovered:
            return advance
        advance.is_recovered = True
        advance.recovered_at = recovered_at or timezone.now()
        advance.recovery_note = recovery_note or ""
        if hasattr(advance, "recovered_amount"):
            advance.recovered_amount = Decimal(advance.amount or 0).quantize(Decimal("0.01"))
            update_fields.append("recovered_amount")
        advance.save(update_fields=update_fields)
        return advance
    if not advance.is_recovered:
        return advance
    advance.is_recovered = False
    advance.recovered_at = None
    advance.recovery_note = ""
    if hasattr(advance, "recovered_amount"):
        advance.recovered_amount = Decimal("0.00")
        update_fields.append("recovered_amount")
    advance.save(update_fields=update_fields)
    return advance


def _goods_issues_for_batch(batch_ref):
    return list(
        FarmerGoodsIssue.objects.filter(batch_ref=batch_ref).select_related("from_branch")
    )


@transaction.atomic
def record_goods_receipt_payment(
    batch_ref,
    *,
    action,
    line_ids=None,
    settled_at=None,
    settlement_note=None,
):
    issues = _goods_issues_for_batch(batch_ref)
    if not issues:
        raise ValidationError("Issue receipt not found.")
    head = issues[0]
    if head.issue_to_type == FarmerGoodsIssue.IssueToType.BRANCH:
        raise ValidationError("Branch transfer receipts cannot be paid.")
    if (
        head.issue_to_type == FarmerGoodsIssue.IssueToType.COLLECTION_POINT
        and head.driver_status != FarmerGoodsIssue.DriverStatus.ACCEPTED
        and action in ("full", "partial")
    ):
        if head.driver_status == FarmerGoodsIssue.DriverStatus.PENDING:
            raise ValidationError(
                "Wait for the route driver to accept this issue before recording payment."
            )
        if head.driver_status == FarmerGoodsIssue.DriverStatus.REJECTED:
            raise ValidationError("Rejected issues cannot be paid.")
        raise ValidationError("This issue is not eligible for payment yet.")
    issue_id_set = {issue.id for issue in issues}
    when = settled_at or timezone.now()
    note = settlement_note or ""

    if action == "full":
        updated = FarmerGoodsIssue.objects.filter(
            batch_ref=batch_ref,
            is_settled=False,
        ).update(is_settled=True, settled_at=when, settlement_note=note)
        return {"updated": updated, "action": action}

    if action == "partial":
        valid_ids = _valid_goods_line_ids(line_ids, issue_id_set)
        if not valid_ids:
            raise ValidationError("Select at least one line to pay.")
        qs = FarmerGoodsIssue.objects.filter(
            id__in=valid_ids,
            batch_ref=batch_ref,
            is_settled=False,
        )
        if not qs.exists():
            raise ValidationError("Selected lines are already paid or invalid.")
        updated = qs.update(is_settled=True, settled_at=when, settlement_note=note)
        return {"updated": updated, "action": action}

    if action == "unsettle_all":
        updated = FarmerGoodsIssue.objects.filter(
            batch_ref=batch_ref,
            is_settled=True,
        ).update(is_settled=False, settled_at=None, settlement_note="")
        return {"updated": updated, "action": action}

    if action == "unsettle_lines":
        valid_ids = _valid_goods_line_ids(line_ids, issue_id_set)
        if not valid_ids:
            raise ValidationError("Select at least one paid line to undo.")
        updated = FarmerGoodsIssue.objects.filter(
            id__in=valid_ids,
            batch_ref=batch_ref,
            is_settled=True,
        ).update(is_settled=False, settled_at=None, settlement_note="")
        return {"updated": updated, "action": action}

    raise ValidationError("Unknown payment action.")


def _valid_goods_line_ids(line_ids, issue_id_set):
    if not line_ids:
        return []
    valid = []
    for raw in line_ids:
        if str(raw).isdigit():
            pk = int(raw)
            if pk in issue_id_set:
                valid.append(pk)
    return valid


@transaction.atomic
def set_goods_batch_settled(batch_ref, *, settled: bool, settled_at=None, settlement_note=None):
    action = "full" if settled else "unsettle_all"
    result = record_goods_receipt_payment(
        batch_ref,
        action=action,
        settled_at=settled_at,
        settlement_note=settlement_note,
    )
    issues = _goods_issues_for_batch(batch_ref)
    head = issues[0] if issues else None
    return result["updated"], head


@transaction.atomic
def mark_loan_instalment_paid(schedule, *, paid_on=None, payment_note=None):
    if schedule.loan.status != FarmerLoan.Status.APPROVED:
        raise ValidationError("Only approved loan instalments can be settled.")
    pending = (
        (schedule.installment_amount or Decimal("0")) - (schedule.paid_amount or Decimal("0"))
    ).quantize(Decimal("0.01"))
    if pending <= 0:
        return schedule
    schedule.paid_amount = ((schedule.paid_amount or Decimal("0")) + pending).quantize(
        Decimal("0.01")
    )
    schedule.sync_paid_status(paid_on=paid_on)
    if payment_note is not None:
        schedule.payment_note = payment_note
    schedule.save(update_fields=["paid_amount", "is_paid", "paid_on", "payment_note"])
    loan = schedule.loan
    total_paid = sum(
        (amt or Decimal("0"))
        for amt in loan.repayment_schedules.values_list("paid_amount", flat=True)
    )
    loan.prior_paid_amount = total_paid.quantize(Decimal("0.01"))
    loan.save(update_fields=["prior_paid_amount", "updated_at"])
    return schedule


@transaction.atomic
def mark_loan_instalment_pending(schedule):
    if schedule.loan.status != FarmerLoan.Status.APPROVED:
        raise ValidationError("Only approved loan instalments can be updated.")
    if not schedule.is_paid and (schedule.paid_amount or Decimal("0")) <= 0:
        return schedule
    schedule.paid_amount = Decimal("0.00")
    schedule.payment_note = ""
    schedule.sync_paid_status()
    schedule.save(update_fields=["paid_amount", "is_paid", "paid_on", "payment_note"])
    loan = schedule.loan
    total_paid = sum(
        (amt or Decimal("0"))
        for amt in loan.repayment_schedules.values_list("paid_amount", flat=True)
    )
    loan.prior_paid_amount = total_paid.quantize(Decimal("0.01"))
    loan.save(update_fields=["prior_paid_amount", "updated_at"])
    return schedule


@transaction.atomic
def mark_collection_point_loan_instalment_paid(
    schedule, *, paid_on=None, payment_note=None, created_by=None
):
    from collection_point_loans.models import CollectionPointLoan
    from collection_point_loans.payment_ledger import record_collection_point_loan_payment

    if schedule.loan.status != CollectionPointLoan.Status.APPROVED:
        raise ValidationError("Only approved loan instalments can be settled.")
    pending = (
        (schedule.installment_amount or Decimal("0")) - (schedule.paid_amount or Decimal("0"))
    ).quantize(Decimal("0.01"))
    if pending <= 0:
        return schedule
    schedule.paid_amount = ((schedule.paid_amount or Decimal("0")) + pending).quantize(
        Decimal("0.01")
    )
    schedule.sync_paid_status(paid_on=paid_on)
    schedule.payment_method = schedule.PaymentMethod.DIRECT
    if payment_note is not None:
        schedule.payment_note = payment_note
    schedule.save(
        update_fields=["paid_amount", "is_paid", "paid_on", "payment_method", "payment_note"]
    )
    loan = schedule.loan
    record_collection_point_loan_payment(
        loan=loan,
        allocations=[(schedule, pending)],
        payment_date=schedule.paid_on,
        method=schedule.PaymentMethod.DIRECT,
        note=payment_note or "Direct loan settlement.",
        created_by=created_by,
    )
    total_paid = sum(
        (amt or Decimal("0"))
        for amt in loan.repayment_schedules.values_list("paid_amount", flat=True)
    )
    loan.prior_paid_amount = total_paid.quantize(Decimal("0.01"))
    loan.save(update_fields=["prior_paid_amount", "updated_at"])
    return schedule


@transaction.atomic
def mark_collection_point_loan_instalment_pending(schedule, *, created_by=None):
    from collection_point_loans.models import CollectionPointLoan
    from collection_point_loans.payment_ledger import record_collection_point_loan_payment

    if schedule.loan.status != CollectionPointLoan.Status.APPROVED:
        raise ValidationError("Only approved loan instalments can be updated.")
    if not schedule.is_paid and (schedule.paid_amount or Decimal("0")) <= 0:
        return schedule
    prior_paid = (schedule.paid_amount or Decimal("0.00")).quantize(Decimal("0.01"))
    schedule.paid_amount = Decimal("0.00")
    schedule.payment_method = ""
    schedule.payment_note = ""
    schedule.sync_paid_status()
    schedule.save(
        update_fields=["paid_amount", "is_paid", "paid_on", "payment_method", "payment_note"]
    )
    loan = schedule.loan
    record_collection_point_loan_payment(
        loan=loan,
        allocations=[(schedule, Decimal("0.00") - prior_paid)],
        payment_date=timezone.localdate(),
        method=schedule.PaymentMethod.DIRECT,
        note="Direct loan settlement undone.",
        created_by=created_by,
    )
    total_paid = sum(
        (amt or Decimal("0"))
        for amt in loan.repayment_schedules.values_list("paid_amount", flat=True)
    )
    loan.prior_paid_amount = total_paid.quantize(Decimal("0.01"))
    loan.save(update_fields=["prior_paid_amount", "updated_at"])
    return schedule
