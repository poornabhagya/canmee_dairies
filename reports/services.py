from datetime import datetime, time, timedelta
from decimal import Decimal
from collections import defaultdict
import json
import re

from django.db.models import Avg, Case, CharField, F, Q, Sum, Value, When
from django.utils import timezone

from canmee_dairies.constants import MILK_LITER_FACTOR
from farmer_loans.models import FarmerLoan, FarmerLoanRepaymentSchedule
from branches.models import Branch
from masters.models import (
    AdvancePaymentType,
    Farmer,
    FarmerAdvancePayment,
    FarmerRateHistory,
    CollectionPoint,
    CollectionPointAdvancePayment,
)
from milk_collections.models import CollectionSource, MilkCollection, MilkFactor
from dispatch.models import MilkDistribution
from suppliers.models import FarmerGoodsIssue
from suppliers.services import summarize_priced_farmer_goods_issues
from .models import (
    CollectionPointPeriodPayment,
    CollectionPointPaymentCorrection,
    CollectionPointPaymentSheetRecord,
    CollectionPointPreviousOutstanding,
    FarmerPeriodPayment,
    FarmerPaymentSheetRecord,
)
from .deduction_skips import (
    collection_point_advance_deduct_map,
    collection_point_advance_remaining,
    collection_point_advances_available_for_period,
    collection_point_payment_period_deduction_skip_map,
    collection_point_unpaid_loan_point_ids,
    cp_loan_deduction_amount_map,
    goods_issue_is_skipped,
    loan_deduction_amount_map,
    payment_period_deduction_skip_map,
    planned_collection_point_advance_deduct,
)
from .payslip_labels import (
    collection_point_payslip_display_name,
    farmer_payslip_display_name,
    payment_sheet_period_english,
    payment_sheet_period_sinhala,
)


def latest_farmer_rate_map(farmer_ids):
    latest_rows = {}
    for row in FarmerRateHistory.objects.filter(farmer_id__in=farmer_ids).order_by(
        "farmer_id", "-effective_at", "-id"
    ):
        if row.farmer_id not in latest_rows:
            latest_rows[row.farmer_id] = row
    return latest_rows


def payment_sheet_branch_key(branch_ids):
    ids = sorted({int(b) for b in (branch_ids or []) if str(b).isdigit()})
    return ",".join(str(branch_id) for branch_id in ids)


def payment_qty_for_branch(branch, *, kg=None, liters=None):
    """Milk quantity for (qty × rate), using the branch collection unit (Kg or Liters)."""
    kg_qty = Decimal(str(kg or 0))
    liters_qty = Decimal(str(liters or 0))
    if branch is not None and getattr(branch, "collection_unit", None) == Branch.CollectionUnit.LITERS:
        return liters_qty.quantize(Decimal("0.01"))
    return kg_qty.quantize(Decimal("0.01"))


def farmer_payment_gross(branch, rate, *, kg=None, liters=None, apply_rate_paid="yes"):
    """Gross milk value: branch collection-unit qty × rate (when apply_rate_paid is yes)."""
    if (apply_rate_paid or "yes") != "yes":
        return Decimal("0.00")
    qty = payment_qty_for_branch(branch, kg=kg, liters=liters)
    return (qty * Decimal(str(rate or 0))).quantize(Decimal("0.01"))


def advance_is_bank_loan(advance):
    return getattr(advance, "advance_type", None) == AdvancePaymentType.BANK_LOAN


def split_advance_amounts(amount, advance_type):
    """Split an advance into (advance payment, bank loan deduction) buckets."""
    qty = Decimal(amount or 0)
    if advance_type == AdvancePaymentType.BANK_LOAN:
        return Decimal("0"), qty
    if advance_type == "outstanding":
        return Decimal("0"), Decimal("0")
    return qty, Decimal("0")


def collection_point_period_net_amount(
    *,
    gross_amount=Decimal("0"),
    advance_amount=Decimal("0"),
    goods_deduction=Decimal("0"),
    loan_deduction=Decimal("0"),
    previous_outstanding=Decimal("0"),
    collector_fee_amount=Decimal("0"),
    additional_amount=Decimal("0"),
    payment_correction=Decimal("0"),
):
    return (
        Decimal(gross_amount or 0)
        - Decimal(advance_amount or 0)
        - Decimal(goods_deduction or 0)
        - Decimal(loan_deduction or 0)
        - Decimal(previous_outstanding or 0)
        + Decimal(collector_fee_amount or 0)
        + Decimal(additional_amount or 0)
        + Decimal(payment_correction or 0)
    ).quantize(Decimal("0.01"))


def collection_point_previous_outstanding_map(
    point_ids,
    as_of_end,
    *,
    pending_only=True,
    period_start=None,
    period_end=None,
):
    """Outstanding amounts deductible on a payment sheet as of ``as_of_end``."""
    amounts = defaultdict(lambda: Decimal("0"))
    if not point_ids or not as_of_end:
        return amounts
    qs = CollectionPointPreviousOutstanding.objects.filter(
        collection_point_id__in=list(point_ids),
        available_on__lte=as_of_end,
    )
    if pending_only:
        qs = qs.filter(is_recovered=False)
    elif period_start and period_end:
        qs = qs.filter(
            Q(is_recovered=False)
            | Q(
                is_recovered=True,
                recovered_period_start=period_start,
                recovered_period_end=period_end,
            )
        )
    for row in qs.values("collection_point_id").annotate(total=Sum("amount")):
        amounts[row["collection_point_id"]] = (row["total"] or Decimal("0")).quantize(
            Decimal("0.01")
        )
    return amounts


def collection_point_previous_outstanding_amount(
    point_id, as_of_end, *, pending_only=True, period_start=None, period_end=None
):
    return collection_point_previous_outstanding_map(
        [point_id],
        as_of_end,
        pending_only=pending_only,
        period_start=period_start,
        period_end=period_end,
    ).get(point_id, Decimal("0.00"))


def collection_point_payment_correction_map(point_ids, period_start, period_end):
    """Manual payment corrections entered on the collection point payment sheet."""
    amounts = defaultdict(lambda: Decimal("0"))
    if not point_ids or not period_start or not period_end:
        return amounts
    for row in CollectionPointPaymentCorrection.objects.filter(
        collection_point_id__in=list(point_ids),
        period_start=period_start,
        period_end=period_end,
    ).values("collection_point_id", "amount"):
        amounts[row["collection_point_id"]] = (row["amount"] or Decimal("0")).quantize(
            Decimal("0.01")
        )
    return amounts


def collection_point_payment_correction_amount(point_id, period_start, period_end):
    return collection_point_payment_correction_map(
        [point_id], period_start, period_end
    ).get(point_id, Decimal("0.00"))


def set_collection_point_payment_correction(
    point,
    period_start,
    period_end,
    amount,
    *,
    updated_by=None,
):
    """Save or clear a manual payment correction for one collection point period."""
    correction = Decimal(amount or 0).quantize(Decimal("0.01"))
    if correction == Decimal("0"):
        CollectionPointPaymentCorrection.objects.filter(
            collection_point=point,
            period_start=period_start,
            period_end=period_end,
        ).delete()
        return Decimal("0.00")
    obj, _created = CollectionPointPaymentCorrection.objects.update_or_create(
        collection_point=point,
        period_start=period_start,
        period_end=period_end,
        defaults={
            "amount": correction,
            "updated_by": updated_by,
        },
    )
    return obj.amount.quantize(Decimal("0.01"))


def collection_point_additional_rate(point):
    return (point.additional or Decimal("0")).quantize(Decimal("0.01"))


def collection_point_additional_farmer_ids(point):
    """Farmer ids used for Additional qty. Empty selection = all farmers at the point."""
    selected = list(point.additional_farmers.values_list("id", flat=True))
    if selected:
        return selected
    return list(
        Farmer.objects.filter(collection_point_id=point.id).values_list("id", flat=True)
    )


def collection_point_additional_qty(point, branch_ids, start, end):
    """Milk qty (branch unit) from Additional-basis farmers for the period."""
    farmer_ids = collection_point_additional_farmer_ids(point)
    if not farmer_ids or not branch_ids or not start or not end:
        return Decimal("0.00")
    branch = point.route.branch if getattr(point, "route_id", None) else None
    total = Decimal("0")
    for row in (
        MilkCollection.objects.filter(
            source=CollectionSource.FARMER,
            farmer_id__in=farmer_ids,
            date__gte=start,
            date__lte=end,
            branch_id__in=branch_ids,
        ).select_related("branch")
    ):
        total += _collection_point_quantity(row, row.branch or branch)
    return total.quantize(Decimal("0.01"))


def collection_point_additional_amount(point, total_qty):
    rate = collection_point_additional_rate(point)
    return (Decimal(total_qty or 0) * rate).quantize(Decimal("0.01"))


def collection_point_additional_amount_for_period(point, branch_ids, start, end):
    """Additional = rate × selected/all farmer milk qty for the period."""
    qty = collection_point_additional_qty(point, branch_ids, start, end)
    return collection_point_additional_amount(point, qty), qty


def bulk_collection_point_additional_amounts(points, branch_ids, start, end):
    """One-pass Additional amounts for many collection points."""
    empty = Decimal("0.00")
    result = {point.id: empty for point in points}
    if not points or not branch_ids or not start or not end:
        return result

    point_ids = [point.id for point in points]
    selected_by_point = defaultdict(list)
    through = CollectionPoint.additional_farmers.through
    for pid, fid in through.objects.filter(collectionpoint_id__in=point_ids).values_list(
        "collectionpoint_id", "farmer_id"
    ):
        selected_by_point[pid].append(fid)
    farmers_at_point = defaultdict(list)
    for pid, fid in Farmer.objects.filter(collection_point_id__in=point_ids).values_list(
        "collection_point_id", "id"
    ):
        farmers_at_point[pid].append(fid)

    farmer_ids = set()
    farmers_for_point = {}
    for point in points:
        ids = selected_by_point.get(point.id) or farmers_at_point.get(point.id) or []
        farmers_for_point[point.id] = ids
        farmer_ids.update(ids)
    if not farmer_ids:
        return result

    qty_by_farmer = {
        row["farmer_id"]: (
            row["total_kg"] or Decimal("0"),
            row["total_liters"] or Decimal("0"),
        )
        for row in (
            MilkCollection.objects.filter(
                source=CollectionSource.FARMER,
                farmer_id__in=farmer_ids,
                date__gte=start,
                date__lte=end,
                branch_id__in=branch_ids,
                is_deleted=False,
            )
            .values("farmer_id")
            .annotate(total_kg=Sum("kg"), total_liters=Sum("liters"))
        )
    }

    for point in points:
        branch = point.route.branch if getattr(point, "route_id", None) else None
        use_liters = bool(branch and branch.collection_unit == Branch.CollectionUnit.LITERS)
        total = Decimal("0")
        for farmer_id in farmers_for_point.get(point.id, []):
            kg, liters = qty_by_farmer.get(farmer_id, (Decimal("0"), Decimal("0")))
            total += liters if use_liters else kg
        result[point.id] = collection_point_additional_amount(point, total)
    return result


def farmer_rate_at_datetime_map(farmer_ids, at):
    """Farmer rate and apply_rate_paid effective at a specific datetime."""
    if not farmer_ids:
        return {}
    farmers = {
        f.id: f
        for f in Farmer.objects.filter(id__in=farmer_ids).only("id", "rate", "apply_rate_paid")
    }
    result = {}
    for farmer_id in farmer_ids:
        row = (
            FarmerRateHistory.objects.filter(farmer_id=farmer_id, effective_at__lte=at)
            .order_by("-effective_at", "-id")
            .only("rate", "apply_rate_paid")
            .first()
        )
        if row:
            result[farmer_id] = (row.rate, row.apply_rate_paid or "yes")
            continue
        farmer = farmers.get(farmer_id)
        if farmer:
            result[farmer_id] = (farmer.rate, farmer.apply_rate_paid or "yes")
    return result


def farmer_payment_snapshot_fields(farmer, branch_ids, period_start, period_end):
    """Capture payment-sheet totals for a farmer at recording time."""
    period = build_farmer_payment_summaries(
        [farmer.id], branch_ids, period_start, period_end, pending_only=False
    ).get(farmer.id, {})

    def q(value):
        return (value or Decimal("0")).quantize(Decimal("0.01"))

    return {
        "rate": q(period.get("rate")),
        "apply_rate_paid": period.get("apply_rate_paid") or "yes",
        "total_liters": q(period.get("total_liters")),
        "gross_amount": q(period.get("gross_amount")),
        "advance_amount": q(period.get("advance_amount")),
        "goods_deduction": q(period.get("goods_deduction")),
        "loan_deduction": q(period.get("loan_deduction")),
        "net_amount": q(period.get("net_amount")),
    }


def summary_with_payment_snapshot(summary, payment):
    """Replace calculated totals with values stored when payment was recorded."""
    if not summary or not payment or payment.rate is None:
        return summary
    snap = dict(summary)
    snap["rate"] = payment.rate
    snap["apply_rate_paid"] = payment.apply_rate_paid or "yes"
    if payment.total_liters is not None:
        snap["total_liters"] = payment.total_liters
    if payment.gross_amount is not None:
        snap["gross_amount"] = payment.gross_amount
    if payment.advance_amount is not None:
        snap["advance_amount"] = payment.advance_amount
    if payment.goods_deduction is not None:
        snap["goods_deduction"] = payment.goods_deduction
    if payment.loan_deduction is not None:
        snap["loan_deduction"] = payment.loan_deduction
        snap["loan_deduction_calculated"] = payment.loan_deduction
    if payment.net_amount is not None:
        snap["net_amount"] = payment.net_amount
    return snap


def refresh_farmer_payment_sheet_record(period_start, period_end, branch_ids):
    """Aggregate farmer period payments into one paid sheet record."""
    branch_key = payment_sheet_branch_key(branch_ids)
    payments = [
        payment
        for payment in FarmerPeriodPayment.objects.filter(
            period_start=period_start,
            period_end=period_end,
        )
        if payment_sheet_branch_key(payment.branch_ids) == branch_key
    ]
    if not payments:
        FarmerPaymentSheetRecord.objects.filter(
            period_start=period_start,
            period_end=period_end,
            branch_key=branch_key,
        ).delete()
        return None

    total_paid = Decimal("0")
    total_gross = Decimal("0")
    last_paid_at = None
    for payment in payments:
        total_paid += payment.amount or Decimal("0")
        if payment.gross_amount is not None:
            total_gross += payment.gross_amount
        if payment.paid_at and (last_paid_at is None or payment.paid_at > last_paid_at):
            last_paid_at = payment.paid_at

    record, _created = FarmerPaymentSheetRecord.objects.update_or_create(
        period_start=period_start,
        period_end=period_end,
        branch_key=branch_key,
        defaults={
            "branch_ids": [int(b) for b in branch_ids if str(b).isdigit()],
            "farmer_count": len(payments),
            "total_paid": total_paid.quantize(Decimal("0.01")),
            "total_gross": total_gross.quantize(Decimal("0.01")),
            "last_paid_at": last_paid_at,
        },
    )
    return record


def collection_point_payment_snapshot_fields(point, branch_ids, period_start, period_end):
    """Capture collection point payment-sheet totals at recording time."""
    _date_cols, rows, _totals = build_collection_point_payment_sheet_data(
        branch_ids, period_start, period_end
    )
    for row in rows:
        if row["point"].id == point.id:
            def q(value):
                return (value or Decimal("0")).quantize(Decimal("0.01"))

            return {
                "rate": q(row.get("rate")),
                "collector_fee_rate": q(row.get("collector_fee_rate")),
                "additional_rate": q(row.get("additional_rate")),
                "total_quantity": q(row.get("total_quantity")),
                "gross_amount": q(row.get("gross_amount")),
                "advance_amount": q(row.get("advance_amount")),
                "goods_deduction": q(row.get("goods_deduction")),
                "loan_deduction": q(row.get("loan_deduction")),
                "collector_fee_amount": q(row.get("collector_fee_amount")),
                "additional_amount": q(row.get("additional_amount")),
                "previous_outstanding_amount": q(row.get("previous_outstanding_amount")),
                "payment_correction_amount": q(row.get("payment_correction_amount")),
                "net_amount": q(row.get("net_amount")),
            }
    raise ValueError(f"No payment sheet row for collection point {point.pk} in the selected period.")


