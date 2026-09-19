from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import F, Q
from django.utils import timezone

from farmer_loans.models import FarmerLoan, FarmerLoanRepaymentSchedule
from collection_point_loans.models import CollectionPointLoan, CollectionPointLoanRepaymentSchedule
from masters.models import CollectionPointAdvancePayment, Farmer, FarmerAdvancePayment
from suppliers.models import FarmerGoodsIssue
from suppliers.services import batch_amount_totals_for_issues, priced_farmer_goods_issue_lines

from .models import (
    CollectionPointPaymentAdvanceDeduction,
    CollectionPointPaymentLoanDeduction,
    CollectionPointPaymentPeriodDeductionSkip,
    FarmerPaymentPeriodDeductionSkip,
)

ZERO = Decimal("0.00")


def _empty_skip_bucket():
    return {
        "advance_ids": set(),
        "goods_line_ids": set(),
        "goods_receipt_refs": set(),
        "loan_schedule_ids": set(),
    }


def payment_period_deduction_skip_map(farmer_ids, period_start, period_end):
    result = {int(fid): _empty_skip_bucket() for fid in farmer_ids}
    if not farmer_ids:
        return result
    for row in FarmerPaymentPeriodDeductionSkip.objects.filter(
        farmer_id__in=farmer_ids,
        period_start=period_start,
        period_end=period_end,
    ).only("farmer_id", "kind", "reference_id", "reference_key"):
        bucket = result.get(row.farmer_id)
        if bucket is None:
            continue
        if row.kind == FarmerPaymentPeriodDeductionSkip.Kind.ADVANCE:
            bucket["advance_ids"].add(int(row.reference_id))
        elif row.kind == FarmerPaymentPeriodDeductionSkip.Kind.GOODS_LINE:
            bucket["goods_line_ids"].add(int(row.reference_id))
        elif row.kind == FarmerPaymentPeriodDeductionSkip.Kind.GOODS_RECEIPT:
            if row.reference_key:
                bucket["goods_receipt_refs"].add(row.reference_key)
        elif row.kind == FarmerPaymentPeriodDeductionSkip.Kind.LOAN_INSTALMENT:
            bucket["loan_schedule_ids"].add(int(row.reference_id))
    return result


def goods_issue_is_skipped(issue, skip_bucket):
    if issue.batch_ref and issue.batch_ref in skip_bucket["goods_receipt_refs"]:
        return True
    return issue.id in skip_bucket["goods_line_ids"]


def set_deduction_skip(
    *,
    farmer,
    period_start,
    period_end,
    kind,
    skip,
    reference_id=0,
    reference_key="",
    updated_by=None,
):
    reference_id = int(reference_id or 0)
    reference_key = (reference_key or "").strip()
    lookup = {
        "farmer": farmer,
        "period_start": period_start,
        "period_end": period_end,
        "kind": kind,
        "reference_id": reference_id,
        "reference_key": reference_key,
    }
    if skip:
        FarmerPaymentPeriodDeductionSkip.objects.update_or_create(
            **lookup,
            defaults={"updated_by": updated_by},
        )
    else:
        FarmerPaymentPeriodDeductionSkip.objects.filter(**lookup).delete()


def loan_deduction_amount_map(
    farmer_ids,
    period_start,
    period_end,
    *,
    pending_only,
    skip_map,
    loan_settings_map,
):
    amounts = defaultdict(lambda: Decimal("0"))
    if not farmer_ids:
        return amounts
    schedule_filter = {
        "loan__farmer_id__in": farmer_ids,
        "loan__status": FarmerLoan.Status.APPROVED,
        "due_date__lte": period_end,
        "due_date__gte": period_start,
    }
    if pending_only:
        schedule_filter["is_paid"] = False
    qs = (
        FarmerLoanRepaymentSchedule.objects.filter(**schedule_filter)
        .select_related("loan")
        .order_by("due_date", "loan_id", "installment_number")
    )
    for schedule in qs:
        farmer_id = schedule.loan.farmer_id
        if not loan_settings_map.get(farmer_id, True):
            continue
        skips = skip_map.get(farmer_id, _empty_skip_bucket())
        if schedule.id in skips["loan_schedule_ids"]:
            continue
        if pending_only:
            amount = (
                (schedule.installment_amount or Decimal("0"))
                - (schedule.paid_amount or Decimal("0"))
            ).quantize(Decimal("0.01"))
        else:
            amount = (schedule.installment_amount or Decimal("0")).quantize(Decimal("0.01"))
        if amount > 0:
            amounts[farmer_id] += amount
    return amounts


