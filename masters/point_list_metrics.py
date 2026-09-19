from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, Max, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from milk_collections.models import CollectionSource, MilkCollection
from suppliers.models import FarmerGoodsIssue

POINT_ACCOUNT_LOOKBACK_START = date(2000, 1, 1)
INACTIVE_ALERT_DAYS = 30

_EMPTY_FARMER_TOTALS = {
    "gross_amount": Decimal("0.00"),
    "advance_amount": Decimal("0.00"),
    "goods_deduction": Decimal("0.00"),
    "loan_deduction": Decimal("0.00"),
}


def annotate_point_list_queryset(qs):
    last_date_subq = (
        MilkCollection.objects.filter(
            collection_point_id=OuterRef("pk"),
            source=CollectionSource.POINT,
            is_deleted=False,
        )
        .values("collection_point_id")
        .annotate(last=Max("date"))
        .values("last")[:1]
    )
    return qs.annotate(last_collection_date=Subquery(last_date_subq))


def annotate_point_today_milk_kg(qs, today):
    today_milk_subq = (
        MilkCollection.objects.filter(
            collection_point_id=OuterRef("pk"),
            date=today,
            source=CollectionSource.POINT,
            is_deleted=False,
        )
        .values("collection_point_id")
        .annotate(
            total=Coalesce(
                Sum("kg"),
                Value(Decimal("0.00"), output_field=DecimalField(max_digits=10, decimal_places=2)),
            )
        )
        .values("total")[:1]
    )
    return qs.annotate(
        today_milk_kg=Coalesce(
            Subquery(today_milk_subq),
            Value(Decimal("0.00"), output_field=DecimalField(max_digits=10, decimal_places=2)),
        )
    )


def attach_point_list_milk_metrics(points, today):
    """Attach today kg and last collection date with two grouped queries instead of per-row subqueries."""
    if not points:
        return
    ids = [point.pk for point in points]
    today_map = {
        row["collection_point_id"]: row["total"] or Decimal("0.00")
        for row in (
            MilkCollection.objects.filter(
                collection_point_id__in=ids,
                date=today,
                source=CollectionSource.POINT,
                is_deleted=False,
            )
            .values("collection_point_id")
            .annotate(total=Sum("kg"))
        )
    }
    last_map = {
        row["collection_point_id"]: row["last"]
        for row in (
            MilkCollection.objects.filter(
                collection_point_id__in=ids,
                source=CollectionSource.POINT,
                is_deleted=False,
            )
            .values("collection_point_id")
            .annotate(last=Max("date"))
        )
    }
    zero = Decimal("0.00")
    for point in points:
        point.today_milk_kg = today_map.get(point.pk) or zero
        point.last_collection_date = last_map.get(point.pk)


def bulk_point_avg_daily_milk_kg(point_ids, today=None, lookback_days=30):
    if not point_ids:
        return {}
    today = today or date.today()
    start = today - timedelta(days=max(1, lookback_days) - 1)
    rows = (
        MilkCollection.objects.filter(
            source=CollectionSource.POINT,
            collection_point_id__in=point_ids,
            is_deleted=False,
            date__gte=start,
            date__lte=today,
        )
        .values("collection_point_id")
        .annotate(
            total_kg=Coalesce(Sum("kg"), Value(0), output_field=DecimalField()),
            day_count=Count("date", distinct=True),
        )
    )
    result = {}
    for row in rows:
        days = row["day_count"] or 0
        if days:
            result[row["collection_point_id"]] = (row["total_kg"] / days).quantize(Decimal("0.01"))
    return result