def collection_point_row_with_payment_snapshot(row, payment):
    """Replace calculated totals with values stored when payment was recorded."""
    if not row or not payment or payment.rate is None:
        return row
    snap = dict(row)
    snap["rate"] = payment.rate
    if payment.collector_fee_rate is not None:
        snap["collector_fee_rate"] = payment.collector_fee_rate
    if payment.total_quantity is not None:
        snap["total_quantity"] = payment.total_quantity
    if payment.gross_amount is not None:
        snap["gross_amount"] = payment.gross_amount
    if payment.advance_amount is not None:
        snap["advance_amount"] = payment.advance_amount
    if payment.goods_deduction is not None:
        snap["goods_deduction"] = payment.goods_deduction
    if payment.loan_deduction is not None:
        snap["loan_deduction"] = payment.loan_deduction
        snap["loan_deduction_calculated"] = payment.loan_deduction
    if payment.collector_fee_amount is not None:
        snap["collector_fee_amount"] = payment.collector_fee_amount
    if payment.additional_rate is not None:
        snap["additional_rate"] = payment.additional_rate
    if payment.additional_amount is not None:
        snap["additional_amount"] = payment.additional_amount
    if payment.previous_outstanding_amount is not None:
        snap["previous_outstanding_amount"] = payment.previous_outstanding_amount
    if payment.payment_correction_amount is not None:
        snap["payment_correction_amount"] = payment.payment_correction_amount
    if payment.net_amount is not None:
        snap["net_amount"] = payment.net_amount
    snap["uses_payment_snapshot"] = True
    return snap


def refresh_collection_point_payment_sheet_record(period_start, period_end, branch_ids):
    """Aggregate collection point period payments into one paid sheet record."""
    branch_key = payment_sheet_branch_key(branch_ids)
    payments = [
        payment
        for payment in CollectionPointPeriodPayment.objects.filter(
            period_start=period_start,
            period_end=period_end,
        )
        if payment_sheet_branch_key(payment.branch_ids) == branch_key
    ]
    if not payments:
        CollectionPointPaymentSheetRecord.objects.filter(
            period_start=period_start,
            period_end=period_end,
            branch_key=branch_key,
        ).delete()
        return None

    total_paid = Decimal("0")
    total_gross = Decimal("0")
    last_paid_at = None
    for payment in payments:
        total_paid += payment.amount or Decimal("0")
        if payment.gross_amount is not None:
            total_gross += payment.gross_amount
        if payment.paid_at and (last_paid_at is None or payment.paid_at > last_paid_at):
            last_paid_at = payment.paid_at

    record, _created = CollectionPointPaymentSheetRecord.objects.update_or_create(
        period_start=period_start,
        period_end=period_end,
        branch_key=branch_key,
        defaults={
            "branch_ids": [int(b) for b in branch_ids if str(b).isdigit()],
            "point_count": len(payments),
            "total_paid": total_paid.quantize(Decimal("0.01")),
            "total_gross": total_gross.quantize(Decimal("0.01")),
            "last_paid_at": last_paid_at,
        },
    )
    return record


def build_farmer_payment_summaries(farmer_ids, branch_ids, start, end, pending_only=False):
    """Net payable and deduction breakdown per farmer for a payment period."""
    if not farmer_ids:
        return {}

    farmer_ids = list(farmer_ids)
    qty_map = defaultdict(lambda: {"kg": Decimal("0"), "liters": Decimal("0")})
    milk_qs = MilkCollection.objects.filter(
        source=CollectionSource.FARMER,
        date__gte=start,
        date__lte=end,
        branch_id__in=branch_ids,
        farmer_id__in=farmer_ids,
    )
    if pending_only:
        milk_qs = milk_qs.filter(is_paid=False)
    for row in milk_qs.values("farmer_id").annotate(
        total_kg=Sum("kg"), total_liters=Sum("liters")
    ):
        qty_map[row["farmer_id"]] = {
            "kg": row["total_kg"] or Decimal("0"),
            "liters": row["total_liters"] or Decimal("0"),
        }

    farmers = {
        f.id: f
        for f in Farmer.objects.filter(id__in=farmer_ids).select_related("branch")
    }
    latest_rate = latest_farmer_rate_map(farmer_ids)
    skip_map = payment_period_deduction_skip_map(farmer_ids, start, end)
    loan_settings_map = loan_deduction_settings_map(farmer_ids, start, end)

    advances_map = defaultdict(Decimal)
    bank_loan_advances_map = defaultdict(Decimal)
    advance_filter = {"farmer_id__in": farmer_ids, "date__gte": start, "date__lte": end}
    if pending_only:
        advance_filter["is_recovered"] = False
    for advance in FarmerAdvancePayment.objects.filter(**advance_filter).only(
        "id", "farmer_id", "amount", "advance_type"
    ):
        skips = skip_map.get(advance.farmer_id, {})
        if advance.id in skips.get("advance_ids", set()):
            continue
        advance_amt, bank_loan_amt = split_advance_amounts(
            advance.amount, advance.advance_type
        )
        advances_map[advance.farmer_id] += advance_amt
        bank_loan_advances_map[advance.farmer_id] += bank_loan_amt

    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)
    goods_amount_map = defaultdict(Decimal)
    goods_feed_map = defaultdict(Decimal)
    goods_vitamin_map = defaultdict(Decimal)
    goods_omi_map = defaultdict(Decimal)
    goods_minerals_map = defaultdict(Decimal)
    goods_others_map = defaultdict(Decimal)
    goods_by_farmer = defaultdict(list)
    goods_filter = {
        "issue_to_type": FarmerGoodsIssue.IssueToType.FARMER,
        "issue_to_id__in": farmer_ids,
        "date__gte": start_dt,
        "date__lt": end_dt,
    }
    if pending_only:
        goods_filter["is_settled"] = False
    goods_rows = list(
        FarmerGoodsIssue.objects.filter(**goods_filter)
        .select_related("product")
        .only(
            "id",
            "issue_to_id",
            "product_id",
            "quantity",
            "date",
            "batch_ref",
            "product__name",
            "product__category",
        )
    )
    for goods in goods_rows:
        skips = skip_map.get(goods.issue_to_id, {})
        if goods_issue_is_skipped(goods, skips):
            continue
        goods_by_farmer[goods.issue_to_id].append(goods)
    for farmer_id, farmer_goods in goods_by_farmer.items():
        (
            _items,
            goods_total,
            feed_total,
            vitamin_total,
            omi_total,
            minerals_total,
            others_total,
        ) = summarize_priced_farmer_goods_issues(farmer_goods)
        goods_amount_map[farmer_id] = goods_total
        goods_feed_map[farmer_id] = feed_total
        goods_vitamin_map[farmer_id] = vitamin_total
        goods_omi_map[farmer_id] = omi_total
        goods_minerals_map[farmer_id] = minerals_total
        goods_others_map[farmer_id] = others_total

    loan_amount_map = loan_deduction_amount_map(
        farmer_ids,
        start,
        end,
        pending_only=pending_only,
        skip_map=skip_map,
        loan_settings_map=loan_settings_map,
    )

    summaries = {}
    for farmer_id in farmer_ids:
        farmer = farmers.get(farmer_id)
        if farmer is None:
            continue
        milk_qty = qty_map[farmer_id]
        total_liters = milk_qty["liters"]
        rate_row = latest_rate.get(farmer_id)
        rate = rate_row.rate if rate_row else farmer.rate
        apply_rate_paid = (rate_row.apply_rate_paid if rate_row else farmer.apply_rate_paid) or "yes"
        gross = farmer_payment_gross(
            farmer.branch,
            rate,
            kg=milk_qty["kg"],
            liters=milk_qty["liters"],
            apply_rate_paid=apply_rate_paid,
        )
        advance = advances_map[farmer_id].quantize(Decimal("0.01"))
        bank_loan_advance = bank_loan_advances_map[farmer_id].quantize(Decimal("0.01"))
        goods_deduction = goods_amount_map[farmer_id].quantize(Decimal("0.01"))
        goods_feed_deduction = goods_feed_map[farmer_id].quantize(Decimal("0.01"))
        goods_vitamin_deduction = goods_vitamin_map[farmer_id].quantize(Decimal("0.01"))
        goods_omi_deduction = goods_omi_map[farmer_id].quantize(Decimal("0.01"))
        goods_minerals_deduction = goods_minerals_map[farmer_id].quantize(Decimal("0.01"))
        goods_others_deduction = goods_others_map[farmer_id].quantize(Decimal("0.01"))
        loan_instalment_calculated = loan_amount_map[farmer_id].quantize(Decimal("0.01"))
        deduct_loan = loan_settings_map.get(farmer_id, True)
        loan_instalment = loan_instalment_calculated if deduct_loan else Decimal("0.00")
        loan_deduction_calculated = (loan_instalment_calculated + bank_loan_advance).quantize(
            Decimal("0.01")
        )
        loan_deduction = (loan_instalment + bank_loan_advance).quantize(Decimal("0.01"))
        net = (gross - advance - goods_deduction - loan_deduction).quantize(Decimal("0.01"))
        summaries[farmer_id] = {
            "rate": rate,
            "apply_rate_paid": apply_rate_paid,
            "total_liters": total_liters.quantize(Decimal("0.01")),
            "gross_amount": gross,
            "advance_amount": advance,
            "bank_loan_advance_amount": bank_loan_advance,
            "goods_deduction": goods_deduction,
            "goods_feed_deduction": goods_feed_deduction,
            "goods_vitamin_deduction": goods_vitamin_deduction,
            "goods_omi_deduction": goods_omi_deduction,
            "goods_minerals_deduction": goods_minerals_deduction,
            "goods_others_deduction": goods_others_deduction,
            "loan_deduction_calculated": loan_deduction_calculated,
            "loan_deduction": loan_deduction,
            "deduct_loan": deduct_loan,
            "net_amount": net,
        }
    return summaries


def farmer_period_deduction_inventory(farmer_ids, start, end):
    """Unsettled advances, goods, and loan instalments in a period (before payment skips)."""
    inventory = {
        int(fid): {
            "advance_in_period": Decimal("0.00"),
            "goods_in_period": Decimal("0.00"),
            "loan_in_period": Decimal("0.00"),
        }
        for fid in farmer_ids
    }
    if not farmer_ids:
        return inventory

    for advance in FarmerAdvancePayment.objects.filter(
        farmer_id__in=farmer_ids,
        date__gte=start,
        date__lte=end,
        is_recovered=False,
    ).only("farmer_id", "amount", "advance_type"):
        farmer_id = advance.farmer_id
        advance_amt, bank_loan_amt = split_advance_amounts(
            advance.amount, advance.advance_type
        )
        inventory[farmer_id]["advance_in_period"] += advance_amt
        inventory[farmer_id]["loan_in_period"] += bank_loan_amt

    for farmer_id in farmer_ids:
        inventory[farmer_id]["advance_in_period"] = inventory[farmer_id][
            "advance_in_period"
        ].quantize(Decimal("0.01"))

    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)
    goods_by_farmer = defaultdict(list)
    for goods in FarmerGoodsIssue.objects.filter(
        issue_to_type=FarmerGoodsIssue.IssueToType.FARMER,
        issue_to_id__in=farmer_ids,
        date__gte=start_dt,
        date__lt=end_dt,
        is_settled=False,
    ).select_related("product"):
        goods_by_farmer[goods.issue_to_id].append(goods)
    for farmer_id, farmer_goods in goods_by_farmer.items():
        _items, total, *_rest = summarize_priced_farmer_goods_issues(farmer_goods)
        inventory[farmer_id]["goods_in_period"] = total.quantize(Decimal("0.01"))

    loan_map = pending_loan_deduction_map(farmer_ids, start, end)
    for farmer_id, amount in loan_map.items():
        inventory[farmer_id]["loan_in_period"] = (
            inventory[farmer_id]["loan_in_period"] + (amount or Decimal("0"))
        ).quantize(Decimal("0.01"))
    for farmer_id in farmer_ids:
        inventory[farmer_id]["loan_in_period"] = inventory[farmer_id][
            "loan_in_period"
        ].quantize(Decimal("0.01"))

    return inventory


def pending_loan_deduction_map(farmer_ids, period_start, period_end):
    """Sum unpaid approved-loan instalments due on or before period_end."""
    amounts = defaultdict(Decimal)
    if not farmer_ids:
        return amounts
    rows = FarmerLoanRepaymentSchedule.objects.filter(
        loan__farmer_id__in=farmer_ids,
        loan__status=FarmerLoan.Status.APPROVED,
        due_date__lte=period_end,
        due_date__gte=period_start,
        is_paid=False,
    ).values("loan__farmer_id", "installment_amount", "paid_amount")
    for row in rows:
        pending = (row["installment_amount"] or Decimal("0")) - (row["paid_amount"] or Decimal("0"))
        if pending > 0:
            amounts[row["loan__farmer_id"]] += pending
    return amounts


def period_loan_deduction_map(farmer_ids, period_start, period_end):
    """Sum approved-loan instalment amounts due within the payment period."""
    amounts = defaultdict(Decimal)
    if not farmer_ids:
        return amounts
    rows = FarmerLoanRepaymentSchedule.objects.filter(
        loan__farmer_id__in=farmer_ids,
        loan__status=FarmerLoan.Status.APPROVED,
        due_date__lte=period_end,
        due_date__gte=period_start,
    ).values("loan__farmer_id", "installment_amount")
    for row in rows:
        amounts[row["loan__farmer_id"]] += row["installment_amount"] or Decimal("0")
    return amounts


def loan_deduction_settings_map(farmer_ids, period_start, period_end):
    from .models import FarmerPaymentLoanDeductionSetting

    settings_map = {}
    if not farmer_ids:
        return settings_map
    for row in FarmerPaymentLoanDeductionSetting.objects.filter(
        farmer_id__in=farmer_ids,
        period_start=period_start,
        period_end=period_end,
    ).only("farmer_id", "deduct_loan"):
        settings_map[row.farmer_id] = row.deduct_loan
    return settings_map


def cp_loan_deduction_settings_map(point_ids, period_start, period_end):
    from .models import CollectionPointPaymentLoanDeductionSetting

    settings_map = {}
    if not point_ids:
        return settings_map
    for row in CollectionPointPaymentLoanDeductionSetting.objects.filter(
        collection_point_id__in=point_ids,
        period_start=period_start,
        period_end=period_end,
    ).only("collection_point_id", "deduct_loan"):
        settings_map[row.collection_point_id] = row.deduct_loan
    return settings_map


def applied_loan_deduction(calculated_amount, farmer_id, settings_map):
    if calculated_amount <= 0:
        return Decimal("0.00")
    deduct = settings_map.get(farmer_id, True)
    if deduct:
        return calculated_amount.quantize(Decimal("0.01"))
    return Decimal("0.00")


def _day_collection_status(total, paid):
    if total <= 0:
        return "none"
    if paid <= 0:
        return "unpaid"
    if paid >= total:
        return "paid"
    return "partial"


def _farmer_rate_info(farmer, latest_rate):
    rate_row = latest_rate.get(farmer.id)
    rate = rate_row.rate if rate_row else farmer.rate
    apply_rate_paid = (rate_row.apply_rate_paid if rate_row else farmer.apply_rate_paid) or "yes"
    return rate, apply_rate_paid


