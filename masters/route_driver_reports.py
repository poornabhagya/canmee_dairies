from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Sum
from django.utils import timezone

from milk_collections.models import CollectionSource, MilkCollection
from reports.point_goods_summary import build_route_point_goods_summary_report

from .models import CollectionPoint, Route


def build_route_summary_context(route, from_date, to_date, point_id=None):
    date_list = []
    current = from_date
    while current <= to_date:
        date_list.append(current)
        current += timedelta(days=1)

    rows_qs = (
        MilkCollection.objects.filter(
            route=route,
            source=CollectionSource.POINT,
            date__gte=from_date,
            date__lte=to_date,
        )
        .select_related("collection_point")
        .order_by("collection_point__number", "collection_point__name", "date", "pk")
    )
    if point_id:
        rows_qs = rows_qs.filter(collection_point_id=point_id)
    rows = rows_qs

    point_map = {}
    for row in rows:
        pid = row.collection_point_id
        if pid not in point_map:
            cp = row.collection_point
            point_map[pid] = {
                "point_number": (str(cp.number).strip() if cp else "—"),
                "point_name": ((cp.name or "").strip() if cp else "—"),
                "day_map": {},
                "row_total": Decimal("0.00"),
            }
        curr = point_map[pid]["day_map"].get(row.date, Decimal("0.00"))
        point_map[pid]["day_map"][row.date] = curr + row.kg
        point_map[pid]["row_total"] += row.kg

    point_rows = []
    for item in point_map.values():
        values = [item["day_map"].get(day, Decimal("0.00")) for day in date_list]
        point_rows.append(
            {
                "point_number": item["point_number"],
                "point_name": item["point_name"],
                "values": values,
                "row_total": item["row_total"],
            }
        )

    day_totals = []
    grand_total = Decimal("0.00")
    for day in date_list:
        day_total = Decimal("0.00")
        for item in point_map.values():
            day_total += item["day_map"].get(day, Decimal("0.00"))
        day_totals.append(day_total)
        grand_total += day_total

    return {
        "route": route,
        "from_date": from_date,
        "to_date": to_date,
        "date_list": date_list,
        "point_rows": point_rows,
        "day_totals": day_totals,
        "grand_total": grand_total,
        "printed_at": timezone.now(),
    }


def build_point_summary_context(route, from_date, to_date, point_id=None):
    rows_qs = (
        MilkCollection.objects.filter(
            route=route,
            source=CollectionSource.POINT,
            date__gte=from_date,
            date__lte=to_date,
        )
        .select_related("collection_point")
        .order_by("collection_point__number", "collection_point__name", "date", "pk")
    )
    if point_id:
        rows_qs = rows_qs.filter(collection_point_id=point_id)
    rows = rows_qs

    grouped_points = []
    current_point_id = None
    current_group = None
    for row in rows:
        if row.collection_point_id != current_point_id:
            if current_group:
                grouped_points.append(current_group)
            current_point_id = row.collection_point_id
            current_group = {
                "point_id": row.collection_point_id,
                "point_number": str(row.collection_point.number).strip() if row.collection_point else "—",
                "point_name": (row.collection_point.name or "").strip() if row.collection_point else "—",
                "rows": [],
                "total_kg": Decimal("0.00"),
                "total_liters": Decimal("0.00"),
            }
        current_group["rows"].append(
            {
                "date": row.date,
                "kg": row.kg,
                "liters": row.liters,
            }
        )
        current_group["total_kg"] += row.kg
        current_group["total_liters"] += row.liters
    if current_group:
        grouped_points.append(current_group)

    overall_total = rows.aggregate(
        total_kg=Sum("kg"),
        total_liters=Sum("liters"),
        total_entries=Count("id"),
    )

    return {
        "route": route,
        "from_date": from_date,
        "to_date": to_date,
        "grouped_points": grouped_points,
        "total_entries": overall_total["total_entries"] or 0,
        "total_kg": overall_total["total_kg"] or Decimal("0.00"),
        "total_liters": overall_total["total_liters"] or Decimal("0.00"),
        "printed_at": timezone.now(),
    }


def build_farmer_goods_context(route: Route, from_date, to_date, point_id=None):
    branch_sections, grand_totals = build_route_point_goods_summary_report(
        route,
        from_date,
        to_date,
        point_id=point_id,
    )
    selected_point = None
    if point_id:
        selected_point = (
            CollectionPoint.objects.filter(pk=point_id, route=route).only("id", "number", "name").first()
        )
    return {
        "route": route,
        "branch_sections": branch_sections,
        "grand_totals": grand_totals,
        "start": from_date,
        "end": to_date,
        "selected_point": selected_point,
        "generated_at": timezone.localtime(),
    }