def _collection_point_issue_id_candidates(point):
    id_candidates = {point.pk}
    try:
        id_candidates.add(int(str(point.number).strip()))
    except (ValueError, TypeError):
        pass
    return id_candidates


def collection_point_payment_period_deduction_skip_map(point_ids, period_start, period_end):
    result = {int(pid): _empty_skip_bucket() for pid in point_ids}
    if not point_ids:
        return result
    for row in CollectionPointPaymentPeriodDeductionSkip.objects.filter(
        collection_point_id__in=point_ids,
        period_start=period_start,
        period_end=period_end,
    ).only("collection_point_id", "kind", "reference_id", "reference_key"):
        bucket = result.get(row.collection_point_id)
        if bucket is None:
            continue
        if row.kind == CollectionPointPaymentPeriodDeductionSkip.Kind.ADVANCE:
            bucket["advance_ids"].add(int(row.reference_id))
        elif row.kind == CollectionPointPaymentPeriodDeductionSkip.Kind.GOODS_LINE:
            bucket["goods_line_ids"].add(int(row.reference_id))
        elif row.kind == CollectionPointPaymentPeriodDeductionSkip.Kind.GOODS_RECEIPT:
            if row.reference_key:
                bucket["goods_receipt_refs"].add(row.reference_key)
        elif row.kind == CollectionPointPaymentPeriodDeductionSkip.Kind.LOAN_INSTALMENT:
            bucket["loan_schedule_ids"].add(int(row.reference_id))
    return result


def collection_point_advance_remaining(advance):
    remaining = (
        Decimal(advance.amount or 0) - Decimal(getattr(advance, "recovered_amount", 0) or 0)
    ).quantize(Decimal("0.01"))
    return remaining if remaining > 0 else ZERO


def collection_point_advance_deduct_map(point_ids, period_start, period_end):
    result = {int(pid): {} for pid in point_ids}
    if not point_ids:
        return result
    for row in CollectionPointPaymentAdvanceDeduction.objects.filter(
        collection_point_id__in=point_ids,
        period_start=period_start,
        period_end=period_end,
    ).only("collection_point_id", "advance_id", "deduct_amount"):
        bucket = result.get(row.collection_point_id)
        if bucket is None:
            continue
        bucket[int(row.advance_id)] = Decimal(row.deduct_amount or 0).quantize(Decimal("0.01"))
    return result


def planned_collection_point_advance_deduct(advance_id, remaining, skip_ids, deduct_map):
    remaining = Decimal(remaining or 0).quantize(Decimal("0.01"))
    if remaining <= 0:
        return ZERO
    if advance_id in (skip_ids or set()):
        return ZERO
    if deduct_map and advance_id in deduct_map:
        amount = Decimal(deduct_map.get(advance_id) or 0).quantize(Decimal("0.01"))
        if amount < 0:
            amount = ZERO
        if amount > remaining:
            amount = remaining
        return amount
    return remaining


def collection_point_advances_available_for_period(point_id, start, end, *, pending_only=True):
    """Open collection-point advances for this sheet, including previous unpaid balances."""
    qs = CollectionPointAdvancePayment.objects.filter(
        collection_point_id=point_id,
        available_on__lte=end,
        date__lte=end,
        recovered_amount__lt=F("amount"),
    ).exclude(advance_type="outstanding")
    if pending_only:
        qs = qs.filter(is_recovered=False)
    return qs


def set_collection_point_advance_deduct(
    *,
    collection_point,
    period_start,
    period_end,
    advance,
    deduct_amount,
    updated_by=None,
):
    remaining = collection_point_advance_remaining(advance)
    deduct_amount = Decimal(deduct_amount or 0).quantize(Decimal("0.01"))
    if deduct_amount < 0:
        deduct_amount = ZERO
    if deduct_amount > remaining:
        deduct_amount = remaining
    skip = deduct_amount <= 0
    set_collection_point_deduction_skip(
        collection_point=collection_point,
        period_start=period_start,
        period_end=period_end,
        kind=CollectionPointPaymentPeriodDeductionSkip.Kind.ADVANCE,
        skip=skip,
        reference_id=advance.id,
        updated_by=updated_by,
    )
    lookup = {
        "collection_point": collection_point,
        "period_start": period_start,
        "period_end": period_end,
        "advance": advance,
    }
    if skip or deduct_amount == remaining:
        CollectionPointPaymentAdvanceDeduction.objects.filter(**lookup).delete()
    else:
        CollectionPointPaymentAdvanceDeduction.objects.update_or_create(
            **lookup,
            defaults={"deduct_amount": deduct_amount, "updated_by": updated_by},
        )
    return deduct_amount