def farmer_paid_collection_gross_map(farmer_ids, branch_ids, start, end):
    """Gross milk value from farmer collections marked paid in the period."""
    from .payment_recording import farmer_period_payment_map

    amounts = defaultdict(lambda: Decimal("0"))
    if not farmer_ids:
        return amounts
    farmer_ids = list(farmer_ids)
    farmers = {
        f.id: f
        for f in Farmer.objects.filter(id__in=farmer_ids).select_related("branch")
    }
    latest_rate = latest_farmer_rate_map(farmer_ids)
    payments_map = farmer_period_payment_map(farmer_ids, start, end)
    paid_qty = defaultdict(lambda: {"kg": Decimal("0"), "liters": Decimal("0")})
    for row in (
        MilkCollection.objects.filter(
            source=CollectionSource.FARMER,
            date__gte=start,
            date__lte=end,
            branch_id__in=branch_ids,
            farmer_id__in=farmer_ids,
            is_paid=True,
        )
        .values("farmer_id")
        .annotate(total_kg=Sum("kg"), total_liters=Sum("liters"))
    ):
        paid_qty[row["farmer_id"]] = {
            "kg": row["total_kg"] or Decimal("0"),
            "liters": row["total_liters"] or Decimal("0"),
        }
    for farmer_id in farmer_ids:
        farmer = farmers.get(farmer_id)
        if farmer is None:
            continue
        payment = payments_map.get(farmer_id)
        if payment and payment.gross_amount is not None:
            amounts[farmer_id] = payment.gross_amount.quantize(Decimal("0.01"))
            continue
        rate, apply_rate_paid = _farmer_rate_info(farmer, latest_rate)
        qty = paid_qty[farmer_id]
        amounts[farmer_id] = farmer_payment_gross(
            farmer.branch,
            rate,
            kg=qty["kg"],
            liters=qty["liters"],
            apply_rate_paid=apply_rate_paid,
        )
    return amounts


def date_range_inclusive(start, end):
    days = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur = cur + timedelta(days=1)
    return days


def _merge_farmer_sheet_summaries(pending_summary, period_summary):
    """Pending amounts drive net/balance; period totals are shown for reference."""
    pending_summary = pending_summary or {}
    period_summary = period_summary or {}
    return {
        **pending_summary,
        "total_liters": period_summary.get("total_liters", Decimal("0.00")),
        "payable_liters": pending_summary.get("total_liters", Decimal("0.00")),
        "gross_amount": period_summary.get("gross_amount", Decimal("0.00")),
        "payable_gross": pending_summary.get("gross_amount", Decimal("0.00")),
        "advance_amount": period_summary.get("advance_amount", Decimal("0.00")),
        "advance_pending": pending_summary.get("advance_amount", Decimal("0.00")),
        "goods_deduction": period_summary.get("goods_deduction", Decimal("0.00")),
        "goods_pending": pending_summary.get("goods_deduction", Decimal("0.00")),
        "loan_deduction_calculated": period_summary.get(
            "loan_deduction_calculated", Decimal("0.00")
        ),
        "loan_pending_calculated": pending_summary.get(
            "loan_deduction_calculated", Decimal("0.00")
        ),
        "loan_deduction": pending_summary.get("loan_deduction", Decimal("0.00")),
        "net_amount": period_summary.get("net_amount", Decimal("0.00")),
        "payable_net": pending_summary.get("net_amount", Decimal("0.00")),
    }


def farmer_sheet_paid_and_balance(
    period_summary,
    pending_summary,
    paid_collection_gross=None,
    cash_paid=None,
):
    """
    Paid = gross milk value from collection days marked paid.
    Balance = net payable on unpaid collections (pending summary net).
  """
    pending_summary = pending_summary or {}
    period_summary = period_summary or {}

    paid_amount = Decimal(paid_collection_gross or Decimal("0")).quantize(Decimal("0.01"))
    balance_amount = Decimal(pending_summary.get("net_amount", Decimal("0"))).quantize(
        Decimal("0.01")
    )
    balance_amount = max(Decimal("0"), balance_amount)

    period_advance = period_summary.get("advance_amount", Decimal("0"))
    period_goods = period_summary.get("goods_deduction", Decimal("0"))
    period_loan = period_summary.get("loan_deduction", Decimal("0"))
    pending_advance = pending_summary.get("advance_amount", Decimal("0"))
    pending_goods = pending_summary.get("goods_deduction", Decimal("0"))
    pending_loan = pending_summary.get("loan_deduction", Decimal("0"))

    settled_advance = (period_advance - pending_advance).quantize(Decimal("0.01"))
    settled_goods = (period_goods - pending_goods).quantize(Decimal("0.01"))
    settled_loan = (period_loan - pending_loan).quantize(Decimal("0.01"))
    cash = Decimal(cash_paid or Decimal("0")).quantize(Decimal("0.01"))

    return {
        "paid_amount": paid_amount,
        "balance_amount": balance_amount,
        "cash_paid": cash,
        "settled_advance": settled_advance,
        "settled_goods": settled_goods,
        "settled_loan": settled_loan,
    }


def farmer_balance_for_period(farmer_id, branch_ids, start, end):
    """Remaining balance for a farmer on the payment sheet."""
    period = build_farmer_payment_summaries(
        [farmer_id], branch_ids, start, end, pending_only=False
    )
    pending = build_farmer_payment_summaries(
        [farmer_id], branch_ids, start, end, pending_only=True
    )
    cash_map, _ = farmer_overlapping_paid_data([farmer_id], start, end)
    paid_gross = farmer_paid_collection_gross_map(
        [farmer_id], branch_ids, start, end
    ).get(farmer_id, Decimal("0"))
    return farmer_sheet_paid_and_balance(
        period.get(farmer_id),
        pending.get(farmer_id),
        paid_collection_gross=paid_gross,
        cash_paid=cash_map.get(farmer_id, Decimal("0")),
    )["balance_amount"]


def farmer_overlapping_paid_data(farmer_ids, sheet_start, sheet_end):
    amounts = defaultdict(lambda: Decimal("0"))
    payments = defaultdict(list)
    if not farmer_ids:
        return amounts, payments
    for payment in (
        FarmerPeriodPayment.objects.filter(
            farmer_id__in=farmer_ids,
            period_start__lte=sheet_end,
            period_end__gte=sheet_start,
        )
        .select_related("recorded_by")
        .order_by("-paid_at")
    ):
        amounts[payment.farmer_id] += payment.amount or Decimal("0")
        payments[payment.farmer_id].append(payment)
    return amounts, payments


def collection_point_overlapping_paid_data(point_ids, sheet_start, sheet_end):
    amounts = defaultdict(lambda: Decimal("0"))
    payments = defaultdict(list)
    if not point_ids:
        return amounts, payments
    for payment in (
        CollectionPointPeriodPayment.objects.filter(
            collection_point_id__in=point_ids,
            period_start__lte=sheet_end,
            period_end__gte=sheet_start,
        )
        .select_related("recorded_by")
        .order_by("-paid_at")
    ):
        amounts[payment.collection_point_id] += payment.amount or Decimal("0")
        payments[payment.collection_point_id].append(payment)
    return amounts, payments


def collection_point_pending_payable_amount(
    point,
    branch_ids,
    start,
    end,
    balance_payment=False,
    previous_outstanding=None,
    payment_correction=None,
):
    """Remaining payable collector fee or full balance for a collection point period."""
    from milk_collections.reconcile_payment import collection_point_period_effective_totals

    _total_qty, unpaid_qty = collection_point_period_effective_totals(point, branch_ids, start, end)
    fee_rate = (point.collector_fee or Decimal("0")).quantize(Decimal("0.01"))
    collector_fee = (unpaid_qty * fee_rate).quantize(Decimal("0.01"))
    if not balance_payment:
        return collector_fee

    farmer_ids = list(Farmer.objects.filter(collection_point=point).values_list("id", flat=True))
    pending = _aggregate_point_farmer_payment_data(
        farmer_ids, branch_ids, start, end, point=point, pending_only=True
    )
    additional, _additional_qty = collection_point_additional_amount_for_period(
        point, branch_ids, start, end
    )
    if previous_outstanding is None:
        previous_outstanding = collection_point_previous_outstanding_amount(
            point.id, end, pending_only=True
        )
    if payment_correction is None:
        payment_correction = collection_point_payment_correction_amount(
            point.id, start, end
        )
    return collection_point_period_net_amount(
        gross_amount=pending["gross_amount"],
        advance_amount=pending["advance_amount"],
        goods_deduction=pending["goods_deduction"],
        loan_deduction=pending["loan_deduction"],
        previous_outstanding=previous_outstanding,
        collector_fee_amount=collector_fee,
        additional_amount=additional,
        payment_correction=payment_correction,
    )


def collection_point_payment_action_flags(
    pending_payable, period_payment=None, allow_zero_settlement=False
):
    """Pay / settle / settled state for one collection point payment-sheet row.

    Negative pending means deductions exceed unpaid milk: cash to pay is 0, but
    the period still needs settlement so the deficit can carry to the next sheet.
    """
    pending = Decimal(pending_payable or 0).quantize(Decimal("0.01"))
    has_payment = bool(period_payment)
    needs_carry_forward = (not has_payment) and pending < Decimal("0")
    balance_amount = max(Decimal("0"), pending)
    can_make_payment = (not has_payment) and (
        balance_amount > Decimal("0")
        or needs_carry_forward
        or bool(allow_zero_settlement)
    )
    return {
        "pending_payable": pending,
        "balance_amount": balance_amount,
        "needs_carry_forward": needs_carry_forward,
        "carry_forward_amount": (
            (-pending).quantize(Decimal("0.01")) if needs_carry_forward else Decimal("0.00")
        ),
        "can_make_payment": can_make_payment,
        "is_fully_settled": has_payment
        or (
            not needs_carry_forward
            and balance_amount <= Decimal("0")
            and not bool(allow_zero_settlement)
        ),
    }


def collection_point_balance_for_period(
    point,
    branch_ids,
    start,
    end,
    paid_amount=None,
    balance_payment=False,
    previous_outstanding=None,
    payment_correction=None,
):
    """Remaining payable for a collection point period (unpaid / pending only).

    ``paid_amount`` is accepted for caller compatibility but ignored: remaining
    payable already excludes settled milk and recovered deductions, matching the
    farmer payment sheet model.
    """
    return max(
        Decimal("0"),
        collection_point_pending_payable_amount(
            point,
            branch_ids,
            start,
            end,
            balance_payment=balance_payment,
            previous_outstanding=previous_outstanding,
            payment_correction=payment_correction,
        ).quantize(Decimal("0.01")),
    )


def collection_point_has_zero_cash_settlement_items(
    *,
    advance_amount=Decimal("0"),
    goods_deduction=Decimal("0"),
    loan_deduction=Decimal("0"),
    previous_outstanding=Decimal("0"),
):
    return any(
        Decimal(value or 0).quantize(Decimal("0.01")) > Decimal("0")
        for value in (
            advance_amount,
            goods_deduction,
            loan_deduction,
            previous_outstanding,
        )
    )


def build_farmer_payment_sheet_data(branch_ids, start, end):
    from .payment_recording import (
        farmer_period_payment_map,
        sync_farmer_milk_paid_from_period_payments,
    )

    sync_farmer_milk_paid_from_period_payments(
        sheet_start=start, sheet_end=end, branch_ids=branch_ids
    )
    date_cols = date_range_inclusive(start, end)
    milk_qs = (
        MilkCollection.objects.filter(
            source=CollectionSource.FARMER,
            date__gte=start,
            date__lte=end,
            branch_id__in=branch_ids,
        )
        .select_related("farmer", "branch")
        .only("date", "liters", "farmer_id", "branch_id", "is_paid", "farmer__id")
    )
    liters_by_farmer_day = defaultdict(Decimal)
    paid_liters_by_farmer_day = defaultdict(Decimal)
    farmer_ids = set()
    for row in milk_qs:
        key = (row.farmer_id, row.date)
        liters = row.liters or Decimal("0")
        liters_by_farmer_day[key] += liters
        if row.is_paid:
            paid_liters_by_farmer_day[key] += liters
        farmer_ids.add(row.farmer_id)
    farmers = list(
        Farmer.objects.filter(id__in=farmer_ids)
        .select_related("branch", "route", "collection_point")
        .prefetch_related("bank_accounts")
        .order_by("common_name", "full_name")
    )
    pending_summaries = build_farmer_payment_summaries(
        list(farmer_ids), branch_ids, start, end, pending_only=True
    )
    period_summaries = build_farmer_payment_summaries(
        list(farmer_ids), branch_ids, start, end, pending_only=False
    )
    cash_paid_map, payments_by_farmer = farmer_overlapping_paid_data(
        list(farmer_ids), start, end
    )
    paid_gross_map = farmer_paid_collection_gross_map(
        list(farmer_ids), branch_ids, start, end
    )
    deduction_inventory = farmer_period_deduction_inventory(list(farmer_ids), start, end)
    period_payments_map = farmer_period_payment_map(list(farmer_ids), start, end)
    rows = []
    for farmer in farmers:
        period_summary = period_summaries.get(farmer.id)
        pending_summary = pending_summaries.get(farmer.id)
        period_payment = period_payments_map.get(farmer.id)
        if period_payment and period_payment.rate is not None:
            period_summary = summary_with_payment_snapshot(period_summary, period_payment)
        summary = _merge_farmer_sheet_summaries(pending_summary, period_summary)
        if not summary:
            continue
        payment_totals = farmer_sheet_paid_and_balance(
            period_summary,
            pending_summary,
            paid_collection_gross=paid_gross_map.get(farmer.id, Decimal("0")),
            cash_paid=cash_paid_map.get(farmer.id, Decimal("0")),
        )
        day_cols = []
        day_vals = []
        has_unpaid_collections = False
        for day in date_cols:
            total_liters = liters_by_farmer_day[(farmer.id, day)]
            paid_liters = paid_liters_by_farmer_day[(farmer.id, day)]
            status = _day_collection_status(total_liters, paid_liters)
            if status in ("unpaid", "partial"):
                has_unpaid_collections = True
            liters_value = total_liters.quantize(Decimal("0.01"))
            day_vals.append(liters_value)
            day_cols.append(
                {
                    "date": day,
                    "liters": liters_value,
                    "paid_liters": paid_liters.quantize(Decimal("0.01")),
                    "status": status,
                }
            )
        period_payments = payments_by_farmer.get(farmer.id, [])
        paid_amount = payment_totals["paid_amount"]
        balance_amount = payment_totals["balance_amount"]
        payable_net = pending_summaries.get(farmer.id, {}).get("net_amount", Decimal("0"))
        can_make_payment = balance_amount > Decimal("0")
        inv = deduction_inventory.get(farmer.id, {})
        bank_accounts = list(farmer.bank_accounts.all())
        rows.append(
            {
                "farmer": farmer,
                "advance_in_period": inv.get("advance_in_period", Decimal("0.00")),
                "goods_in_period": inv.get("goods_in_period", Decimal("0.00")),
                "loan_in_period": inv.get("loan_in_period", Decimal("0.00")),
                "day_vals": day_vals,
                "day_cols": day_cols,
                "has_unpaid_collections": has_unpaid_collections,
                "period_payment": period_payments[0] if period_payments else None,
                "period_payments": period_payments,
                "cash_paid": payment_totals["cash_paid"],
                "paid_advance": payment_totals["settled_advance"],
                "paid_goods": payment_totals["settled_goods"],
                "paid_loan": payment_totals["settled_loan"],
                "paid_amount": paid_amount,
                "balance_amount": balance_amount,
                "payable_net": payable_net.quantize(Decimal("0.01")),
                "can_make_payment": can_make_payment,
                "is_fully_settled": not can_make_payment,
                "period_payment": period_payment,
                "uses_payment_snapshot": bool(
                    period_payment and period_payment.rate is not None
                ),
                "bank_accounts_json": json.dumps(
                    [
                        {
                            "id": account.pk,
                            "label": account.label,
                            "primary": account.is_primary,
                            "bank_code": account.bank_code or "",
                            "branch_code": account.branch_code or "",
                            "ceft_ready": account.is_ceft_ready,
                        }
                        for account in bank_accounts
                    ]
                ),
                "primary_bank_account_id": next(
                    (account.pk for account in bank_accounts if account.is_primary),
                    bank_accounts[0].pk if bank_accounts else None,
                ),
                "primary_bank_ceft_ready": bool(
                    next(
                        (
                            account
                            for account in bank_accounts
                            if account.is_primary and account.is_ceft_ready
                        ),
                        next((account for account in bank_accounts if account.is_ceft_ready), None),
                    )
                ),
                **summary,
            }
        )
    day_totals = [Decimal("0")] * len(date_cols)
    sheet_totals = {
        "day_vals": day_totals,
        "total_liters": Decimal("0"),
        "gross_amount": Decimal("0"),
        "advance_amount": Decimal("0"),
        "goods_deduction": Decimal("0"),
        "loan_deduction": Decimal("0"),
        "net_amount": Decimal("0"),
        "paid_amount": Decimal("0"),
        "balance_amount": Decimal("0"),
        "cash_paid": Decimal("0"),
        "paid_advance": Decimal("0"),
        "paid_goods": Decimal("0"),
        "paid_loan": Decimal("0"),
    }
    for row in rows:
        for i, liters in enumerate(row["day_vals"]):
            sheet_totals["day_vals"][i] += liters or Decimal("0")
        sheet_totals["total_liters"] += row["total_liters"] or Decimal("0")
        sheet_totals["gross_amount"] += row["gross_amount"] or Decimal("0")
        sheet_totals["advance_amount"] += row["advance_amount"] or Decimal("0")
        sheet_totals["goods_deduction"] += row["goods_deduction"] or Decimal("0")
        sheet_totals["loan_deduction"] += row["loan_deduction"] or Decimal("0")
        sheet_totals["net_amount"] += row["net_amount"] or Decimal("0")
        sheet_totals["paid_amount"] += row["paid_amount"] or Decimal("0")
        sheet_totals["balance_amount"] += row["balance_amount"] or Decimal("0")
        sheet_totals["cash_paid"] += row.get("cash_paid") or Decimal("0")
        sheet_totals["paid_advance"] += row.get("paid_advance") or Decimal("0")
        sheet_totals["paid_goods"] += row.get("paid_goods") or Decimal("0")
        sheet_totals["paid_loan"] += row.get("paid_loan") or Decimal("0")
    cash_method_total = Decimal("0")
    bank_method_total = Decimal("0")
    cash_method_count = 0
    bank_method_count = 0
    rate_sum = Decimal("0")
    rate_count = 0
    bank_method = FarmerPeriodPayment.PaymentMethod.BANK_TRANSFER
    for row in rows:
        rate = row.get("rate")
        if rate is not None:
            rate_sum += Decimal(rate)
            rate_count += 1
        if row.get("can_make_payment"):
            amount = Decimal(row.get("balance_amount") or 0)
            if amount <= 0:
                continue
            period_payment = row.get("period_payment")
            if period_payment and (period_payment.payment_method or ""):
                method = period_payment.payment_method
            elif row.get("primary_bank_account_id"):
                method = bank_method
            else:
                method = FarmerPeriodPayment.PaymentMethod.CASH
            if method == bank_method:
                bank_method_total += amount
                bank_method_count += 1
            else:
                cash_method_total += amount
                cash_method_count += 1
        else:
            for payment in row.get("period_payments") or []:
                amount = Decimal(payment.amount or 0)
                if amount <= 0:
                    continue
                if (payment.payment_method or "") == bank_method:
                    bank_method_total += amount
                    bank_method_count += 1
                else:
                    cash_method_total += amount
                    cash_method_count += 1
    farmer_count = len(rows)
    sheet_totals["farmer_count"] = farmer_count
    sheet_totals["day_count"] = len(date_cols)
    sheet_totals["cash_method_total"] = cash_method_total
    sheet_totals["bank_method_total"] = bank_method_total
    sheet_totals["total_payment"] = (cash_method_total + bank_method_total).quantize(Decimal("0.01"))
    sheet_totals["cash_method_count"] = cash_method_count
    sheet_totals["bank_method_count"] = bank_method_count
    sheet_totals["avg_farmer_rate"] = (
        (rate_sum / Decimal(rate_count)).quantize(Decimal("0.01")) if rate_count else None
    )
    day_count = sheet_totals.get("day_count") or 0
    sheet_totals["avg_farmer_collection"] = (
        (sheet_totals["total_liters"] / Decimal(farmer_count) / Decimal(day_count)).quantize(
            Decimal("0.01")
        )
        if farmer_count and day_count
        else None
    )
    sheet_totals["day_vals"] = [v.quantize(Decimal("0.01")) for v in sheet_totals["day_vals"]]
    for key in (
        "total_liters",
        "gross_amount",
        "advance_amount",
        "goods_deduction",
        "loan_deduction",
        "net_amount",
        "paid_amount",
        "balance_amount",
        "cash_paid",
        "paid_advance",
        "paid_goods",
        "paid_loan",
        "cash_method_total",
        "bank_method_total",
        "total_payment",
    ):
        sheet_totals[key] = sheet_totals[key].quantize(Decimal("0.01"))
    return date_cols, rows, sheet_totals


