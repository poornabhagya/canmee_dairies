"""Payment-sheet quantities from point/farmer reconcile choices."""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, Sum

from masters.models import CollectionPoint

from .models import CollectionPointMilkReconcileChoice, CollectionSource, MilkCollection


def _quantize(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def reconcile_choice_map(collection_point_ids, start, end):
    """{(collection_point_id, date): source} for saved choices."""
    result = {}
    if not collection_point_ids:
        return result
    for row in CollectionPointMilkReconcileChoice.objects.filter(
        collection_point_id__in=collection_point_ids,
        date__gte=start,
        date__lte=end,
    ).only("collection_point_id", "date", "source"):
        result[(row.collection_point_id, row.date)] = row.source
    return result


def set_reconcile_choice(*, collection_point, route, date, source, updated_by=None):
    if source not in {
        CollectionPointMilkReconcileChoice.Source.POINT,
        CollectionPointMilkReconcileChoice.Source.FARMER,
    }:
        raise ValueError("Invalid reconcile source.")
    CollectionPointMilkReconcileChoice.objects.update_or_create(
        collection_point=collection_point,
        route=route,
        date=date,
        defaults={"source": source, "updated_by": updated_by},
    )


def _points_per_route(route_ids):
    counts = {
        row["route_id"]: row["point_count"]
        for row in CollectionPoint.objects.filter(route_id__in=route_ids)
        .values("route_id")
        .annotate(point_count=Count("id"))
    }
    return counts


def _point_kg_map(branch_ids, start, end, point_ids):
    result = {}
    for row in (
        MilkCollection.objects.filter(
            source=CollectionSource.POINT,
            date__gte=start,
            date__lte=end,
            branch_id__in=branch_ids,
            collection_point_id__in=point_ids,
        )
        .values("collection_point_id", "date")
        .annotate(total_kg=Sum("kg"), total_liters=Sum("liters"))
    ):
        result[(row["collection_point_id"], row["date"])] = (
            _quantize(row["total_kg"]),
            _quantize(row["total_liters"]),
        )
    return result


def _farmer_kg_maps(branch_ids, start, end, point_ids):
    """Farmer kg/l indexed for reconcile: route-level and point-assigned."""
    base = MilkCollection.objects.filter(
        source=CollectionSource.FARMER,
        date__gte=start,
        date__lte=end,
        branch_id__in=branch_ids,
    )
    farmer_by_route_day = {}
    for row in base.values("date", "route_id").annotate(
        total_kg=Sum("kg"), total_liters=Sum("liters")
    ):
        farmer_by_route_day[(row["route_id"], row["date"])] = (
            _quantize(row["total_kg"]),
            _quantize(row["total_liters"]),
        )

    farmer_by_point_day = {}
    for row in (
        base.filter(farmer__collection_point_id__in=point_ids)
        .values("date", "farmer__collection_point_id")
        .annotate(total_kg=Sum("kg"), total_liters=Sum("liters"))
    ):
        farmer_by_point_day[(row["farmer__collection_point_id"], row["date"])] = (
            _quantize(row["total_kg"]),
            _quantize(row["total_liters"]),
        )
    return farmer_by_route_day, farmer_by_point_day


def _farmer_kg_for_point(point, date, farmer_by_route_day, farmer_by_point_day, points_per_route):
    if points_per_route.get(point.route_id, 0) <= 1:
        return farmer_by_route_day.get((point.route_id, date), (Decimal("0.00"), Decimal("0.00")))
    return farmer_by_point_day.get((point.id, date), (Decimal("0.00"), Decimal("0.00")))


def effective_kg_for_point_day(*, point_kg, farmer_kg, choice):
    point_kg = _quantize(point_kg)
    farmer_kg = _quantize(farmer_kg)
    if choice == CollectionPointMilkReconcileChoice.Source.FARMER:
        return farmer_kg
    return point_kg


def effective_liters_for_point_day(*, point_liters, farmer_liters, choice):
    point_liters = _quantize(point_liters)
    farmer_liters = _quantize(farmer_liters)
    if choice == CollectionPointMilkReconcileChoice.Source.FARMER:
        return farmer_liters
    return point_liters


def _qty_in_branch_unit(branch, kg, liters):
    from branches.models import Branch

    if branch and branch.collection_unit == Branch.CollectionUnit.LITERS:
        return liters
    return kg


def build_effective_payment_qty_maps(points, branch_ids, start, end, dates):
    """
    Payment-sheet qty maps using reconcile choices.

    Returns effective_qty, paid_qty (actual point paid), unpaid_effective per (point_id, date).
    """
    point_ids = [p.id for p in points]
    route_ids = {p.route_id for p in points if p.route_id}
    points_per_route = _points_per_route(route_ids)
    point_kg_map = _point_kg_map(branch_ids, start, end, point_ids)
    farmer_by_route_day, farmer_by_point_day = _farmer_kg_maps(branch_ids, start, end, point_ids)
    choices = reconcile_choice_map(point_ids, start, end)

    paid_point_map = defaultdict(Decimal)
    for row in (
        MilkCollection.objects.filter(
            source=CollectionSource.POINT,
            date__gte=start,
            date__lte=end,
            branch_id__in=branch_ids,
            collection_point_id__in=point_ids,
            is_paid=True,
        )
        .select_related("branch")
        .only("collection_point_id", "date", "kg", "liters", "branch__collection_unit")
    ):
        branch = row.branch
        from branches.models import Branch

        paid_qty = row.liters if branch and branch.collection_unit == Branch.CollectionUnit.LITERS else row.kg
        paid_point_map[(row.collection_point_id, row.date)] += _quantize(paid_qty)

    effective_qty = {}
    unpaid_effective = {}
    reconcile_meta = {}

    for point in points:
        branch = point.route.branch if point.route_id else None
        for day in dates:
            key = (point.id, day)
            point_kg, point_liters = point_kg_map.get(key, (Decimal("0.00"), Decimal("0.00")))
            farmer_kg, farmer_liters = _farmer_kg_for_point(
                point, day, farmer_by_route_day, farmer_by_point_day, points_per_route
            )
            choice = choices.get(key, CollectionPointMilkReconcileChoice.Source.POINT)
            eff_kg = effective_kg_for_point_day(
                point_kg=point_kg, farmer_kg=farmer_kg, choice=choice
            )
            eff_liters = effective_liters_for_point_day(
                point_liters=point_liters, farmer_liters=farmer_liters, choice=choice
            )
            effective = _qty_in_branch_unit(branch, eff_kg, eff_liters)
            paid = paid_point_map.get(key, Decimal("0.00"))
            unpaid = max(Decimal("0.00"), (effective - paid).quantize(Decimal("0.01")))
            effective_qty[key] = effective
            unpaid_effective[key] = unpaid
            reconcile_meta[key] = {
                "point_kg": point_kg,
                "farmer_kg": farmer_kg,
                "choice": choice,
                "reconcile_kg": eff_kg,
            }

    return effective_qty, paid_point_map, unpaid_effective, reconcile_meta


def _active_reconcile_dates_for_point(
    point,
    point_kg_map,
    choices,
    farmer_by_route_day,
    farmer_by_point_day,
    points_per_route,
):
    dates = set()
    for point_id, day in point_kg_map:
        if point_id == point.id:
            dates.add(day)
    for point_id, day in choices:
        if point_id == point.id:
            dates.add(day)
    if points_per_route.get(point.route_id, 0) <= 1:
        for route_id, day in farmer_by_route_day:
            if route_id == point.route_id:
                dates.add(day)
    else:
        for point_id, day in farmer_by_point_day:
            if point_id == point.id:
                dates.add(day)
    return dates


def build_effective_payment_qty_totals(points, branch_ids, start, end):
    """
    Total and unpaid effective qty per point without scanning every calendar day.
    """
    point_ids = [p.id for p in points]
    route_ids = {p.route_id for p in points if p.route_id}
    points_per_route = _points_per_route(route_ids)
    point_kg_map = _point_kg_map(branch_ids, start, end, point_ids)
    farmer_by_route_day, farmer_by_point_day = _farmer_kg_maps(branch_ids, start, end, point_ids)
    choices = reconcile_choice_map(point_ids, start, end)

    paid_point_map = defaultdict(Decimal)
    for row in (
        MilkCollection.objects.filter(
            source=CollectionSource.POINT,
            date__gte=start,
            date__lte=end,
            branch_id__in=branch_ids,
            collection_point_id__in=point_ids,
            is_paid=True,
        )
        .select_related("branch")
        .only("collection_point_id", "date", "kg", "liters", "branch__collection_unit")
    ):
        branch = row.branch
        from branches.models import Branch

        paid_qty = row.liters if branch and branch.collection_unit == Branch.CollectionUnit.LITERS else row.kg
        paid_point_map[(row.collection_point_id, row.date)] += _quantize(paid_qty)

    totals = {}
    for point in points:
        branch = point.route.branch if point.route_id else None
        total = Decimal("0.00")
        unpaid = Decimal("0.00")
        active_dates = _active_reconcile_dates_for_point(
            point,
            point_kg_map,
            choices,
            farmer_by_route_day,
            farmer_by_point_day,
            points_per_route,
        )
        for day in active_dates:
            key = (point.id, day)
            point_kg, point_liters = point_kg_map.get(key, (Decimal("0.00"), Decimal("0.00")))
            farmer_kg, farmer_liters = _farmer_kg_for_point(
                point, day, farmer_by_route_day, farmer_by_point_day, points_per_route
            )
            choice = choices.get(key, CollectionPointMilkReconcileChoice.Source.POINT)
            eff_kg = effective_kg_for_point_day(
                point_kg=point_kg, farmer_kg=farmer_kg, choice=choice
            )
            eff_liters = effective_liters_for_point_day(
                point_liters=point_liters, farmer_liters=farmer_liters, choice=choice
            )
            effective = _qty_in_branch_unit(branch, eff_kg, eff_liters)
            paid = paid_point_map.get(key, Decimal("0.00"))
            total += effective
            unpaid += max(Decimal("0.00"), (effective - paid).quantize(Decimal("0.01")))
        totals[point.id] = {
            "total": total.quantize(Decimal("0.01")),
            "unpaid": unpaid.quantize(Decimal("0.01")),
        }
    return totals


def _date_range_inclusive(start, end):
    from datetime import timedelta

    dates = []
    day = start
    while day <= end:
        dates.append(day)
        day += timedelta(days=1)
    return dates


def collection_point_period_effective_totals(point, branch_ids, start, end):
    """Total and unpaid effective qty (branch unit) for payment calculations."""
    totals = build_effective_payment_qty_totals([point], branch_ids, start, end).get(
        point.id, {"total": Decimal("0.00"), "unpaid": Decimal("0.00")}
    )
    return totals["total"], totals["unpaid"]