def _point_advance_picker_payload(advance, *, remaining, deduct_amount, pending_only, start=None, end=None):
    remaining = Decimal(remaining or 0).quantize(Decimal("0.01"))
    deduct_amount = Decimal(deduct_amount or 0).quantize(Decimal("0.01"))
    original = Decimal(advance.amount or 0).quantize(Decimal("0.01"))
    paid_amount = Decimal(getattr(advance, "recovered_amount", 0) or 0).quantize(Decimal("0.01"))
    balance = (remaining - deduct_amount).quantize(Decimal("0.01"))
    if balance < 0:
        balance = ZERO
    display_amount = remaining if pending_only else deduct_amount or remaining
    paid_on = getattr(advance, "recovered_at", None)
    timing = "current"
    if start and advance.date and advance.date < start:
        timing = "previous"
    elif end and advance.date and advance.date > end:
        timing = "future"
    return {
        "id": advance.id,
        "date": advance.date.isoformat(),
        "amount": str(display_amount),
        "original_amount": str(original),
        "paid_amount": str(paid_amount),
        "remaining": str(remaining),
        "deduct_amount": str(deduct_amount),
        "balance_amount": str(balance),
        "paid_on": (
            timezone.localtime(paid_on).date().isoformat()
            if timezone.is_aware(paid_on)
            else paid_on.date().isoformat()
        )
        if paid_on
        else "",
        "timing": timing,
        "note": advance.note or "",
        "advance_type": advance.advance_type,
        "type_label": advance.get_advance_type_display(),
        "deduct": deduct_amount > 0,
        "allow_partial": True,
        "scope": "point",
        "owner_label": "Collection point",
    }


def collection_point_unpaid_loan_point_ids(point_ids):
    """Collection points with an approved loan that still has an unpaid instalment."""
    if not point_ids:
        return set()
    return set(
        CollectionPointLoanRepaymentSchedule.objects.filter(
            loan__collection_point_id__in=point_ids,
            loan__status=CollectionPointLoan.Status.APPROVED,
            is_paid=False,
            paid_amount__lt=F("installment_amount"),
        ).values_list("loan__collection_point_id", flat=True)
    )


def collection_point_loan_remaining(schedule):
    remaining = (
        Decimal(schedule.installment_amount or 0) - Decimal(schedule.paid_amount or 0)
    ).quantize(Decimal("0.01"))
    return remaining if remaining > 0 else ZERO


def collection_point_loan_deduct_map(point_ids, period_start, period_end):
    result = {int(pid): {} for pid in point_ids}
    if not point_ids:
        return result
    for row in CollectionPointPaymentLoanDeduction.objects.filter(
        collection_point_id__in=point_ids,
        period_start=period_start,
        period_end=period_end,
    ).only("collection_point_id", "schedule_id", "deduct_amount"):
        bucket = result.get(row.collection_point_id)
        if bucket is None:
            continue
        bucket[int(row.schedule_id)] = Decimal(row.deduct_amount or 0).quantize(Decimal("0.01"))
    return result


def planned_collection_point_loan_deduct(schedule_id, remaining, skip_ids, deduct_map):
    remaining = Decimal(remaining or 0).quantize(Decimal("0.01"))
    if remaining <= 0:
        return ZERO
    if schedule_id in (skip_ids or set()):
        return ZERO
    if deduct_map and schedule_id in deduct_map:
        amount = Decimal(deduct_map.get(schedule_id) or 0).quantize(Decimal("0.01"))
        if amount < 0:
            amount = ZERO
        if amount > remaining:
            amount = remaining
        return amount
    return remaining


def collection_point_loan_schedules_available_for_period(point_id, start, end, *, pending_only=True):
    """Open CP loan instalments for this sheet, including overdue and forwarded remainders."""
    qs = CollectionPointLoanRepaymentSchedule.objects.filter(
        loan__collection_point_id=point_id,
        loan__status=CollectionPointLoan.Status.APPROVED,
        available_on__lte=end,
    )
    if pending_only:
        qs = qs.filter(is_paid=False)
    return qs.select_related("loan").order_by("due_date", "loan_id", "installment_number")