def group_farmer_payment_sheet_by_route(rows, date_cols):
    """Group farmer payment-sheet rows by route for print packs."""
    groups = []
    groups_by_key = {}
    bank_method = FarmerPeriodPayment.PaymentMethod.BANK_TRANSFER
    for row in rows:
        farmer = row["farmer"]
        route = farmer.route if getattr(farmer, "route_id", None) else None
        key = route.pk if route else 0
        if key not in groups_by_key:
            group = {
                "route": route,
                "route_id": key,
                "route_label": f"{route.code} — {route.name}" if route else "No route",
                "rows": [],
            }
            groups_by_key[key] = group
            groups.append(group)
        groups_by_key[key]["rows"].append(row)

    day_count = len(date_cols) or 1
    for group in groups:
        totals = {
            "day_vals": [Decimal("0") for _ in date_cols],
            "total_liters": Decimal("0"),
            "gross_amount": Decimal("0"),
            "advance_amount": Decimal("0"),
            "goods_deduction": Decimal("0"),
            "loan_deduction": Decimal("0"),
            "net_amount": Decimal("0"),
            "paid_amount": Decimal("0"),
            "balance_amount": Decimal("0"),
        }
        cash_method_total = Decimal("0")
        bank_method_total = Decimal("0")
        cash_method_count = 0
        bank_method_count = 0
        rate_sum = Decimal("0")
        rate_count = 0
        for row in group["rows"]:
            for i, liters in enumerate(row.get("day_vals") or []):
                totals["day_vals"][i] += liters or Decimal("0")
            totals["total_liters"] += row.get("total_liters") or Decimal("0")
            totals["gross_amount"] += row.get("gross_amount") or Decimal("0")
            totals["advance_amount"] += row.get("advance_amount") or Decimal("0")
            totals["goods_deduction"] += row.get("goods_deduction") or Decimal("0")
            totals["loan_deduction"] += (
                row.get("loan_deduction_calculated")
                or row.get("loan_deduction")
                or Decimal("0")
            )
            totals["net_amount"] += row.get("net_amount") or Decimal("0")
            totals["paid_amount"] += row.get("paid_amount") or Decimal("0")
            totals["balance_amount"] += row.get("balance_amount") or Decimal("0")
            rate = row.get("rate")
            if rate is not None:
                rate_sum += Decimal(rate)
                rate_count += 1
            if row.get("can_make_payment"):
                amount = Decimal(row.get("balance_amount") or 0)
                if amount <= 0:
                    continue
                period_payment = row.get("period_payment")
                if period_payment and (period_payment.payment_method or ""):
                    method = period_payment.payment_method
                elif row.get("primary_bank_account_id"):
                    method = bank_method
                else:
                    method = FarmerPeriodPayment.PaymentMethod.CASH
                if method == bank_method:
                    bank_method_total += amount
                    bank_method_count += 1
                else:
                    cash_method_total += amount
                    cash_method_count += 1
            else:
                for payment in row.get("period_payments") or []:
                    amount = Decimal(payment.amount or 0)
                    if amount <= 0:
                        continue
                    if (payment.payment_method or "") == bank_method:
                        bank_method_total += amount
                        bank_method_count += 1
                    else:
                        cash_method_total += amount
                        cash_method_count += 1
        farmer_count = len(group["rows"])
        totals["farmer_count"] = farmer_count
        totals["day_count"] = day_count
        totals["cash_method_total"] = cash_method_total.quantize(Decimal("0.01"))
        totals["bank_method_total"] = bank_method_total.quantize(Decimal("0.01"))
        totals["cash_method_count"] = cash_method_count
        totals["bank_method_count"] = bank_method_count
        totals["total_payment"] = (cash_method_total + bank_method_total).quantize(Decimal("0.01"))
        totals["avg_farmer_rate"] = (
            (rate_sum / Decimal(rate_count)).quantize(Decimal("0.01")) if rate_count else None
        )
        totals["avg_farmer_collection"] = (
            (totals["total_liters"] / Decimal(farmer_count) / Decimal(day_count)).quantize(
                Decimal("0.01")
            )
            if farmer_count and day_count
            else None
        )
        totals["day_vals"] = [v.quantize(Decimal("0.01")) for v in totals["day_vals"]]
        for key in (
            "total_liters",
            "gross_amount",
            "advance_amount",
            "goods_deduction",
            "loan_deduction",
            "net_amount",
            "paid_amount",
            "balance_amount",
        ):
            totals[key] = totals[key].quantize(Decimal("0.01"))
        group["totals"] = totals
    groups.sort(key=lambda g: (0 if g["route"] else 1, g["route_label"]))
    return groups


def farmer_payment_sheet_row_snapshot(farmer_id, branch_ids, start, end):
    """Serialized deduction/net totals for one farmer row (AJAX refresh)."""
    _date_cols, rows, _totals = build_farmer_payment_sheet_data(branch_ids, start, end)
    for row in rows:
        if row["farmer"].id == farmer_id:
            q = lambda v: str((v or Decimal("0")).quantize(Decimal("0.01")))
            return {
                "advance_amount": q(row.get("advance_amount")),
                "advance_pending": q(row.get("advance_pending")),
                "advance_in_period": q(row.get("advance_in_period")),
                "goods_deduction": q(row.get("goods_deduction")),
                "goods_pending": q(row.get("goods_pending")),
                "goods_in_period": q(row.get("goods_in_period")),
                "loan_deduction": q(row.get("loan_deduction")),
                "loan_deduction_calculated": q(row.get("loan_deduction_calculated")),
                "loan_pending_calculated": q(row.get("loan_pending_calculated")),
                "loan_in_period": q(row.get("loan_in_period")),
                "net_amount": q(row.get("net_amount")),
                "paid_amount": q(row.get("paid_amount")),
                "balance_amount": q(row.get("balance_amount")),
                "deduct_loan": bool(row.get("deduct_loan", True)),
                "can_make_payment": bool(row.get("can_make_payment")),
                "is_fully_settled": bool(row.get("is_fully_settled")),
            }
    return None


def collection_point_payment_sheet_row_snapshot(point_id, branch_ids, start, end):
    """Serialized deduction/net totals for one collection point row (AJAX refresh).

    Computes only the requested point — does not rebuild the full payment sheet.
    """
    from milk_collections.reconcile_payment import collection_point_period_effective_totals
    from .payment_recording import collection_point_period_payment_map

    point = (
        CollectionPoint.objects.filter(pk=point_id)
        .select_related("route", "route__branch")
        .first()
    )
    if point is None:
        return None

    farmer_ids = list(
        Farmer.objects.filter(collection_point_id=point_id).values_list("id", flat=True)
    )
    total_qty, _unpaid_qty = collection_point_period_effective_totals(
        point, branch_ids, start, end
    )
    fee_rate = (point.collector_fee or Decimal("0")).quantize(Decimal("0.01"))
    collector_fee_amount = (total_qty * fee_rate).quantize(Decimal("0.01"))
    additional_amount, _additional_qty = collection_point_additional_amount_for_period(
        point, branch_ids, start, end
    )
    previous_outstanding = collection_point_previous_outstanding_amount(
        point.id, end, pending_only=False, period_start=start, period_end=end
    )
    previous_outstanding_pending = collection_point_previous_outstanding_amount(
        point.id, end, pending_only=True
    )
    payment_correction = collection_point_payment_correction_amount(
        point.id, start, end
    )

    pending_farmer_data = _aggregate_point_farmer_payment_data(
        farmer_ids, branch_ids, start, end, point=point, pending_only=True
    )
    period_farmer_data = _aggregate_point_farmer_payment_data(
        farmer_ids, branch_ids, start, end, point=point, pending_only=False
    )
    cash_paid_map, _payments = collection_point_overlapping_paid_data(
        [point.id], start, end
    )
    fee_paid = collection_point_paid_collector_fee_map(
        [point.id], branch_ids, start, end, points_by_id={point.id: point}
    ).get(point.id, Decimal("0"))
    payment_totals = collection_point_sheet_paid_and_balance(
        point,
        branch_ids,
        start,
        end,
        period_farmer_data,
        pending_farmer_data,
        paid_collector_fee=fee_paid,
        cash_paid=cash_paid_map.get(point.id, Decimal("0")),
        collector_fee_amount=collector_fee_amount,
        additional_amount=additional_amount,
        previous_outstanding=previous_outstanding,
        previous_outstanding_pending=previous_outstanding_pending,
        payment_correction=payment_correction,
    )
    net_amount = collection_point_period_net_amount(
        gross_amount=period_farmer_data["gross_amount"],
        advance_amount=period_farmer_data["advance_amount"],
        goods_deduction=period_farmer_data["goods_deduction"],
        loan_deduction=period_farmer_data["loan_deduction"],
        previous_outstanding=previous_outstanding,
        collector_fee_amount=collector_fee_amount,
        additional_amount=additional_amount,
        payment_correction=payment_correction,
    )
    allow_zero_settlement = collection_point_has_zero_cash_settlement_items(
        advance_amount=period_farmer_data["advance_amount"],
        goods_deduction=period_farmer_data["goods_deduction"],
        loan_deduction=period_farmer_data["loan_deduction_calculated"],
        previous_outstanding=previous_outstanding_pending,
    ) and net_amount == Decimal("0.00")
    paid_amount = payment_totals["paid_amount"]
    balance_amount = payment_totals["balance_amount"]
    period_payment = collection_point_period_payment_map([point.id], start, end).get(point.id)
    action_flags = collection_point_payment_action_flags(
        collection_point_pending_payable_amount(
            point,
            branch_ids,
            start,
            end,
            balance_payment=True,
            previous_outstanding=previous_outstanding_pending,
            payment_correction=payment_correction,
        ),
        period_payment,
        allow_zero_settlement=allow_zero_settlement,
    )
    can_make_payment = action_flags["can_make_payment"]

    row = {
        "advance_amount": period_farmer_data["advance_amount"],
        "advance_pending": pending_farmer_data["advance_amount"],
        "goods_deduction": period_farmer_data["goods_deduction"],
        "goods_pending": pending_farmer_data["goods_deduction"],
        "loan_deduction": pending_farmer_data["loan_deduction"],
        "loan_deduction_calculated": period_farmer_data["loan_deduction_calculated"],
        "loan_pending_calculated": pending_farmer_data["loan_deduction_calculated"],
        "has_unpaid_cp_loan": point.id
        in collection_point_unpaid_loan_point_ids([point.id]),
        "net_amount": net_amount,
        "paid_amount": paid_amount,
        "balance_amount": balance_amount,
        "deduct_loan": period_farmer_data.get("deduct_loan", True),
        "can_make_payment": can_make_payment,
        "needs_carry_forward": action_flags["needs_carry_forward"],
        "carry_forward_amount": action_flags["carry_forward_amount"],
        "is_fully_settled": action_flags["is_fully_settled"],
        "rate": period_farmer_data["rate"],
        "gross_amount": period_farmer_data["gross_amount"],
        "collector_fee_amount": collector_fee_amount,
        "additional_amount": additional_amount,
        "previous_outstanding_amount": previous_outstanding,
        "previous_outstanding_pending": previous_outstanding_pending,
        "payment_correction_amount": payment_correction,
        "total_quantity": total_qty.quantize(Decimal("0.01")),
    }
    row = collection_point_row_with_payment_snapshot(row, period_payment)
    # Snapshot may replace NET; keep paid/balance consistent with remaining payable.
    if period_payment and row.get("net_amount") is not None:
        snap_net = Decimal(row["net_amount"]).quantize(Decimal("0.01"))
        row["balance_amount"] = balance_amount
        row["paid_amount"] = max(
            Decimal("0"), (snap_net - balance_amount).quantize(Decimal("0.01"))
        )
        row["can_make_payment"] = action_flags["can_make_payment"]
        row["needs_carry_forward"] = action_flags["needs_carry_forward"]
        row["carry_forward_amount"] = action_flags["carry_forward_amount"]
        row["is_fully_settled"] = action_flags["is_fully_settled"]

    q = lambda v: str((v or Decimal("0")).quantize(Decimal("0.01")))
    return {
        "advance_amount": q(row.get("advance_amount")),
        "advance_pending": q(row.get("advance_pending")),
        "goods_deduction": q(row.get("goods_deduction")),
        "goods_pending": q(row.get("goods_pending")),
        "loan_deduction": q(row.get("loan_deduction")),
        "loan_deduction_calculated": q(row.get("loan_deduction_calculated")),
        "loan_pending_calculated": q(row.get("loan_pending_calculated")),
        "has_unpaid_cp_loan": bool(row.get("has_unpaid_cp_loan")),
        "previous_outstanding_amount": q(row.get("previous_outstanding_amount")),
        "previous_outstanding_pending": q(row.get("previous_outstanding_pending")),
        "payment_correction_amount": q(row.get("payment_correction_amount")),
        "net_amount": q(row.get("net_amount")),
        "paid_amount": q(row.get("paid_amount")),
        "balance_amount": q(row.get("balance_amount")),
        "deduct_loan": bool(row.get("deduct_loan", True)),
        "can_make_payment": bool(row.get("can_make_payment")),
        "needs_carry_forward": bool(row.get("needs_carry_forward")),
        "carry_forward_amount": q(row.get("carry_forward_amount")),
        "is_fully_settled": bool(row.get("is_fully_settled")),
    }