def summarize_point_list_rows(points, today=None):
    today = today or date.today()
    active_today = 0
    inactive_today = 0
    inactive_30_days = 0
    missing_location = 0
    max_inactive_days = 0
    for point in points:
        if not (point.location or "").strip():
            missing_location += 1
        point.has_no_location = not (point.location or "").strip()
        milk = point.today_milk_kg or Decimal("0")
        is_active = milk > Decimal("0")
        point.is_active_point = is_active
        point.is_inactive_point = not is_active
        point.is_active_today = is_active
        if is_active:
            active_today += 1
        else:
            inactive_today += 1
        last = point.last_collection_date
        if last:
            days_since = (today - last).days
        elif point.created_at:
            days_since = (today - point.created_at.date()).days
        else:
            days_since = 0
        point.days_since_collection = days_since
        point.is_inactive_30_days = days_since >= INACTIVE_ALERT_DAYS
        if point.is_inactive_30_days:
            inactive_30_days += 1
            max_inactive_days = max(max_inactive_days, days_since)
    return {
        "active_today": active_today,
        "inactive_today": inactive_today,
        "inactive_30_days": inactive_30_days,
        "missing_location": missing_location,
        "max_inactive_days": max_inactive_days,
    }


def _collection_point_issue_id_candidates(point):
    id_candidates = {point.pk}
    try:
        id_candidates.add(int(str(point.number).strip()))
    except (ValueError, TypeError):
        pass
    return id_candidates


def _build_issue_to_point_map(points):
    mapping = {}
    for point in points:
        for candidate in _collection_point_issue_id_candidates(point):
            mapping[candidate] = point.id
    return mapping


def _bulk_collection_point_goods_totals(points, start, end, pending_only=False, skip_map=None):
    pending_map, period_map = _bulk_collection_point_goods_maps(
        points, start, end, skip_map=skip_map
    )
    return pending_map if pending_only else period_map