def set_collection_point_loan_deduct(
    *,
    collection_point,
    period_start,
    period_end,
    schedule,
    deduct_amount,
    updated_by=None,
):
    remaining = collection_point_loan_remaining(schedule)
    deduct_amount = Decimal(deduct_amount or 0).quantize(Decimal("0.01"))
    if deduct_amount < 0:
        deduct_amount = ZERO
    if deduct_amount > remaining:
        deduct_amount = remaining
    skip = deduct_amount <= 0
    set_collection_point_deduction_skip(
        collection_point=collection_point,
        period_start=period_start,
        period_end=period_end,
        kind=CollectionPointPaymentPeriodDeductionSkip.Kind.LOAN_INSTALMENT,
        skip=skip,
        reference_id=schedule.id,
        updated_by=updated_by,
    )
    auto_available = bool(schedule.available_on and schedule.available_on <= period_end and remaining > 0)
    lookup = {
        "collection_point": collection_point,
        "period_start": period_start,
        "period_end": period_end,
        "schedule": schedule,
    }
    if auto_available and (skip or deduct_amount == remaining):
        CollectionPointPaymentLoanDeduction.objects.filter(**lookup).delete()
    else:
        CollectionPointPaymentLoanDeduction.objects.update_or_create(
            **lookup,
            defaults={"deduct_amount": deduct_amount, "updated_by": updated_by},
        )
    return deduct_amount


def _loan_timing_label(due_date, start, end):
    if due_date < start:
        return "previous"
    if due_date > end:
        return "future"
    return "current"


def _point_loan_picker_payload(schedule, *, remaining, deduct_amount, pending_only, start, end, deduct_all):
    remaining = Decimal(remaining or 0).quantize(Decimal("0.01"))
    deduct_amount = Decimal(deduct_amount or 0).quantize(Decimal("0.01"))
    original = Decimal(schedule.installment_amount or 0).quantize(Decimal("0.01"))
    if not deduct_all:
        deduct_amount = ZERO
    balance = (remaining - deduct_amount).quantize(Decimal("0.01"))
    if balance < 0:
        balance = ZERO
    display_amount = remaining if pending_only else deduct_amount or remaining
    return {
        "schedule_id": schedule.id,
        "loan_id": schedule.loan_id,
        "installment_number": schedule.installment_number,
        "due_date": schedule.due_date.isoformat(),
        "amount": str(display_amount),
        "original_amount": str(original),
        "remaining": str(remaining),
        "deduct_amount": str(deduct_amount),
        "balance_amount": str(balance),
        "deduct": deduct_amount > 0,
        "allow_partial": True,
        "timing": _loan_timing_label(schedule.due_date, start, end),
        "scope": "point",
        "owner_label": "Collection point",
    }


def cp_loan_deduction_amount_map(
    point_ids,
    period_start,
    period_end,
    *,
    pending_only,
    skip_map,
    loan_settings_map,
):
    amounts = defaultdict(lambda: Decimal("0"))
    if not point_ids:
        return amounts
    deduct_maps = collection_point_loan_deduct_map(point_ids, period_start, period_end)
    for point_id in point_ids:
        point_id = int(point_id)
        if not loan_settings_map.get(point_id, True):
            continue
        skips = skip_map.get(point_id, _empty_skip_bucket())
        skip_ids = skips.get("loan_schedule_ids", set())
        point_deducts = deduct_maps.get(point_id, {})
        seen = set()
        for schedule in collection_point_loan_schedules_available_for_period(
            point_id, period_start, period_end, pending_only=True
        ):
            remaining = collection_point_loan_remaining(schedule)
            deduct = planned_collection_point_loan_deduct(
                schedule.id, remaining, skip_ids, point_deducts
            )
            seen.add(schedule.id)
            if deduct > 0:
                amounts[point_id] += deduct
        extra_ids = set(point_deducts.keys()) - seen
        if extra_ids:
            extra_qs = CollectionPointLoanRepaymentSchedule.objects.filter(
                id__in=extra_ids,
                loan__collection_point_id=point_id,
            ).only("id", "installment_amount", "paid_amount")
            if pending_only:
                extra_qs = extra_qs.filter(is_paid=False)
            for schedule in extra_qs:
                remaining = collection_point_loan_remaining(schedule)
                deduct = planned_collection_point_loan_deduct(
                    schedule.id, remaining, skip_ids, point_deducts
                )
                if not pending_only:
                    deduct = Decimal(point_deducts.get(schedule.id) or 0).quantize(Decimal("0.01"))
                    if schedule.id in skip_ids:
                        deduct = ZERO
                if deduct > 0:
                    amounts[point_id] += deduct
        amounts[point_id] = amounts[point_id].quantize(Decimal("0.01"))
    return amounts