def _collection_point_quantity(milk_row, branch):
    if branch and branch.collection_unit == Branch.CollectionUnit.LITERS:
        return milk_row.liters or Decimal("0")
    return milk_row.kg or Decimal("0")


def collection_point_paid_collector_fee_map(point_ids, branch_ids, start, end, points_by_id=None):
    """Collector fee earned from collection point milk marked paid in the period."""
    from .payment_recording import collection_point_period_payment_map

    amounts = defaultdict(lambda: Decimal("0"))
    if not point_ids:
        return amounts
    payments_map = collection_point_period_payment_map(point_ids, start, end)
    points_by_id = points_by_id or {
        p.id: p
        for p in CollectionPoint.objects.filter(id__in=point_ids).only("id", "collector_fee")
    }
    paid_qty = defaultdict(Decimal)
    milk_qs = MilkCollection.objects.filter(
        source=CollectionSource.POINT,
        date__gte=start,
        date__lte=end,
        branch_id__in=branch_ids,
        collection_point_id__in=point_ids,
        is_paid=True,
    ).select_related("branch")
    for row in milk_qs:
        if not row.collection_point_id:
            continue
        paid_qty[row.collection_point_id] += _collection_point_quantity(row, row.branch)
    for point_id in point_ids:
        payment = payments_map.get(point_id)
        if payment and payment.collector_fee_amount is not None:
            amounts[point_id] = payment.collector_fee_amount.quantize(Decimal("0.01"))
            continue
        point = points_by_id.get(point_id)
        if point is None:
            continue
        fee_rate = (point.collector_fee or Decimal("0")).quantize(Decimal("0.01"))
        amounts[point_id] = (paid_qty[point_id] * fee_rate).quantize(Decimal("0.01"))
    return amounts


def _collection_point_issue_id_candidates(point):
    id_candidates = {point.pk}
    try:
        id_candidates.add(int(str(point.number).strip()))
    except (ValueError, TypeError):
        pass
    return id_candidates


def _collection_point_direct_goods_data(
    point, branch_ids, start, end, pending_only=False, skip_map=None
):
    """Goods issued directly to a collection point in the payment period."""
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)
    goods_filter = {
        "issue_to_type": FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
        "issue_to_id__in": _collection_point_issue_id_candidates(point),
        "date__gte": start_dt,
        "date__lt": end_dt,
    }
    if pending_only:
        goods_filter["is_settled"] = False
    goods_rows = list(
        FarmerGoodsIssue.objects.filter(**goods_filter)
        .filter(
            driver_status=FarmerGoodsIssue.DriverStatus.ACCEPTED,
        )
        .select_related("product")
        .only(
            "id",
            "issue_to_id",
            "product_id",
            "quantity",
            "date",
            "batch_ref",
            "is_settled",
            "settled_at",
            "driver_status",
            "product__name",
            "product__category",
        )
        .order_by("-date", "-id")
    )
    if skip_map is not None:
        skip_bucket = skip_map.get(point.id, {})
        goods_rows = [
            goods for goods in goods_rows if not goods_issue_is_skipped(goods, skip_bucket)
        ]
    (
        goods_items,
        goods_total,
        feed_total,
        vitamin_total,
        omi_total,
        minerals_total,
        others_total,
    ) = summarize_priced_farmer_goods_issues(goods_rows)
    return {
        "goods_deduction": goods_total,
        "goods_feed_deduction": feed_total,
        "goods_vitamin_deduction": vitamin_total,
        "goods_omi_deduction": omi_total,
        "goods_minerals_deduction": minerals_total,
        "goods_others_deduction": others_total,
        "goods_items": goods_items,
    }


def _collection_point_own_advance_data(
    point, start, end, pending_only=False, skip_map=None, deduct_map=None
):
    """Advances issued directly to a collection point, including forwarded remainders."""
    skip_bucket = (skip_map or {}).get(point.id, {}) if skip_map is not None else {}
    skip_ids = skip_bucket.get("advance_ids", set()) if skip_bucket else set()
    if deduct_map is None:
        deduct_map = collection_point_advance_deduct_map([point.id], start, end)
    point_deducts = (deduct_map or {}).get(point.id, {})
    advance_total = Decimal("0")
    bank_loan_total = Decimal("0")
    seen = set()

    for advance in collection_point_advances_available_for_period(
        point.id, start, end, pending_only=True
    ).only("id", "amount", "advance_type", "recovered_amount"):
        remaining = collection_point_advance_remaining(advance)
        deduct = planned_collection_point_advance_deduct(
            advance.id, remaining, skip_ids, point_deducts
        )
        seen.add(advance.id)
        if deduct <= 0:
            continue
        advance_amt, bank_loan_amt = split_advance_amounts(deduct, advance.advance_type)
        advance_total += advance_amt
        bank_loan_total += bank_loan_amt

    if not pending_only:
        extra_ids = set(point_deducts.keys()) - seen
        if extra_ids:
            for advance in CollectionPointAdvancePayment.objects.filter(id__in=extra_ids).only(
                "id", "amount", "advance_type"
            ):
                deduct = Decimal(point_deducts.get(advance.id) or 0).quantize(Decimal("0.01"))
                if deduct <= 0:
                    continue
                advance_amt, bank_loan_amt = split_advance_amounts(
                    deduct, advance.advance_type
                )
                advance_total += advance_amt
                bank_loan_total += bank_loan_amt

    return {
        "advance_amount": advance_total.quantize(Decimal("0.01")),
        "bank_loan_amount": bank_loan_total.quantize(Decimal("0.01")),
    }


def _collection_point_own_loan_data(
    point, start, end, pending_only=False, cp_skip_map=None, cp_loan_settings_map=None
):
    """Loan instalment totals for collection point loans (not linked farmers)."""
    if cp_skip_map is None:
        cp_skip_map = collection_point_payment_period_deduction_skip_map([point.id], start, end)
    if cp_loan_settings_map is None:
        cp_loan_settings_map = cp_loan_deduction_settings_map([point.id], start, end)
    amounts = cp_loan_deduction_amount_map(
        [point.id],
        start,
        end,
        pending_only=pending_only,
        skip_map=cp_skip_map,
        loan_settings_map=cp_loan_settings_map,
    )
    calculated = amounts.get(point.id, Decimal("0")).quantize(Decimal("0.01"))
    deduct_loan = cp_loan_settings_map.get(point.id, True)
    applied = calculated if deduct_loan else Decimal("0.00")
    return {
        "loan_deduction_calculated": calculated,
        "loan_deduction": applied,
        "deduct_loan": deduct_loan,
        "cp_loan_in_period": calculated,
    }


def _aggregate_point_farmer_payment_data(
    farmer_ids, branch_ids, start, end, point=None, pending_only=False, cp_skip_map=None
):
    empty = {
        "rate": Decimal("0.00"),
        "gross_amount": Decimal("0.00"),
        "advance_amount": Decimal("0.00"),
        "goods_deduction": Decimal("0.00"),
        "goods_feed_deduction": Decimal("0.00"),
        "goods_vitamin_deduction": Decimal("0.00"),
        "goods_omi_deduction": Decimal("0.00"),
        "goods_minerals_deduction": Decimal("0.00"),
        "goods_others_deduction": Decimal("0.00"),
        "loan_deduction": Decimal("0.00"),
        "loan_deduction_calculated": Decimal("0.00"),
    }
    if not farmer_ids and point is None:
        return empty

    summaries = (
        build_farmer_payment_summaries(
            farmer_ids, branch_ids, start, end, pending_only=pending_only
        )
        if farmer_ids
        else {}
    )
    if farmer_ids:
        from .payment_recording import farmer_period_payment_map

        farmer_payments = farmer_period_payment_map(farmer_ids, start, end)
        for farmer_id, summary in list(summaries.items()):
            payment = farmer_payments.get(farmer_id)
            if payment and payment.rate is not None:
                summaries[farmer_id] = summary_with_payment_snapshot(summary, payment)
    rates = []
    totals = {key: Decimal("0") for key in empty if key != "rate"}
    for summary in summaries.values():
        rates.append(summary["rate"])
        totals["gross_amount"] += summary["gross_amount"]
        totals["advance_amount"] += summary["advance_amount"]
        totals["goods_deduction"] += summary["goods_deduction"]
        totals["goods_feed_deduction"] += summary["goods_feed_deduction"]
        totals["goods_vitamin_deduction"] += summary["goods_vitamin_deduction"]
        totals["goods_omi_deduction"] += summary["goods_omi_deduction"]
        totals["goods_minerals_deduction"] += summary["goods_minerals_deduction"]
        totals["goods_others_deduction"] += summary["goods_others_deduction"]
        totals["loan_deduction"] += summary["loan_deduction"]
        totals["loan_deduction_calculated"] += summary["loan_deduction_calculated"]

    if point is not None:
        if cp_skip_map is None:
            cp_skip_map = collection_point_payment_period_deduction_skip_map(
                [point.id], start, end
            )
        deduct_map = collection_point_advance_deduct_map([point.id], start, end)
        point_goods = _collection_point_direct_goods_data(
            point,
            branch_ids,
            start,
            end,
            pending_only=pending_only,
            skip_map=cp_skip_map,
        )
        totals["goods_deduction"] += point_goods["goods_deduction"]
        totals["goods_feed_deduction"] += point_goods["goods_feed_deduction"]
        totals["goods_vitamin_deduction"] += point_goods["goods_vitamin_deduction"]
        totals["goods_omi_deduction"] += point_goods["goods_omi_deduction"]
        totals["goods_minerals_deduction"] += point_goods["goods_minerals_deduction"]
        totals["goods_others_deduction"] += point_goods["goods_others_deduction"]
        point_adv = _collection_point_own_advance_data(
            point,
            start,
            end,
            pending_only=pending_only,
            skip_map=cp_skip_map,
            deduct_map=deduct_map,
        )
        totals["advance_amount"] += point_adv["advance_amount"]
        totals["loan_deduction"] += point_adv["bank_loan_amount"]
        totals["loan_deduction_calculated"] += point_adv["bank_loan_amount"]
        cp_loan = _collection_point_own_loan_data(
            point,
            start,
            end,
            pending_only=pending_only,
            cp_skip_map=cp_skip_map,
        )
        totals["loan_deduction"] += cp_loan["loan_deduction"]
        totals["loan_deduction_calculated"] += cp_loan["loan_deduction_calculated"]

    result = {
        "rate": (sum(rates) / len(rates)).quantize(Decimal("0.01")) if rates else Decimal("0.00"),
        **{key: value.quantize(Decimal("0.01")) for key, value in totals.items()},
    }
    if point is not None:
        cp_period = _collection_point_own_loan_data(
            point, start, end, pending_only=False, cp_skip_map=cp_skip_map
        )
        cp_pending = _collection_point_own_loan_data(
            point, start, end, pending_only=True, cp_skip_map=cp_skip_map
        )
        result["cp_loan_in_period"] = cp_period["cp_loan_in_period"]
        result["deduct_loan"] = cp_pending["deduct_loan"]
    return result


def _farmers_by_collection_point(point_ids):
    mapping = defaultdict(list)
    if not point_ids:
        return mapping
    for row in Farmer.objects.filter(collection_point_id__in=point_ids).values("id", "collection_point_id"):
        mapping[row["collection_point_id"]].append(row["id"])
    return mapping


def collection_point_sheet_paid_and_balance(
    point,
    branch_ids,
    start,
    end,
    period_farmer_data,
    pending_farmer_data,
    paid_collector_fee=None,
    cash_paid=None,
    collector_fee_amount=None,
    additional_amount=None,
    previous_outstanding=None,
    previous_outstanding_pending=None,
    payment_correction=None,
):
    """Paid/balance columns for the collection point payment sheet (full balance mode).

    Balance = remaining unpaid payable (pending).
    Paid = period NET − balance (settled portion of the period net).
    """
    period_farmer_data = period_farmer_data or {}
    pending_farmer_data = pending_farmer_data or {}

    fee_total = Decimal(collector_fee_amount or Decimal("0")).quantize(Decimal("0.01"))
    additional = Decimal(additional_amount or Decimal("0")).quantize(Decimal("0.01"))
    prev_os = Decimal(previous_outstanding or Decimal("0")).quantize(Decimal("0.01"))
    correction = Decimal(payment_correction or Decimal("0")).quantize(Decimal("0.01"))
    pending_os = previous_outstanding_pending
    if pending_os is None:
        pending_os = collection_point_previous_outstanding_amount(
            point.id, end, pending_only=True
        )
    pending_os = Decimal(pending_os or Decimal("0")).quantize(Decimal("0.01"))
    period_net = collection_point_period_net_amount(
        gross_amount=period_farmer_data.get("gross_amount", Decimal("0")),
        advance_amount=period_farmer_data.get("advance_amount", Decimal("0")),
        goods_deduction=period_farmer_data.get("goods_deduction", Decimal("0")),
        loan_deduction=period_farmer_data.get("loan_deduction", Decimal("0")),
        previous_outstanding=prev_os,
        collector_fee_amount=fee_total,
        additional_amount=additional,
        payment_correction=correction,
    )

    balance_amount = collection_point_balance_for_period(
        point,
        branch_ids,
        start,
        end,
        balance_payment=True,
        previous_outstanding=pending_os,
        payment_correction=correction,
    )
    paid_amount = max(Decimal("0"), (period_net - balance_amount).quantize(Decimal("0.01")))
    cash = Decimal(cash_paid or Decimal("0")).quantize(Decimal("0.01"))
    fee_paid = Decimal(paid_collector_fee or Decimal("0")).quantize(Decimal("0.01"))

    period_advance = period_farmer_data.get("advance_amount", Decimal("0"))
    period_goods = period_farmer_data.get("goods_deduction", Decimal("0"))
    period_loan = period_farmer_data.get("loan_deduction_calculated", Decimal("0"))
    pending_advance = pending_farmer_data.get("advance_amount", Decimal("0"))
    pending_goods = pending_farmer_data.get("goods_deduction", Decimal("0"))
    pending_loan = pending_farmer_data.get("loan_deduction_calculated", Decimal("0"))

    settled_advance = (period_advance - pending_advance).quantize(Decimal("0.01"))
    settled_goods = (period_goods - pending_goods).quantize(Decimal("0.01"))
    settled_loan = (period_loan - pending_loan).quantize(Decimal("0.01"))
    settled_outstanding = (prev_os - pending_os).quantize(Decimal("0.01"))

    return {
        "paid_amount": paid_amount,
        "balance_amount": balance_amount,
        "cash_paid": cash,
        "collector_fee_paid": fee_paid,
        "settled_advance": settled_advance,
        "settled_goods": settled_goods,
        "settled_loan": settled_loan,
        "settled_outstanding": settled_outstanding,
        "collector_fee_balance": max(Decimal("0"), fee_total - fee_paid).quantize(
            Decimal("0.01")
        ),
    }


