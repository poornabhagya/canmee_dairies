from datetime import date
from decimal import Decimal

from django.db.models import Count, Max, Q, Sum, Value, DecimalField
from django.db.models.functions import Coalesce

from milk_collections.models import CollectionSource, MilkCollection
from reports.services import build_farmer_payment_summaries

FARMER_ACCOUNT_LOOKBACK_START = date(2000, 1, 1)
INACTIVE_ALERT_DAYS = 30


def annotate_farmer_list_queryset(qs, today=None):
    today = today or date.today()
    return qs.annotate(
        today_milk_kg=Coalesce(
            Sum(
                "milkcollection__kg",
                filter=Q(
                    milkcollection__date=today,
                    milkcollection__source=CollectionSource.FARMER,
                    milkcollection__is_deleted=False,
                ),
            ),
            Value(Decimal("0.00"), output_field=DecimalField(max_digits=10, decimal_places=2)),
        ),
        last_collection_date=Max(
            "milkcollection__date",
            filter=Q(
                milkcollection__source=CollectionSource.FARMER,
                milkcollection__is_deleted=False,
            ),
        ),
    )


def summarize_farmer_list_rows(farmers, today=None):
    today = today or date.today()
    active_today = 0
    inactive_today = 0
    resigned = 0
    inactive_30_days = 0
    max_inactive_days = 0
    for farmer in farmers:
        is_resigned = farmer.status == "resigned"
        farmer.is_resigned = is_resigned
        if is_resigned:
            resigned += 1
            farmer.is_active_farmer = False
            farmer.is_inactive_farmer = False
            farmer.is_inactive_30_days = False
            farmer.days_since_collection = None
            continue
        milk = farmer.today_milk_kg or Decimal("0")
        is_active = milk > Decimal("0")
        farmer.is_active_farmer = is_active
        farmer.is_inactive_farmer = not is_active
        if is_active:
            active_today += 1
        else:
            inactive_today += 1
        last = farmer.last_collection_date
        if last:
            days_since = (today - last).days
        elif farmer.created_at:
            days_since = (today - farmer.created_at.date()).days
        else:
            days_since = 0
        farmer.days_since_collection = days_since
        farmer.is_inactive_30_days = days_since >= INACTIVE_ALERT_DAYS
        if farmer.is_inactive_30_days:
            inactive_30_days += 1
            max_inactive_days = max(max_inactive_days, days_since)
    return {
        "active_today": active_today,
        "inactive_today": inactive_today,
        "resigned": resigned,
        "inactive_30_days": inactive_30_days,
        "max_inactive_days": max_inactive_days,
    }


def build_farmer_account_summaries(farmer_ids, branch_ids, today=None):
    if not farmer_ids or not branch_ids:
        return {}
    today = today or date.today()
    pending = build_farmer_payment_summaries(
        list(farmer_ids),
        list(branch_ids),
        FARMER_ACCOUNT_LOOKBACK_START,
        today,
        pending_only=True,
    )
    unpaid_days = {
        row["farmer_id"]: row["day_count"] or 0
        for row in MilkCollection.objects.filter(
            farmer_id__in=farmer_ids,
            source=CollectionSource.FARMER,
            is_paid=False,
            is_deleted=False,
            branch_id__in=branch_ids,
        )
        .values("farmer_id")
        .annotate(day_count=Count("date", distinct=True))
    }
    summaries = {}
    for farmer_id in farmer_ids:
        data = pending.get(farmer_id) or {}
        gross = data.get("gross_amount") or Decimal("0.00")
        advance = data.get("advance_amount") or Decimal("0.00")
        goods = data.get("goods_deduction") or Decimal("0.00")
        loan = data.get("loan_deduction") or Decimal("0.00")
        net = data.get("net_amount") or Decimal("0.00")
        summaries[farmer_id] = {
            "unpaid_days": unpaid_days.get(farmer_id, 0),
            "gross": gross,
            "advance": advance,
            "goods": goods,
            "loan": loan,
            "net": net,
        }
    return summaries