def set_collection_point_deduction_skip(
    *,
    collection_point,
    period_start,
    period_end,
    kind,
    skip,
    reference_id=0,
    reference_key="",
    updated_by=None,
):
    reference_id = int(reference_id or 0)
    reference_key = (reference_key or "").strip()
    lookup = {
        "collection_point": collection_point,
        "period_start": period_start,
        "period_end": period_end,
        "kind": kind,
        "reference_id": reference_id,
        "reference_key": reference_key,
    }
    if skip:
        CollectionPointPaymentPeriodDeductionSkip.objects.update_or_create(
            **lookup,
            defaults={"updated_by": updated_by},
        )
    else:
        CollectionPointPaymentPeriodDeductionSkip.objects.filter(**lookup).delete()


def _period_goods_issues_for_collection_point(point, start, end, pending_only):
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)
    goods_filter = {
        "issue_to_type": FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
        "issue_to_id__in": _collection_point_issue_id_candidates(point),
        "date__gte": start_dt,
        "date__lt": end_dt,
        "driver_status": FarmerGoodsIssue.DriverStatus.ACCEPTED,
    }
    if pending_only:
        goods_filter["is_settled"] = False
    return list(
        FarmerGoodsIssue.objects.filter(**goods_filter)
        .select_related("product")
        .order_by("-date", "-id")
    )


def _build_goods_picker_rows(
    goods_issues, skip_bucket, *, scope, farmer_id=None, farmer_name="", deducted_if_settled=False
):
    receipts_map = defaultdict(list)
    standalone_lines = []
    for issue in goods_issues:
        priced_lines, _total = priced_farmer_goods_issue_lines([issue])
        if not priced_lines:
            continue
        row = priced_lines[0]
        line_amount = row["line_amount"].quantize(Decimal("0.01"))
        if line_amount <= 0:
            continue
        deducted = (
            bool(issue.is_settled)
            if deducted_if_settled
            else not goods_issue_is_skipped(issue, skip_bucket)
        )
        line_payload = {
            "id": issue.id,
            "date": issue.date.isoformat(),
            "product_name": issue.product.name,
            "quantity": str(row["quantity"].quantize(Decimal("0.01"))),
            "unit_price": str(row["unit_price"].quantize(Decimal("0.01"))),
            "amount": str(line_amount),
            "batch_ref": issue.batch_ref or "",
            "deduct": deducted,
            "scope": scope,
            "farmer_id": farmer_id,
            "farmer_name": farmer_name or "",
        }
        if issue.batch_ref:
            receipts_map[issue.batch_ref].append(line_payload)
        else:
            standalone_lines.append(line_payload)

    batch_totals = batch_amount_totals_for_issues(
        [issue for issue in goods_issues if issue.batch_ref]
    )
    goods_receipts = []
    for batch_ref, lines in sorted(receipts_map.items(), key=lambda item: item[1][0]["date"], reverse=True):
        receipt_skipped = batch_ref in skip_bucket["goods_receipt_refs"]
        amount = batch_totals.get(batch_ref, Decimal("0"))
        if amount <= 0:
            amount = sum(Decimal(line["amount"]) for line in lines)
        goods_receipts.append(
            {
                "batch_ref": batch_ref,
                "date": lines[0]["date"],
                "amount": str(amount.quantize(Decimal("0.01"))),
                "deduct": (
                    any(line["deduct"] for line in lines)
                    if deducted_if_settled
                    else not receipt_skipped
                ),
                "scope": scope,
                "farmer_id": farmer_id,
                "farmer_name": farmer_name or "",
                "lines": lines,
            }
        )
    return goods_receipts, standalone_lines