def build_collection_point_payment_sheet_data(branch_ids, start, end):
    """Daily point collection qty (branch unit), farmer aggregates, and collector fee."""
    from milk_collections.reconcile_payment import build_effective_payment_qty_maps
    from .payment_recording import (
        collection_point_period_payment_map,
        sync_collection_point_milk_paid_from_period_payments,
    )

    sync_collection_point_milk_paid_from_period_payments(
        sheet_start=start, sheet_end=end, branch_ids=branch_ids
    )
    date_cols = date_range_inclusive(start, end)
    milk_qs = (
        MilkCollection.objects.filter(
            source=CollectionSource.POINT,
            date__gte=start,
            date__lte=end,
            branch_id__in=branch_ids,
        )
        .select_related("collection_point", "branch")
        .only(
            "date",
            "kg",
            "liters",
            "collection_point_id",
            "branch_id",
            "branch__collection_unit",
            "is_paid",
        )
    )
    point_ids = set()
    for row in milk_qs:
        if row.collection_point_id:
            point_ids.add(row.collection_point_id)

    farmers_map = _farmers_by_collection_point(point_ids)
    points = list(
        CollectionPoint.objects.filter(id__in=point_ids)
        .select_related("route", "route__branch")
        .prefetch_related("bank_accounts")
        .order_by("route__branch__code", "route__code", "number")
    )
    effective_qty_map, paid_qty_by_point_day, unpaid_qty_by_point_day, _reconcile_meta = (
        build_effective_payment_qty_maps(points, branch_ids, start, end, date_cols)
    )
    points_by_id = {point.id: point for point in points}
    paid_collection_map = collection_point_paid_collector_fee_map(
        list(point_ids), branch_ids, start, end, points_by_id=points_by_id
    )
    cash_paid_map, payments_by_point = collection_point_overlapping_paid_data(
        list(point_ids), start, end
    )
    period_payments_map = collection_point_period_payment_map(list(point_ids), start, end)
    period_os_map = collection_point_previous_outstanding_map(
        list(point_ids), end, pending_only=False, period_start=start, period_end=end
    )
    pending_os_map = collection_point_previous_outstanding_map(
        list(point_ids), end, pending_only=True
    )
    payment_correction_map = collection_point_payment_correction_map(
        list(point_ids), start, end
    )
    unpaid_loan_point_ids = collection_point_unpaid_loan_point_ids(list(point_ids))
    rows = []
    unit_labels = set()
    for point in points:
        branch = point.route.branch if point.route_id else None
        unit_label = branch.get_collection_unit_display() if branch else Branch.CollectionUnit.KG.label
        unit_labels.add(unit_label)
        day_cols = []
        day_vals = []
        has_unpaid_collections = False
        for day in date_cols:
            total_qty = effective_qty_map.get((point.id, day), Decimal("0"))
            paid_qty = paid_qty_by_point_day.get((point.id, day), Decimal("0"))
            status = _day_collection_status(total_qty, paid_qty)
            if status in ("unpaid", "partial"):
                has_unpaid_collections = True
            qty_value = total_qty.quantize(Decimal("0.01"))
            day_vals.append(qty_value)
            day_cols.append(
                {
                    "date": day,
                    "quantity": qty_value,
                    "paid_quantity": paid_qty.quantize(Decimal("0.01")),
                    "status": status,
                }
            )
        total_qty = sum(day_vals, Decimal("0")).quantize(Decimal("0.01"))
        payable_qty = sum(
            (unpaid_qty_by_point_day.get((point.id, d), Decimal("0")) for d in date_cols),
            Decimal("0"),
        ).quantize(Decimal("0.01"))
        fee_rate = (point.collector_fee or Decimal("0")).quantize(Decimal("0.01"))
        collector_fee_amount = (total_qty * fee_rate).quantize(Decimal("0.01"))
        payable_collector_fee = (payable_qty * fee_rate).quantize(Decimal("0.01"))
        additional_rate = collection_point_additional_rate(point)
        additional_amount, _additional_qty = collection_point_additional_amount_for_period(
            point, branch_ids, start, end
        )
        farmer_ids = farmers_map.get(point.id, [])
        pending_farmer_data = _aggregate_point_farmer_payment_data(
            farmer_ids, branch_ids, start, end, point=point, pending_only=True
        )
        period_farmer_data = _aggregate_point_farmer_payment_data(
            farmer_ids, branch_ids, start, end, point=point, pending_only=False
        )
        period_farmer_data = {
            **period_farmer_data,
            "collector_fee_amount": collector_fee_amount,
        }
        previous_outstanding = period_os_map.get(point.id, Decimal("0"))
        previous_outstanding_pending = pending_os_map.get(point.id, Decimal("0"))
        payment_correction = payment_correction_map.get(point.id, Decimal("0"))
        row_net_amount = collection_point_period_net_amount(
            gross_amount=period_farmer_data["gross_amount"],
            advance_amount=period_farmer_data["advance_amount"],
            goods_deduction=period_farmer_data["goods_deduction"],
            loan_deduction=period_farmer_data["loan_deduction"],
            previous_outstanding=previous_outstanding,
            collector_fee_amount=collector_fee_amount,
            additional_amount=additional_amount,
            payment_correction=payment_correction,
        )
        allow_zero_settlement = collection_point_has_zero_cash_settlement_items(
            advance_amount=period_farmer_data["advance_amount"],
            goods_deduction=period_farmer_data["goods_deduction"],
            loan_deduction=period_farmer_data["loan_deduction_calculated"],
            previous_outstanding=previous_outstanding_pending,
        ) and row_net_amount == Decimal("0.00")
        payment_totals = collection_point_sheet_paid_and_balance(
            point,
            branch_ids,
            start,
            end,
            period_farmer_data,
            pending_farmer_data,
            paid_collector_fee=paid_collection_map.get(point.id, Decimal("0")),
            cash_paid=cash_paid_map.get(point.id, Decimal("0")),
            collector_fee_amount=collector_fee_amount,
            additional_amount=additional_amount,
            previous_outstanding=previous_outstanding,
            previous_outstanding_pending=previous_outstanding_pending,
            payment_correction=payment_correction,
        )
        paid_amount = payment_totals["paid_amount"]
        balance_amount = payment_totals["balance_amount"]
        period_payments = payments_by_point.get(point.id, [])
        period_payment = period_payments_map.get(point.id)
        payable_net = collection_point_pending_payable_amount(
            point,
            branch_ids,
            start,
            end,
            balance_payment=True,
            previous_outstanding=previous_outstanding_pending,
            payment_correction=payment_correction,
        )
        action_flags = collection_point_payment_action_flags(
            payable_net,
            period_payment,
            allow_zero_settlement=allow_zero_settlement,
        )
        can_make_payment = action_flags["can_make_payment"]
        bank_accounts = list(point.bank_accounts.all())
        row = {
                "point": point,
                "branch": branch,
                "unit_label": unit_label,
                "collector_fee_rate": fee_rate,
                "additional_rate": additional_rate,
                "day_vals": day_vals,
                "day_cols": day_cols,
                "has_unpaid_collections": has_unpaid_collections,
                "total_quantity": total_qty,
                "payable_quantity": payable_qty,
                "collector_fee_amount": collector_fee_amount,
                "additional_amount": additional_amount,
                "previous_outstanding_amount": previous_outstanding,
                "previous_outstanding_pending": previous_outstanding_pending,
                "payment_correction_amount": payment_correction,
                "rate": period_farmer_data["rate"],
                "gross_amount": period_farmer_data["gross_amount"],
                "advance_amount": period_farmer_data["advance_amount"],
                "advance_pending": pending_farmer_data["advance_amount"],
                "goods_deduction": period_farmer_data["goods_deduction"],
                "goods_pending": pending_farmer_data["goods_deduction"],
                "loan_deduction": pending_farmer_data["loan_deduction"],
                "loan_deduction_calculated": period_farmer_data["loan_deduction_calculated"],
                "loan_pending_calculated": pending_farmer_data["loan_deduction_calculated"],
                "deduct_loan": period_farmer_data.get("deduct_loan", True),
                "cp_loan_in_period": period_farmer_data.get("cp_loan_in_period", Decimal("0")),
                "has_unpaid_cp_loan": point.id in unpaid_loan_point_ids,
                "net_amount": row_net_amount,
                "payable_net": payable_net,
                "cash_paid": payment_totals["cash_paid"],
                "paid_advance": payment_totals["settled_advance"],
                "paid_goods": payment_totals["settled_goods"],
                "paid_loan": payment_totals["settled_loan"],
                "paid_outstanding": payment_totals["settled_outstanding"],
                "period_payment": period_payments[0] if period_payments else None,
                "period_payments": period_payments,
                "paid_amount": paid_amount,
                "payment_amount": (
                    Decimal("0.00")
                    if action_flags["needs_carry_forward"]
                    else balance_amount
                ),
                "balance_amount": balance_amount,
                "collector_fee_balance": payment_totals["collector_fee_balance"],
                "can_make_payment": can_make_payment,
                "needs_carry_forward": action_flags["needs_carry_forward"],
                "carry_forward_amount": action_flags["carry_forward_amount"],
                "is_fully_settled": action_flags["is_fully_settled"],
                "uses_payment_snapshot": False,
                "bank_accounts_json": json.dumps(
                    [
                        {
                            "id": account.pk,
                            "label": account.label,
                            "primary": account.is_primary,
                            "bank_code": account.bank_code or "",
                            "branch_code": account.branch_code or "",
                            "ceft_ready": account.is_ceft_ready,
                        }
                        for account in bank_accounts
                    ]
                ),
                "primary_bank_account_id": next(
                    (account.pk for account in bank_accounts if account.is_primary),
                    bank_accounts[0].pk if bank_accounts else None,
                ),
                "primary_bank_ceft_ready": bool(
                    next(
                        (
                            account
                            for account in bank_accounts
                            if account.is_primary and account.is_ceft_ready
                        ),
                        next((account for account in bank_accounts if account.is_ceft_ready), None),
                    )
                ),
            }
        rows.append(collection_point_row_with_payment_snapshot(row, period_payment))

    mixed_units = len(unit_labels) > 1
    day_totals = [Decimal("0")] * len(date_cols)
    sheet_totals = {
        "day_vals": day_totals,
        "total_quantity": Decimal("0"),
        "gross_amount": Decimal("0"),
        "advance_amount": Decimal("0"),
        "goods_deduction": Decimal("0"),
        "loan_deduction": Decimal("0"),
        "collector_fee_amount": Decimal("0"),
        "additional_amount": Decimal("0"),
        "previous_outstanding_amount": Decimal("0"),
        "payment_correction_amount": Decimal("0"),
        "net_amount": Decimal("0"),
        "paid_amount": Decimal("0"),
        "balance_amount": Decimal("0"),
        "cash_paid": Decimal("0"),
        "paid_advance": Decimal("0"),
        "paid_goods": Decimal("0"),
        "paid_loan": Decimal("0"),
        "paid_outstanding": Decimal("0"),
        "mixed_units": mixed_units,
        "unit_label": next(iter(unit_labels)) if len(unit_labels) == 1 else "",
    }
    for row in rows:
        for i, qty in enumerate(row["day_vals"]):
            if not mixed_units:
                sheet_totals["day_vals"][i] += qty or Decimal("0")
        if not mixed_units:
            sheet_totals["total_quantity"] += row["total_quantity"] or Decimal("0")
        sheet_totals["gross_amount"] += row["gross_amount"] or Decimal("0")
        sheet_totals["advance_amount"] += row["advance_amount"] or Decimal("0")
        sheet_totals["goods_deduction"] += row["goods_deduction"] or Decimal("0")
        sheet_totals["loan_deduction"] += row["loan_deduction"] or Decimal("0")
        sheet_totals["collector_fee_amount"] += row["collector_fee_amount"] or Decimal("0")
        sheet_totals["additional_amount"] += row["additional_amount"] or Decimal("0")
        sheet_totals["previous_outstanding_amount"] += row.get("previous_outstanding_amount") or Decimal("0")
        sheet_totals["payment_correction_amount"] += row.get("payment_correction_amount") or Decimal("0")
        sheet_totals["net_amount"] += row["net_amount"] or Decimal("0")
        sheet_totals["paid_amount"] += row["paid_amount"] or Decimal("0")
        sheet_totals["balance_amount"] += row["balance_amount"] or Decimal("0")
        sheet_totals["cash_paid"] += row.get("cash_paid") or Decimal("0")
        sheet_totals["paid_advance"] += row.get("paid_advance") or Decimal("0")
        sheet_totals["paid_goods"] += row.get("paid_goods") or Decimal("0")
        sheet_totals["paid_loan"] += row.get("paid_loan") or Decimal("0")
        sheet_totals["paid_outstanding"] += row.get("paid_outstanding") or Decimal("0")
    cash_method_total = Decimal("0")
    bank_method_total = Decimal("0")
    cash_method_count = 0
    bank_method_count = 0
    rate_sum = Decimal("0")
    rate_count = 0
    bank_method = CollectionPointPeriodPayment.PaymentMethod.BANK_TRANSFER
    for row in rows:
        rate = row.get("rate")
        if rate is not None:
            rate_sum += Decimal(rate)
            rate_count += 1
        if row.get("can_make_payment"):
            amount = Decimal(row.get("payment_amount") or 0)
            if amount <= 0:
                continue
            period_payment = row.get("period_payment")
            if period_payment and (period_payment.payment_method or ""):
                method = period_payment.payment_method
            elif row.get("primary_bank_account_id"):
                method = bank_method
            else:
                method = CollectionPointPeriodPayment.PaymentMethod.CASH
            if method == bank_method:
                bank_method_total += amount
                bank_method_count += 1
            else:
                cash_method_total += amount
                cash_method_count += 1
        else:
            for payment in row.get("period_payments") or []:
                amount = Decimal(payment.amount or 0)
                if amount <= 0:
                    continue
                if (payment.payment_method or "") == bank_method:
                    bank_method_total += amount
                    bank_method_count += 1
                else:
                    cash_method_total += amount
                    cash_method_count += 1
    point_count = len(rows)
    sheet_totals["point_count"] = point_count
    sheet_totals["cash_method_total"] = cash_method_total
    sheet_totals["bank_method_total"] = bank_method_total
    sheet_totals["cash_method_count"] = cash_method_count
    sheet_totals["bank_method_count"] = bank_method_count
    sheet_totals["avg_point_rate"] = (
        (rate_sum / Decimal(rate_count)).quantize(Decimal("0.01")) if rate_count else None
    )
    sheet_totals["avg_point_collection"] = (
        (sheet_totals["total_quantity"] / Decimal(point_count)).quantize(Decimal("0.01"))
        if point_count and not mixed_units
        else None
    )
    day_count = len(date_cols) or 1
    sheet_totals["day_count"] = day_count
    total_kg = Decimal("0")
    total_liters = Decimal("0")
    factor = MILK_LITER_FACTOR
    for row in rows:
        qty = Decimal(row.get("total_quantity") or 0)
        unit = (row.get("unit_label") or "").strip().lower()
        if unit.startswith("liter"):
            total_liters += qty
            total_kg += (qty / factor) if factor else qty
        else:
            total_kg += qty
            total_liters += (qty * factor) if factor else qty
    total_kg = total_kg.quantize(Decimal("0.01"))
    total_liters = total_liters.quantize(Decimal("0.01"))
    sheet_totals["total_kg"] = total_kg
    sheet_totals["total_liters"] = total_liters
    # Most accurate daily average: total milk ÷ points ÷ days (both units).
    if point_count:
        sheet_totals["avg_daily_point_collection_kg"] = (
            total_kg / Decimal(point_count) / Decimal(day_count)
        ).quantize(Decimal("0.01"))
        sheet_totals["avg_daily_point_collection_liters"] = (
            total_liters / Decimal(point_count) / Decimal(day_count)
        ).quantize(Decimal("0.01"))
    else:
        sheet_totals["avg_daily_point_collection_kg"] = None
        sheet_totals["avg_daily_point_collection_liters"] = None
    unit_label_l = (sheet_totals.get("unit_label") or "").strip().lower()
    if unit_label_l.startswith("liter"):
        sheet_totals["avg_daily_point_collection"] = sheet_totals[
            "avg_daily_point_collection_liters"
        ]
    else:
        sheet_totals["avg_daily_point_collection"] = sheet_totals["avg_daily_point_collection_kg"]
    # Most accurate rate: weighted by quantity (gross ÷ total milk), not mean of rates.
    gross_amount = Decimal(sheet_totals.get("gross_amount") or 0)
    if total_kg > 0:
        sheet_totals["avg_point_rate_per_kg"] = (gross_amount / total_kg).quantize(Decimal("0.01"))
    else:
        sheet_totals["avg_point_rate_per_kg"] = None
    if total_liters > 0:
        sheet_totals["avg_point_rate_per_liter"] = (gross_amount / total_liters).quantize(
            Decimal("0.01")
        )
    else:
        sheet_totals["avg_point_rate_per_liter"] = None
    if not mixed_units and sheet_totals.get("total_quantity"):
        sheet_totals["avg_point_rate"] = (
            gross_amount / Decimal(sheet_totals["total_quantity"])
        ).quantize(Decimal("0.01"))
    elif sheet_totals["avg_point_rate_per_kg"] is not None and not unit_label_l.startswith("liter"):
        sheet_totals["avg_point_rate"] = sheet_totals["avg_point_rate_per_kg"]
    elif sheet_totals["avg_point_rate_per_liter"] is not None:
        sheet_totals["avg_point_rate"] = sheet_totals["avg_point_rate_per_liter"]
    sheet_totals["total_payment"] = (cash_method_total + bank_method_total).quantize(Decimal("0.01"))
    if not mixed_units:
        sheet_totals["day_vals"] = [v.quantize(Decimal("0.01")) for v in sheet_totals["day_vals"]]
        sheet_totals["total_quantity"] = sheet_totals["total_quantity"].quantize(Decimal("0.01"))
    for key in (
        "gross_amount",
        "advance_amount",
        "goods_deduction",
        "loan_deduction",
        "collector_fee_amount",
        "additional_amount",
        "previous_outstanding_amount",
        "payment_correction_amount",
        "net_amount",
        "paid_amount",
        "balance_amount",
        "cash_paid",
        "paid_advance",
        "paid_goods",
        "paid_loan",
        "paid_outstanding",
        "cash_method_total",
        "bank_method_total",
    ):
        sheet_totals[key] = sheet_totals[key].quantize(Decimal("0.01"))
    sheet_totals["show_additional_column"] = sheet_totals["additional_amount"] > Decimal("0")
    sheet_totals["show_payment_correction_column"] = (
        sheet_totals["payment_correction_amount"] != Decimal("0")
    )
    return date_cols, rows, sheet_totals