def _bulk_collection_point_goods_maps(points, start, end, skip_map=None):
    from reports.deduction_skips import goods_issue_is_skipped
    from reports.services import summarize_priced_farmer_goods_issues

    empty = {point.id: Decimal("0.00") for point in points}
    if not points:
        return empty.copy(), empty.copy()

    issue_to_point = _build_issue_to_point_map(points)
    if not issue_to_point:
        return empty.copy(), empty.copy()

    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)
    goods_rows = (
        FarmerGoodsIssue.objects.filter(
            issue_to_type=FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
            issue_to_id__in=list(issue_to_point.keys()),
            date__gte=start_dt,
            date__lt=end_dt,
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

    pending_goods_by_point = defaultdict(list)
    period_goods_by_point = defaultdict(list)
    for goods in goods_rows:
        point_id = issue_to_point.get(goods.issue_to_id)
        if point_id is None:
            continue
        if skip_map is not None:
            skip_bucket = skip_map.get(point_id, {})
            if goods_issue_is_skipped(goods, skip_bucket):
                continue
        period_goods_by_point[point_id].append(goods)
        if not goods.is_settled:
            pending_goods_by_point[point_id].append(goods)

    pending_totals = {}
    period_totals = {}
    for point in points:
        (
            _goods_items,
            pending_total,
            _feed_total,
            _vitamin_total,
            _omi_total,
            _minerals_total,
            _others_total,
        ) = summarize_priced_farmer_goods_issues(pending_goods_by_point.get(point.id, []))
        (
            _goods_items,
            period_total,
            _feed_total,
            _vitamin_total,
            _omi_total,
            _minerals_total,
            _others_total,
        ) = summarize_priced_farmer_goods_issues(period_goods_by_point.get(point.id, []))
        pending_totals[point.id] = pending_total.quantize(Decimal("0.01"))
        period_totals[point.id] = period_total.quantize(Decimal("0.01"))
    return pending_totals, period_totals


def _sum_farmer_payment_rows(summary_rows):
    totals = {key: Decimal("0.00") for key in _EMPTY_FARMER_TOTALS}
    for summary in summary_rows:
        if not summary:
            continue
        for key in totals:
            totals[key] += summary.get(key) or Decimal("0.00")
    return {key: value.quantize(Decimal("0.01")) for key, value in totals.items()}


def _aggregate_point_farmer_totals(
    farmer_ids,
    farmer_summaries,
    farmer_payments,
    point_goods_total,
):
    rows = []
    for farmer_id in farmer_ids:
        summary = farmer_summaries.get(farmer_id)
        if not summary:
            continue
        payment = farmer_payments.get(farmer_id)
        if payment and payment.rate is not None:
            from reports.services import summary_with_payment_snapshot

            summary = summary_with_payment_snapshot(summary, payment)
        rows.append(summary)
    totals = _sum_farmer_payment_rows(rows)
    totals["goods_deduction"] = (
        totals["goods_deduction"] + (point_goods_total or Decimal("0.00"))
    ).quantize(Decimal("0.01"))
    return totals


def build_point_account_summaries(points, branch_ids, today=None):
    if not points or not branch_ids:
        return {}

    from milk_collections.reconcile_payment import build_effective_payment_qty_totals
    from reports.deduction_skips import collection_point_payment_period_deduction_skip_map
    from reports.payment_recording import farmer_period_payment_map
    from reports.services import (
        _farmers_by_collection_point,
        build_farmer_payment_summaries,
        bulk_collection_point_additional_amounts,
        collection_point_paid_collector_fee_map,
    )

    today = today or date.today()
    start = POINT_ACCOUNT_LOOKBACK_START
    point_ids = [point.id for point in points]
    points_by_id = {point.id: point for point in points}
    farmers_map = _farmers_by_collection_point(point_ids)
    all_farmer_ids = list({farmer_id for farmer_ids in farmers_map.values() for farmer_id in farmer_ids})

    pending_farmer_summaries = build_farmer_payment_summaries(
        all_farmer_ids, branch_ids, start, today, pending_only=True
    )
    period_farmer_summaries = build_farmer_payment_summaries(
        all_farmer_ids, branch_ids, start, today, pending_only=False
    )
    farmer_payments = farmer_period_payment_map(all_farmer_ids, start, today)

    qty_totals = build_effective_payment_qty_totals(points, branch_ids, start, today)
    paid_collector_map = collection_point_paid_collector_fee_map(
        point_ids, branch_ids, start, today, points_by_id=points_by_id
    )
    cp_skip_map = collection_point_payment_period_deduction_skip_map(point_ids, start, today)
    pending_point_goods, period_point_goods = _bulk_collection_point_goods_maps(
        points, start, today, skip_map=cp_skip_map
    )
    additional_map = bulk_collection_point_additional_amounts(points, branch_ids, start, today)

    summaries = {}
    for point in points:
        farmer_ids = farmers_map.get(point.id, [])
        pending_farmer = _aggregate_point_farmer_totals(
            farmer_ids,
            pending_farmer_summaries,
            farmer_payments,
            pending_point_goods.get(point.id, Decimal("0.00")),
        )
        period_farmer = _aggregate_point_farmer_totals(
            farmer_ids,
            period_farmer_summaries,
            farmer_payments,
            period_point_goods.get(point.id, Decimal("0.00")),
        )

        fee_rate = (point.collector_fee or Decimal("0")).quantize(Decimal("0.01"))
        point_qty = qty_totals.get(point.id, {"total": Decimal("0.00"), "unpaid": Decimal("0.00")})
        unpaid_qty = point_qty["unpaid"]
        collector_fee_unpaid = (unpaid_qty * fee_rate).quantize(Decimal("0.01"))

        total_qty = point_qty["total"]
        collector_fee_total = (total_qty * fee_rate).quantize(Decimal("0.01"))
        additional_total = additional_map.get(point.id, Decimal("0.00"))
        period_net = (
            period_farmer["gross_amount"]
            - period_farmer["advance_amount"]
            - period_farmer["goods_deduction"]
            - period_farmer["loan_deduction"]
            + collector_fee_total
            + additional_total
        ).quantize(Decimal("0.01"))
        paid_amount = paid_collector_map.get(point.id, Decimal("0"))
        net = max(Decimal("0"), (period_net - paid_amount).quantize(Decimal("0.01")))

        summaries[point.id] = {
            "collector_fee": collector_fee_unpaid,
            "gross": pending_farmer.get("gross_amount") or Decimal("0.00"),
            "advance": pending_farmer.get("advance_amount") or Decimal("0.00"),
            "goods": pending_farmer.get("goods_deduction") or Decimal("0.00"),
            "loan": pending_farmer.get("loan_deduction") or Decimal("0.00"),
            "net": net,
        }
    return summaries