def collection_point_payment_deduction_picker_data(
    point,
    farmer_ids,
    start,
    end,
    cp_skip_map,
    farmer_skip_map,
    loan_settings_map,
    cp_loan_settings_map,
    pending_only=True,
):
    """Advances, goods, and loans for a collection point payment row (direct + linked farmers)."""
    cp_skips = cp_skip_map.get(point.id, _empty_skip_bucket())
    deduct_cp_loan_all = cp_loan_settings_map.get(point.id, True)
    farmers = {
        farmer.id: farmer
        for farmer in Farmer.objects.filter(id__in=farmer_ids).only(
            "id", "full_name", "common_name"
        )
    }

    advances = []
    goods_receipts = []
    goods_lines = []
    loan_instalments = []
    addable_loan_instalments = []

    point_deducts = collection_point_advance_deduct_map([point.id], start, end).get(point.id, {})
    if pending_only:
        point_advances = collection_point_advances_available_for_period(
            point.id, start, end, pending_only=True
        ).order_by("-date", "-id")
    elif point_deducts:
        point_advances = CollectionPointAdvancePayment.objects.filter(
            id__in=point_deducts.keys()
        ).order_by("-date", "-id")
    else:
        point_advances = (
            CollectionPointAdvancePayment.objects.filter(
                collection_point_id=point.id,
                date__gte=start,
                date__lte=end,
            )
            .exclude(advance_type="outstanding")
            .order_by("-date", "-id")
        )
    for advance in point_advances:
        leftover = collection_point_advance_remaining(advance)
        if pending_only and leftover <= 0:
            continue
        if pending_only:
            deduct_amount = planned_collection_point_advance_deduct(
                advance.id, leftover, cp_skips["advance_ids"], point_deducts
            )
            available = leftover
        elif point_deducts:
            deduct_amount = Decimal(point_deducts.get(advance.id) or 0).quantize(Decimal("0.01"))
            available = leftover + deduct_amount
        else:
            deduct_amount = (
                Decimal(advance.amount or 0).quantize(Decimal("0.01"))
                if advance.is_recovered
                else ZERO
            )
            available = leftover + deduct_amount
        advances.append(
            _point_advance_picker_payload(
                advance,
                remaining=available,
                deduct_amount=deduct_amount,
                pending_only=pending_only,
                start=start,
                end=end,
            )
        )

    direct_issues = _period_goods_issues_for_collection_point(
        point, start, end, pending_only=pending_only
    )
    direct_receipts, direct_lines = _build_goods_picker_rows(
        direct_issues,
        cp_skips,
        scope="point",
        deducted_if_settled=not pending_only,
    )
    goods_receipts.extend(direct_receipts)
    goods_lines.extend(direct_lines)

    addable_loan_instalments = []
    point_loan_deducts = collection_point_loan_deduct_map([point.id], start, end).get(point.id, {})
    listed_schedule_ids = set()
    if pending_only:
        point_schedules = list(
            collection_point_loan_schedules_available_for_period(
                point.id, start, end, pending_only=True
            )
        )
        extra_ids = set(point_loan_deducts.keys()) - {schedule.id for schedule in point_schedules}
        if extra_ids:
            point_schedules.extend(
                CollectionPointLoanRepaymentSchedule.objects.filter(
                    id__in=extra_ids,
                    loan__collection_point=point,
                    is_paid=False,
                ).select_related("loan")
            )
    elif point_loan_deducts:
        point_schedules = list(
            CollectionPointLoanRepaymentSchedule.objects.filter(id__in=point_loan_deducts.keys())
            .select_related("loan")
            .order_by("due_date", "loan_id", "installment_number")
        )
    else:
        point_schedules = list(
            CollectionPointLoanRepaymentSchedule.objects.filter(
                loan__collection_point=point,
                loan__status=CollectionPointLoan.Status.APPROVED,
                due_date__gte=start,
                due_date__lte=end,
            )
            .select_related("loan")
            .order_by("due_date", "loan_id", "installment_number")
        )
    point_schedules.sort(key=lambda row: (row.due_date, row.loan_id, row.installment_number))
    for schedule in point_schedules:
        leftover = collection_point_loan_remaining(schedule)
        if pending_only and leftover <= 0 and schedule.id not in point_loan_deducts:
            continue
        if pending_only:
            deduct_amount = planned_collection_point_loan_deduct(
                schedule.id, leftover, cp_skips["loan_schedule_ids"], point_loan_deducts
            )
            available = leftover
        elif point_loan_deducts:
            deduct_amount = Decimal(point_loan_deducts.get(schedule.id) or 0).quantize(Decimal("0.01"))
            available = leftover + deduct_amount
        else:
            deduct_amount = leftover if schedule.is_paid else ZERO
            available = leftover + deduct_amount
        listed_schedule_ids.add(schedule.id)
        loan_instalments.append(
            _point_loan_picker_payload(
                schedule,
                remaining=available,
                deduct_amount=deduct_amount,
                pending_only=pending_only,
                start=start,
                end=end,
                deduct_all=deduct_cp_loan_all,
            )
        )

    if pending_only:
        for schedule in (
            CollectionPointLoanRepaymentSchedule.objects.filter(
                loan__collection_point=point,
                loan__status=CollectionPointLoan.Status.APPROVED,
                is_paid=False,
            )
            .exclude(id__in=listed_schedule_ids)
            .select_related("loan")
            .order_by("due_date", "loan_id", "installment_number")
        ):
            remaining = collection_point_loan_remaining(schedule)
            if remaining <= 0:
                continue
            timing = _loan_timing_label(schedule.due_date, start, end)
            timing_label = {"previous": "Previous", "future": "Future"}.get(timing, "Current")
            addable_loan_instalments.append(
                {
                    "schedule_id": schedule.id,
                    "loan_id": schedule.loan_id,
                    "installment_number": schedule.installment_number,
                    "due_date": schedule.due_date.isoformat(),
                    "remaining": str(remaining),
                    "timing": timing,
                    "label": (
                        f"{timing_label} · Loan #{schedule.loan_id} · Inst. "
                        f"{schedule.installment_number} · {schedule.due_date.isoformat()} · "
                        f"{remaining}"
                    ),
                    "scope": "point",
                    "allow_partial": True,
                }
            )

    if farmer_ids:
        farmer_advance_filter = {
            "farmer_id__in": farmer_ids,
            "date__gte": start,
            "date__lte": end,
        }
        if pending_only:
            farmer_advance_filter["is_recovered"] = False
        for advance in (
            FarmerAdvancePayment.objects.filter(**farmer_advance_filter).order_by("-date", "-id")
        ):
            farmer = farmers.get(advance.farmer_id)
            if farmer is None:
                continue
            farmer_name = farmer.common_name or farmer.full_name
            skips = farmer_skip_map.get(advance.farmer_id, _empty_skip_bucket())
            deducted = (
                advance.is_recovered
                if not pending_only
                else advance.id not in skips["advance_ids"]
            )
            amount = Decimal(advance.amount or 0).quantize(Decimal("0.01"))
            deduct_amount = amount if deducted else ZERO
            advances.append(
                {
                    "id": advance.id,
                    "date": advance.date.isoformat(),
                    "amount": str(amount),
                    "original_amount": str(amount),
                    "remaining": str(amount),
                    "deduct_amount": str(deduct_amount),
                    "balance_amount": str(ZERO if deducted else amount),
                    "note": advance.note or "",
                    "advance_type": advance.advance_type,
                    "type_label": advance.get_advance_type_display(),
                    "deduct": deducted,
                    "allow_partial": False,
                    "scope": "farmer",
                    "farmer_id": advance.farmer_id,
                    "farmer_name": farmer_name,
                    "owner_label": farmer_name,
                }
            )

        tz = timezone.get_current_timezone()
        start_dt = timezone.make_aware(datetime.combine(start, time.min), tz)
        end_dt = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)
        farmer_goods_filter = {
            "issue_to_type": FarmerGoodsIssue.IssueToType.FARMER,
            "issue_to_id__in": farmer_ids,
            "date__gte": start_dt,
            "date__lt": end_dt,
        }
        if pending_only:
            farmer_goods_filter["is_settled"] = False
        farmer_issues_by_id = defaultdict(list)
        for issue in (
            FarmerGoodsIssue.objects.filter(**farmer_goods_filter)
            .select_related("product")
            .order_by("-date", "-id")
        ):
            farmer_issues_by_id[issue.issue_to_id].append(issue)

        for farmer_id in sorted(farmer_issues_by_id):
            farmer = farmers.get(farmer_id)
            if farmer is None:
                continue
            farmer_name = farmer.common_name or farmer.full_name
            skips = farmer_skip_map.get(farmer_id, _empty_skip_bucket())
            farmer_receipts, farmer_lines = _build_goods_picker_rows(
                farmer_issues_by_id[farmer_id],
                skips,
                scope="farmer",
                farmer_id=farmer_id,
                farmer_name=farmer_name,
                deducted_if_settled=not pending_only,
            )
            goods_receipts.extend(farmer_receipts)
            goods_lines.extend(farmer_lines)

        farmer_schedule_filter = {
            "loan__farmer_id__in": farmer_ids,
            "loan__status": FarmerLoan.Status.APPROVED,
            "due_date__lte": end,
            "due_date__gte": start,
        }
        if pending_only:
            farmer_schedule_filter["is_paid"] = False
        for schedule in (
            FarmerLoanRepaymentSchedule.objects.filter(**farmer_schedule_filter)
            .select_related("loan")
            .order_by("due_date", "loan_id", "installment_number")
        ):
            farmer_id = schedule.loan.farmer_id
            farmer = farmers.get(farmer_id)
            if farmer is None:
                continue
            remaining = (
                (schedule.installment_amount or Decimal("0"))
                - (schedule.paid_amount or Decimal("0"))
            ).quantize(Decimal("0.01"))
            display_amount = (
                (schedule.installment_amount or Decimal("0")).quantize(Decimal("0.01"))
                if not pending_only
                else remaining
            )
            if display_amount <= 0:
                continue
            farmer_name = farmer.common_name or farmer.full_name
            skips = farmer_skip_map.get(farmer_id, _empty_skip_bucket())
            deduct_loan_all = loan_settings_map.get(farmer_id, True)
            skipped = schedule.id in skips["loan_schedule_ids"]
            deducted = (
                schedule.is_paid if not pending_only else deduct_loan_all and not skipped
            )
            loan_instalments.append(
                {
                    "schedule_id": schedule.id,
                    "loan_id": schedule.loan_id,
                    "installment_number": schedule.installment_number,
                    "due_date": schedule.due_date.isoformat(),
                    "amount": str(display_amount),
                    "original_amount": str(display_amount),
                    "remaining": str(display_amount if pending_only else remaining),
                    "deduct_amount": str(display_amount if deducted else ZERO),
                    "balance_amount": str(ZERO if deducted else display_amount),
                    "deduct": deducted,
                    "allow_partial": False,
                    "timing": _loan_timing_label(schedule.due_date, start, end),
                    "scope": "farmer",
                    "farmer_id": farmer_id,
                    "farmer_name": farmer_name,
                }
            )

    return {
        "advances": advances,
        "goods_receipts": goods_receipts,
        "goods_lines": goods_lines,
        "loan_instalments": loan_instalments,
        "addable_loan_instalments": addable_loan_instalments,
        "deduct_cp_loan_all": deduct_cp_loan_all,
    }