def sum_payment_sheet_row_totals(rows, date_cols, mixed_units):
    """Aggregate payment-sheet numeric columns for a subset of rows."""
    day_totals = [Decimal("0")] * len(date_cols)
    totals = {
        "day_vals": day_totals,
        "total_quantity": Decimal("0"),
        "gross_amount": Decimal("0"),
        "advance_amount": Decimal("0"),
        "goods_deduction": Decimal("0"),
        "loan_deduction": Decimal("0"),
        "collector_fee_amount": Decimal("0"),
        "additional_amount": Decimal("0"),
        "previous_outstanding_amount": Decimal("0"),
        "payment_correction_amount": Decimal("0"),
        "net_amount": Decimal("0"),
        "paid_amount": Decimal("0"),
        "balance_amount": Decimal("0"),
        "cash_paid": Decimal("0"),
        "paid_advance": Decimal("0"),
        "paid_goods": Decimal("0"),
        "paid_loan": Decimal("0"),
        "paid_outstanding": Decimal("0"),
        "mixed_units": mixed_units,
        "point_count": len(rows),
    }
    for row in rows:
        for i, qty in enumerate(row["day_vals"]):
            if not mixed_units:
                totals["day_vals"][i] += qty or Decimal("0")
        if not mixed_units:
            totals["total_quantity"] += row["total_quantity"] or Decimal("0")
        totals["gross_amount"] += row["gross_amount"] or Decimal("0")
        totals["advance_amount"] += row["advance_amount"] or Decimal("0")
        totals["goods_deduction"] += row["goods_deduction"] or Decimal("0")
        totals["loan_deduction"] += row["loan_deduction"] or Decimal("0")
        totals["collector_fee_amount"] += row["collector_fee_amount"] or Decimal("0")
        totals["additional_amount"] += row["additional_amount"] or Decimal("0")
        totals["previous_outstanding_amount"] += row.get("previous_outstanding_amount") or Decimal("0")
        totals["payment_correction_amount"] += row.get("payment_correction_amount") or Decimal("0")
        totals["net_amount"] += row["net_amount"] or Decimal("0")
        totals["paid_amount"] += row["paid_amount"] or Decimal("0")
        totals["balance_amount"] += row["balance_amount"] or Decimal("0")
        totals["cash_paid"] += row.get("cash_paid") or Decimal("0")
        totals["paid_advance"] += row.get("paid_advance") or Decimal("0")
        totals["paid_goods"] += row.get("paid_goods") or Decimal("0")
        totals["paid_loan"] += row.get("paid_loan") or Decimal("0")
        totals["paid_outstanding"] += row.get("paid_outstanding") or Decimal("0")
    if not mixed_units:
        totals["day_vals"] = [v.quantize(Decimal("0.01")) for v in totals["day_vals"]]
        totals["total_quantity"] = totals["total_quantity"].quantize(Decimal("0.01"))
    for key in (
        "gross_amount",
        "advance_amount",
        "goods_deduction",
        "loan_deduction",
        "collector_fee_amount",
        "additional_amount",
        "previous_outstanding_amount",
        "payment_correction_amount",
        "net_amount",
        "paid_amount",
        "balance_amount",
        "cash_paid",
        "paid_advance",
        "paid_goods",
        "paid_loan",
        "paid_outstanding",
    ):
        totals[key] = totals[key].quantize(Decimal("0.01"))
    totals["show_additional_column"] = totals["additional_amount"] > Decimal("0")
    return totals


def group_collection_point_payment_sheet_by_route(rows, date_cols, mixed_units):
    """Group payment-sheet rows by route, preserving route order."""
    groups = []
    groups_by_key = {}
    for row in rows:
        point = row["point"]
        route = point.route if point.route_id else None
        key = route.pk if route else 0
        if key not in groups_by_key:
            group = {
                "route": route,
                "route_id": key,
                "route_label": f"{route.code} — {route.name}" if route else "No route",
                "branch": row.get("branch"),
                "rows": [],
            }
            groups_by_key[key] = group
            groups.append(group)
        groups_by_key[key]["rows"].append(row)
    day_count = len(date_cols) or 1
    bank_method = CollectionPointPeriodPayment.PaymentMethod.BANK_TRANSFER
    factor = MILK_LITER_FACTOR
    for group in groups:
        totals = sum_payment_sheet_row_totals(group["rows"], date_cols, mixed_units)
        cash_method_total = Decimal("0")
        bank_method_total = Decimal("0")
        cash_method_count = 0
        bank_method_count = 0
        total_kg = Decimal("0")
        total_liters = Decimal("0")
        for row in group["rows"]:
            qty = Decimal(row.get("total_quantity") or 0)
            unit = (row.get("unit_label") or "").strip().lower()
            if unit.startswith("liter"):
                total_liters += qty
                total_kg += (qty / factor) if factor else qty
            else:
                total_kg += qty
                total_liters += (qty * factor) if factor else qty
            if row.get("can_make_payment"):
                amount = Decimal(row.get("payment_amount") or 0)
                if amount <= 0:
                    continue
                period_payment = row.get("period_payment")
                if period_payment and (period_payment.payment_method or ""):
                    method = period_payment.payment_method
                elif row.get("primary_bank_account_id"):
                    method = bank_method
                else:
                    method = CollectionPointPeriodPayment.PaymentMethod.CASH
                if method == bank_method:
                    bank_method_total += amount
                    bank_method_count += 1
                else:
                    cash_method_total += amount
                    cash_method_count += 1
            else:
                for payment in row.get("period_payments") or []:
                    amount = Decimal(payment.amount or 0)
                    if amount <= 0:
                        continue
                    if (payment.payment_method or "") == bank_method:
                        bank_method_total += amount
                        bank_method_count += 1
                    else:
                        cash_method_total += amount
                        cash_method_count += 1
        point_count = totals["point_count"]
        total_kg = total_kg.quantize(Decimal("0.01"))
        total_liters = total_liters.quantize(Decimal("0.01"))
        totals["day_count"] = day_count
        totals["total_kg"] = total_kg
        totals["total_liters"] = total_liters
        totals["cash_method_total"] = cash_method_total.quantize(Decimal("0.01"))
        totals["bank_method_total"] = bank_method_total.quantize(Decimal("0.01"))
        totals["cash_method_count"] = cash_method_count
        totals["bank_method_count"] = bank_method_count
        totals["total_payment"] = (cash_method_total + bank_method_total).quantize(Decimal("0.01"))
        gross_amount = Decimal(totals.get("gross_amount") or 0)
        totals["avg_point_rate_per_kg"] = (
            (gross_amount / total_kg).quantize(Decimal("0.01")) if total_kg > 0 else None
        )
        totals["avg_point_rate_per_liter"] = (
            (gross_amount / total_liters).quantize(Decimal("0.01")) if total_liters > 0 else None
        )
        if point_count:
            totals["avg_daily_point_collection_kg"] = (
                total_kg / Decimal(point_count) / Decimal(day_count)
            ).quantize(Decimal("0.01"))
            totals["avg_daily_point_collection_liters"] = (
                total_liters / Decimal(point_count) / Decimal(day_count)
            ).quantize(Decimal("0.01"))
        else:
            totals["avg_daily_point_collection_kg"] = None
            totals["avg_daily_point_collection_liters"] = None
        group["totals"] = totals
    return groups


_FARMER_REG_NUM_RE = re.compile(r"^F[A-Za-z]{2}(\d{5})$")


def farmer_display_number(farmer):
    """Numeric farmer id from registration suffix, e.g. FGE00088 -> 88."""
    reg = (farmer.registration_number or "").strip()
    match = _FARMER_REG_NUM_RE.match(reg)
    if match:
        return str(int(match.group(1)))
    return reg or str(farmer.pk)


def _collection_point_farmer_sheet_rows(farmer_ids, branch_ids, start, end):
    """Per-farmer milk qty, rate, and payment lines for a collection point payslip."""
    if not farmer_ids:
        empty_totals = {
            "kg": Decimal("0.00"),
            "liters": Decimal("0.00"),
            "rate": Decimal("0.00"),
            "amount": Decimal("0.00"),
        }
        return [], empty_totals

    farmers = list(
        Farmer.objects.filter(id__in=farmer_ids)
        .select_related("branch")
        .order_by("common_name", "full_name")
    )
    summaries = build_farmer_payment_summaries(farmer_ids, branch_ids, start, end)
    from .payment_recording import farmer_period_payment_map

    farmer_payments = farmer_period_payment_map(farmer_ids, start, end)
    for farmer_id, summary in list(summaries.items()):
        payment = farmer_payments.get(farmer_id)
        if payment and payment.rate is not None:
            summaries[farmer_id] = summary_with_payment_snapshot(summary, payment)

    milk_agg = {}
    for row in (
        MilkCollection.objects.filter(
            source=CollectionSource.FARMER,
            farmer_id__in=farmer_ids,
            date__gte=start,
            date__lte=end,
            branch_id__in=branch_ids,
        )
        .values("farmer_id")
        .annotate(total_kg=Sum("kg"), total_liters=Sum("liters"))
    ):
        milk_agg[row["farmer_id"]] = {
            "kg": row["total_kg"] or Decimal("0"),
            "liters": row["total_liters"] or Decimal("0"),
        }

    factor_avg = {}
    for row in (
        MilkFactor.objects.filter(
            source=CollectionSource.FARMER,
            farmer_id__in=farmer_ids,
            date__gte=start,
            date__lte=end,
            route__branch_id__in=branch_ids,
        )
        .values("farmer_id")
        .annotate(avg_fat=Avg("fat"), avg_snf=Avg("snf"))
    ):
        factor_avg[row["farmer_id"]] = {
            "fat": row["avg_fat"],
            "snf": row["avg_snf"],
        }

    rows = []
    for farmer in farmers:
        summary = summaries.get(farmer.id)
        if summary is None:
            continue
        milk = milk_agg.get(farmer.id, {"kg": Decimal("0"), "liters": Decimal("0")})
        kg = (milk["kg"] or Decimal("0")).quantize(Decimal("0.01"))
        liters = (milk["liters"] or Decimal("0")).quantize(Decimal("0.01"))
        amount = summary["gross_amount"]
        if kg <= 0 and liters <= 0 and amount <= 0:
            continue
        factors = factor_avg.get(farmer.id, {})
        fat = factors.get("fat")
        snf = factors.get("snf")
        rows.append(
            {
                "number": farmer_display_number(farmer),
                "name": (farmer.common_name or farmer.full_name or "").strip().upper(),
                "kg": kg,
                "liters": liters,
                "fat": fat.quantize(Decimal("0.01")) if fat is not None else None,
                "snf": snf.quantize(Decimal("0.01")) if snf is not None else None,
                "rate": summary["rate"],
                "amount": amount,
            }
        )

    rows.sort(key=lambda row: (int(row["number"]) if row["number"].isdigit() else row["number"]))

    total_kg = sum((row["kg"] for row in rows), Decimal("0")).quantize(Decimal("0.01"))
    total_liters = sum((row["liters"] for row in rows), Decimal("0")).quantize(Decimal("0.01"))
    total_amount = sum((row["amount"] for row in rows), Decimal("0")).quantize(Decimal("0.01"))
    branch = None
    if farmers:
        branch = farmers[0].branch
        for farmer in farmers[1:]:
            if farmer.branch_id != getattr(branch, "id", None):
                branch = None
                break
    payment_qty = payment_qty_for_branch(branch, kg=total_kg, liters=total_liters)
    if payment_qty > 0:
        total_rate = (total_amount / payment_qty).quantize(Decimal("0.01"))
    elif rows:
        total_rate = (sum((row["rate"] for row in rows), Decimal("0")) / len(rows)).quantize(
            Decimal("0.01")
        )
    else:
        total_rate = Decimal("0.00")

    return rows, {
        "kg": total_kg,
        "liters": total_liters,
        "rate": total_rate,
        "amount": total_amount,
    }