def _period_goods_issues_for_farmer(farmer_id, start, end, pending_only):
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)
    goods_filter = {
        "issue_to_type": FarmerGoodsIssue.IssueToType.FARMER,
        "issue_to_id": farmer_id,
        "date__gte": start_dt,
        "date__lt": end_dt,
    }
    if pending_only:
        goods_filter["is_settled"] = False
    return list(
        FarmerGoodsIssue.objects.filter(**goods_filter)
        .select_related("product")
        .order_by("-date", "-id")
    )


def farmer_payment_deduction_picker_data(farmer, start, end, skip_map, loan_settings_map):
    """Advance, goods, and loan rows for the payment-sheet deduction picker."""
    farmer_id = farmer.id
    skips = skip_map.get(farmer_id, _empty_skip_bucket())
    deduct_loan_all = loan_settings_map.get(farmer_id, True)

    advances = []
    advance_filter = {
        "farmer_id": farmer_id,
        "date__gte": start,
        "date__lte": end,
        "is_recovered": False,
    }
    for advance in FarmerAdvancePayment.objects.filter(**advance_filter).order_by("-date", "-id"):
        advances.append(
            {
                "id": advance.id,
                "date": advance.date.isoformat(),
                "amount": str(advance.amount.quantize(Decimal("0.01"))),
                "note": advance.note or "",
                "advance_type": advance.advance_type,
                "type_label": advance.get_advance_type_display(),
                "deduct": advance.id not in skips["advance_ids"],
            }
        )

    goods_issues = _period_goods_issues_for_farmer(farmer_id, start, end, pending_only=True)
    goods_receipts, standalone_lines = _build_goods_picker_rows(
        goods_issues,
        skips,
        scope="farmer",
        farmer_id=farmer_id,
        farmer_name=farmer.common_name or farmer.full_name,
    )

    loan_instalments = []
    schedule_filter = {
        "loan__farmer": farmer,
        "loan__status": FarmerLoan.Status.APPROVED,
        "due_date__lte": end,
        "due_date__gte": start,
        "is_paid": False,
    }
    for schedule in (
        FarmerLoanRepaymentSchedule.objects.filter(**schedule_filter)
        .select_related("loan")
        .order_by("due_date", "loan_id", "installment_number")
    ):
        pending = (
            (schedule.installment_amount or Decimal("0")) - (schedule.paid_amount or Decimal("0"))
        ).quantize(Decimal("0.01"))
        if pending <= 0:
            continue
        skipped = schedule.id in skips["loan_schedule_ids"]
        loan_instalments.append(
            {
                "schedule_id": schedule.id,
                "loan_id": schedule.loan_id,
                "installment_number": schedule.installment_number,
                "due_date": schedule.due_date.isoformat(),
                "amount": str(pending),
                "deduct": deduct_loan_all and not skipped,
            }
        )

    return {
        "advances": advances,
        "goods_receipts": goods_receipts,
        "goods_lines": standalone_lines,
        "loan_instalments": loan_instalments,
        "deduct_loan_all": deduct_loan_all,
    }