def _collection_point_linked_farmer_payslip_details(farmer_ids, branch_ids, start, end, point=None):
    """Advance, loan, and goods line items for farmers and collection point goods."""
    advances = []
    loan_items = []
    goods_items = []
    if not farmer_ids and point is None:
        return {"advances": advances, "loan_items": loan_items, "goods_items": goods_items}

    farmer_ids = list(farmer_ids or [])
    farmers = {}
    farmer_names = {}
    if farmer_ids:
        farmers = {
            f.id: f
            for f in Farmer.objects.filter(id__in=farmer_ids).only(
                "id", "initial", "common_name", "full_name"
            )
        }
        farmer_names = {
            farmer_id: farmer_payslip_display_name(farmers[farmer_id])
            for farmer_id in farmers
        }

        advances = list(
            FarmerAdvancePayment.objects.filter(
                farmer_id__in=farmer_ids,
                date__gte=start,
                date__lte=end,
            )
            .select_related("farmer")
            .order_by("-date", "-id")
        )

    for farmer_id in farmer_ids:
        farmer = farmers.get(farmer_id)
        if farmer is None:
            continue
        items, _ = loan_deduction_items_for_farmer(farmer, start, end, pending_only=False)
        for item in items:
            loan_items.append(
                {
                    **item,
                    "farmer_id": farmer_id,
                    "farmer_name": farmer_names.get(farmer_id, ""),
                }
            )
    loan_items.sort(key=lambda row: (row["due_date"], row["loan_id"], row["installment_number"]))

    if farmer_ids:
        tz = timezone.get_current_timezone()
        start_dt = timezone.make_aware(datetime.combine(start, time.min), tz)
        end_dt = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)
        goods_by_farmer = defaultdict(list)
        goods_rows = list(
            FarmerGoodsIssue.objects.filter(
                issue_to_type=FarmerGoodsIssue.IssueToType.FARMER,
                issue_to_id__in=farmer_ids,
                date__gte=start_dt,
                date__lt=end_dt,
            )
            .select_related("product")
            .only(
                "issue_to_id",
                "product_id",
                "quantity",
                "date",
                "batch_ref",
                "is_settled",
                "settled_at",
                "product__name",
                "product__category",
            )
            .order_by("-date", "-id")
        )
        for goods in goods_rows:
            goods_by_farmer[goods.issue_to_id].append(goods)

        for farmer_id, farmer_goods in goods_by_farmer.items():
            items, *_ = summarize_priced_farmer_goods_issues(farmer_goods)
            for item in items:
                goods_items.append(
                    {
                        **item,
                        "farmer_id": farmer_id,
                        "farmer_name": farmer_names.get(farmer_id, ""),
                    }
                )

    if point is not None:
        point_display = collection_point_payslip_display_name(point)
        point_goods = _collection_point_direct_goods_data(point, branch_ids, start, end)
        for item in point_goods["goods_items"]:
            goods_items.append(
                {
                    **item,
                    "farmer_id": None,
                    "farmer_name": point_display,
                }
            )

    goods_items.sort(key=lambda row: row["date"], reverse=True)

    return {
        "advances": advances,
        "loan_items": loan_items,
        "goods_items": goods_items,
    }


def _collection_point_goods_by_product(goods_items):
    """Aggregate linked-farmer goods issue lines by product name."""
    by_product = defaultdict(lambda: {"quantity": Decimal("0"), "amount": Decimal("0")})
    for item in goods_items:
        name = (item.get("product_name") or "").strip() or "—"
        by_product[name]["product_name"] = name
        by_product[name]["quantity"] += item.get("quantity") or Decimal("0")
        by_product[name]["amount"] += item.get("amount") or Decimal("0")

    rows = []
    for data in by_product.values():
        qty = data["quantity"].quantize(Decimal("0.01"))
        amount = data["amount"].quantize(Decimal("0.01"))
        unit_price = (amount / qty).quantize(Decimal("0.01")) if qty > 0 else Decimal("0.00")
        rows.append(
            {
                "product_name": data["product_name"],
                "quantity": qty,
                "unit_price": unit_price,
                "amount": amount,
            }
        )
    rows.sort(key=lambda row: row["product_name"].lower())
    total = sum((row["amount"] for row in rows), Decimal("0")).quantize(Decimal("0.01"))
    return rows, total


def build_collection_point_payslip_context(point, start, end, branch_ids):
    from .models import CollectionPointPeriodPayment

    date_cols = date_range_inclusive(start, end)
    branch = point.route.branch if point.route_id else None
    period_payment = CollectionPointPeriodPayment.objects.filter(
        collection_point=point, period_start=start, period_end=end
    ).first()
    milk_qs = MilkCollection.objects.filter(
        source=CollectionSource.POINT,
        collection_point=point,
        date__gte=start,
        date__lte=end,
        branch_id__in=branch_ids,
    ).select_related("branch")
    qty_by_day = defaultdict(Decimal)
    for row in milk_qs:
        qty_by_day[row.date] += _collection_point_quantity(row, row.branch or branch)

    fee_rate = (point.collector_fee or Decimal("0")).quantize(Decimal("0.01"))
    if period_payment and period_payment.collector_fee_rate is not None:
        fee_rate = period_payment.collector_fee_rate
    additional_rate = collection_point_additional_rate(point)
    if period_payment and period_payment.additional_rate is not None:
        additional_rate = period_payment.additional_rate
    unit_label = branch.get_collection_unit_display() if branch else Branch.CollectionUnit.KG.label
    day_rows = []
    for day in date_cols:
        qty = qty_by_day[day].quantize(Decimal("0.01"))
        amount = (qty * fee_rate).quantize(Decimal("0.01")) if qty > 0 else None
        day_rows.append({"date": day, "quantity": qty, "amount": amount})

    total_qty = sum((row["quantity"] for row in day_rows), Decimal("0")).quantize(Decimal("0.01"))
    collector_fee_amount = (total_qty * fee_rate).quantize(Decimal("0.01"))
    if period_payment and period_payment.collector_fee_amount is not None:
        collector_fee_amount = period_payment.collector_fee_amount
    additional_amount, _additional_qty = collection_point_additional_amount_for_period(
        point, branch_ids, start, end
    )
    if period_payment and period_payment.additional_amount is not None:
        additional_amount = period_payment.additional_amount
    if period_payment and period_payment.total_quantity is not None:
        total_qty = period_payment.total_quantity
    farmer_ids = list(Farmer.objects.filter(collection_point=point).values_list("id", flat=True))
    farmer_data = _aggregate_point_farmer_payment_data(
        farmer_ids, branch_ids, start, end, point=point
    )
    farmer_rows, farmer_totals = _collection_point_farmer_sheet_rows(
        farmer_ids, branch_ids, start, end
    )
    detail_lists = _collection_point_linked_farmer_payslip_details(
        farmer_ids, branch_ids, start, end, point=point
    )
    goods_by_product, goods_by_product_total = _collection_point_goods_by_product(
        detail_lists["goods_items"]
    )
    previous_outstanding = collection_point_previous_outstanding_amount(
        point.id, end, pending_only=False, period_start=start, period_end=end
    )
    if period_payment and period_payment.previous_outstanding_amount is not None:
        previous_outstanding = period_payment.previous_outstanding_amount
    payment_correction = collection_point_payment_correction_amount(
        point.id, start, end
    )
    if period_payment and period_payment.payment_correction_amount is not None:
        payment_correction = period_payment.payment_correction_amount
    deduction_total = (
        farmer_data["advance_amount"]
        + farmer_data["goods_deduction"]
        + farmer_data["loan_deduction"]
        + previous_outstanding
    ).quantize(Decimal("0.01"))
    balance_payment = collection_point_period_net_amount(
        gross_amount=farmer_data["gross_amount"],
        advance_amount=farmer_data["advance_amount"],
        goods_deduction=farmer_data["goods_deduction"],
        loan_deduction=farmer_data["loan_deduction"],
        previous_outstanding=previous_outstanding,
        collector_fee_amount=collector_fee_amount,
        additional_amount=additional_amount,
        payment_correction=payment_correction,
    )
    if period_payment and period_payment.net_amount is not None:
        balance_payment = period_payment.net_amount
    if period_payment and period_payment.gross_amount is not None:
        farmer_data = {**farmer_data, "gross_amount": period_payment.gross_amount}
    if period_payment and period_payment.rate is not None:
        farmer_data = {**farmer_data, "rate": period_payment.rate}

    return {
        "point": point,
        "point_display_name": collection_point_payslip_display_name(point),
        "branch": branch,
        "unit_label": unit_label,
        "start": start,
        "end": end,
        "date_cols": date_cols,
        "day_rows": day_rows,
        "collector_fee_rate": fee_rate,
        "total_quantity": total_qty,
        "collector_fee_amount": collector_fee_amount,
        "additional_rate": additional_rate,
        "additional_amount": additional_amount,
        "rate": farmer_data["rate"],
        "gross_amount": farmer_data["gross_amount"],
        "advance_amount": farmer_data["advance_amount"],
        "previous_outstanding_amount": previous_outstanding,
        "payment_correction_amount": payment_correction,
        "goods_deduction": farmer_data["goods_deduction"],
        "goods_feed_total": farmer_data["goods_feed_deduction"],
        "goods_vitamin_total": farmer_data["goods_vitamin_deduction"],
        "goods_omi_total": farmer_data["goods_omi_deduction"],
        "goods_minerals_total": farmer_data["goods_minerals_deduction"],
        "goods_others_total": farmer_data["goods_others_deduction"],
        "loan_total": farmer_data["loan_deduction"],
        "loan_total_calculated": farmer_data["loan_deduction_calculated"],
        "goods_total": farmer_data["goods_deduction"],
        "advances": detail_lists["advances"],
        "loan_items": detail_lists["loan_items"],
        "goods_items": detail_lists["goods_items"],
        "goods_by_product": goods_by_product,
        "goods_by_product_total": goods_by_product_total,
        "has_goods_attachment": farmer_data["goods_deduction"] > Decimal("0"),
        "farmer_rows": farmer_rows,
        "farmer_totals": farmer_totals,
        "deduction_total": deduction_total,
        "balance_payment": balance_payment,
        "net_amount": balance_payment,
        "payment_sheet_period": payment_sheet_period_english(start, end),
        "payment_sheet_period_si": payment_sheet_period_sinhala(start, end),
    }


def collection_point_payment_sheet_export_matrix(
    date_cols, rows, sheet_totals, *, include_settlement=True
):
    def point_label(point):
        return f"{point.number} — {point.name}"

    def num(value):
        return float(value or 0)

    settlement_cols = [
        "Advance",
        "Goods Ded.",
        "Bank Loan",
        "Prev. outstanding",
        "Payment corrections",
        "Collector fee",
    ]
    if sheet_totals.get("show_additional_column"):
        settlement_cols.append("Additional")
    settlement_cols += [
        "Net",
        "Paid",
        "Balance",
    ]
    header = (
        ["Collection point", "Rate"]
        + [d.strftime("%m-%d") for d in date_cols]
        + ["Total qty", "Gross"]
        + (settlement_cols if include_settlement else [])
    )
    matrix = [header]
    for row in rows:
        base_row = (
            [
                point_label(row["point"]),
                num(row["rate"]),
            ]
            + [num(v) for v in row["day_vals"]]
            + [
                num(row["total_quantity"]),
                num(row["gross_amount"]),
            ]
        )
        if include_settlement:
            base_row += [
                num(row["advance_amount"]),
                num(row["goods_deduction"]),
                num(row["loan_deduction"]),
                num(row.get("previous_outstanding_amount")),
                num(row.get("payment_correction_amount")),
                num(row["collector_fee_amount"]),
            ]
            if sheet_totals.get("show_additional_column"):
                base_row.append(num(row.get("additional_amount")))
            base_row += [
                num(row["net_amount"]),
                num(row.get("paid_amount")),
                num(row.get("balance_amount")),
            ]
        matrix.append(base_row)
    total_qty = "" if sheet_totals.get("mixed_units") else num(sheet_totals["total_quantity"])
    day_vals = (
        [""] * len(date_cols)
        if sheet_totals.get("mixed_units")
        else [num(v) for v in sheet_totals["day_vals"]]
    )
    total_row = (
        ["Total", ""]
        + day_vals
        + [
            total_qty,
            num(sheet_totals["gross_amount"]),
        ]
    )
    if include_settlement:
        total_row += [
            num(sheet_totals["advance_amount"]),
            num(sheet_totals["goods_deduction"]),
            num(sheet_totals["loan_deduction"]),
            num(sheet_totals.get("previous_outstanding_amount")),
            num(sheet_totals.get("payment_correction_amount")),
            num(sheet_totals["collector_fee_amount"]),
        ]
        if sheet_totals.get("show_additional_column"):
            total_row.append(num(sheet_totals.get("additional_amount")))
        total_row += [
            num(sheet_totals["net_amount"]),
            num(sheet_totals.get("paid_amount")),
            num(sheet_totals.get("balance_amount")),
        ]
    matrix.append(total_row)
    return matrix


def payment_sheet_export_matrix(date_cols, rows, sheet_totals, *, include_settlement=True):
    def farmer_name(farmer):
        return farmer.common_name or farmer.full_name or ""

    def num(value):
        return float(value or 0)

    settlement_cols = ["Advance", "Goods Ded.", "Bank Loan", "Net", "Paid", "Balance"]
    header = (
        ["Farmer", "Rate"]
        + [d.strftime("%m-%d") for d in date_cols]
        + ["Total L", "Gross"]
        + (settlement_cols if include_settlement else [])
    )
    matrix = [header]
    for row in rows:
        base_row = (
            [farmer_name(row["farmer"]), num(row["rate"])]
            + [num(v) for v in row["day_vals"]]
            + [
                num(row["total_liters"]),
                num(row["gross_amount"]),
            ]
        )
        if include_settlement:
            base_row += [
                num(row["advance_amount"]),
                num(row["goods_deduction"]),
                num(row["loan_deduction"]),
                num(row["net_amount"]),
                num(row.get("paid_amount")),
                num(row.get("balance_amount")),
            ]
        matrix.append(base_row)
    total_row = (
        ["Total", ""]
        + [num(v) for v in sheet_totals["day_vals"]]
        + [
            num(sheet_totals["total_liters"]),
            num(sheet_totals["gross_amount"]),
        ]
    )
    if include_settlement:
        total_row += [
            num(sheet_totals["advance_amount"]),
            num(sheet_totals["goods_deduction"]),
            num(sheet_totals["loan_deduction"]),
            num(sheet_totals["net_amount"]),
            num(sheet_totals.get("paid_amount")),
            num(sheet_totals.get("balance_amount")),
        ]
    matrix.append(total_row)
    return matrix


def loan_deduction_items_for_farmer(farmer, period_start, period_end, pending_only=True):
    items = []
    total = Decimal("0")
    schedule_filter = {
        "loan__farmer": farmer,
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
        if pending_only:
            pending = (
                (schedule.installment_amount or Decimal("0")) - (schedule.paid_amount or Decimal("0"))
            ).quantize(Decimal("0.01"))
            amount = pending
        else:
            amount = (schedule.installment_amount or Decimal("0")).quantize(Decimal("0.01"))
        if amount <= 0:
            continue
        total += amount
        items.append(
            {
                "schedule_id": schedule.id,
                "loan_id": schedule.loan_id,
                "installment_number": schedule.installment_number,
                "due_date": schedule.due_date,
                "amount": amount,
                "is_paid": schedule.is_paid,
                "paid_on": schedule.paid_on,
            }
        )
    return items, total.quantize(Decimal("0.01"))


def get_collection_summary(filters=None):
    filters = dict(filters or {})
    farmer_id = filters.pop("farmer_id", None)
    qs = MilkCollection.objects.filter(**filters)
    if farmer_id:
        qs = qs.filter(source=CollectionSource.FARMER, farmer_id=farmer_id)
    else:
        qs = qs.route_collections()
    return (
        qs.annotate(
            place_name=Case(
                When(source=CollectionSource.POINT, then=F("collection_point__name")),
                When(source=CollectionSource.FARMER, then=F("farmer__full_name")),
                default=Value(""),
                output_field=CharField(),
            )
        )
        .values("date", "route__name", "place_name", "branch__name")
        .annotate(total_kg=Sum("kg"), total_liters=Sum("liters"))
        .order_by("-date", "branch__name", "route__name", "place_name")
    )


def get_buyer_summary(filters=None):
    filters = filters or {}
    return (
        MilkDistribution.objects.filter(**filters)
        .values("buyer__name", "branch__name")
        .annotate(total_kg=Sum("kg"))
        .order_by("branch__name", "buyer__name")
    )
