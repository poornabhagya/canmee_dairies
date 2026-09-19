"""Compare collection point milk rows with summed farmer milk at each point."""

from __future__ import annotations

from decimal import Decimal

from django.db.models import Sum

from masters.models import CollectionPoint

from .models import CollectionPointMilkReconcileChoice, CollectionSource
from .reconcile_payment import effective_kg_for_point_day, reconcile_choice_map
from .farmer_point_rollup import sum_farmer_milk_at_point


def _quantize(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _reconcile_status(point_kg: Decimal, farmer_kg: Decimal) -> str:
    if point_kg == farmer_kg:
        return "matched"
    if point_kg == 0 and farmer_kg > 0:
        return "missing_point"
    if farmer_kg == 0 and point_kg > 0:
        return "missing_farmers"
    return "mismatch"


def build_point_farmer_reconciliation_rows(
    base_qs, *, farmer_rollups_qs=None, status_filter=""
):
    """
    One row per date + route + collection point comparing POINT totals vs farmer rollup.

    base_qs controls which rows appear (includes text search). Farmer kg/l totals always
    use farmer_rollups_qs so search by point name/number does not zero out farmer milk.

    status_filter: '' (all), 'matched', 'mismatch', 'missing_point', 'missing_farmers', 'issues' (any non-matched)
    """
    farmer_rollups_qs = farmer_rollups_qs or base_qs
    point_rows = (
        base_qs.filter(source=CollectionSource.POINT, collection_point_id__isnull=False)
        .values(
            "date",
            "route_id",
            "collection_point_id",
            "collection_point__number",
            "collection_point__name",
            "route__code",
            "route__name",
            "branch_id",
            "branch__name",
        )
        .annotate(point_kg=Sum("kg"), point_liters=Sum("liters"))
    )

    # Assigned farmers only — used to discover keys when there is no POINT row yet.
    assigned_farmer_rows = (
        base_qs.filter(
            source=CollectionSource.FARMER,
            farmer__collection_point_id__isnull=False,
        )
        .values("date", "route_id", "farmer__collection_point_id")
        .annotate(farmer_kg=Sum("kg"), farmer_liters=Sum("liters"))
    )

    point_map = {}
    meta = {}
    for row in point_rows:
        key = (row["date"], row["route_id"], row["collection_point_id"])
        point_map[key] = row
        meta[key] = {
            "point_number": row["collection_point__number"],
            "point_name": row["collection_point__name"],
            "route_code": row["route__code"],
            "route_name": row["route__name"],
            "branch_id": row["branch_id"],
            "branch_name": row["branch__name"],
        }

    assigned_farmer_keys = set()
    for row in assigned_farmer_rows:
        assigned_farmer_keys.add(
            (row["date"], row["route_id"], row["farmer__collection_point_id"])
        )

    all_keys = set(point_map.keys()) | assigned_farmer_keys
    collection_point_ids = {key[2] for key in all_keys}
    dates = {key[0] for key in all_keys}
    choice_map = reconcile_choice_map(
        collection_point_ids,
        min(dates) if dates else None,
        max(dates) if dates else None,
    ) if dates else {}
    missing_cp_ids = {key[2] for key in all_keys if key not in meta}
    if missing_cp_ids:
        for cp in CollectionPoint.objects.filter(pk__in=missing_cp_ids).select_related(
            "route", "route__branch"
        ):
            for key in all_keys:
                if key[2] != cp.pk:
                    continue
                meta[key] = {
                    "point_number": cp.number,
                    "point_name": cp.name,
                    "route_code": cp.route.code if cp.route_id else "",
                    "route_name": cp.route.name if cp.route_id else "",
                    "branch_id": cp.route.branch_id if cp.route_id else None,
                    "branch_name": cp.route.branch.name if cp.route_id and cp.route.branch_id else "",
                }

    rows = []
    farmer_totals_cache = {}
    for key in all_keys:
        date, route_id, collection_point_id = key
        point_row = point_map.get(key, {})
        cache_key = (date, route_id, collection_point_id)
        if cache_key not in farmer_totals_cache:
            farmer_totals_cache[cache_key] = sum_farmer_milk_at_point(
                date, route_id, collection_point_id, base_qs=farmer_rollups_qs
            )
        farmer_kg, farmer_liters = farmer_totals_cache[cache_key]
        point_kg = _quantize(point_row.get("point_kg"))
        point_liters = _quantize(point_row.get("point_liters"))
        farmer_kg = _quantize(farmer_kg)
        farmer_liters = _quantize(farmer_liters)
        choice = choice_map.get(
            (collection_point_id, date), CollectionPointMilkReconcileChoice.Source.POINT
        )
        reconcile_kg = effective_kg_for_point_day(
            point_kg=point_kg, farmer_kg=farmer_kg, choice=choice
        )
        diff_kg = (point_kg - farmer_kg).quantize(Decimal("0.01"))
        diff_liters = (point_liters - farmer_liters).quantize(Decimal("0.01"))
        status = _reconcile_status(point_kg, farmer_kg)
        info = meta.get(key, {})
        rows.append(
            {
                "date": date,
                "route_id": route_id,
                "collection_point_id": collection_point_id,
                "branch_id": info.get("branch_id"),
                "branch_name": info.get("branch_name") or "—",
                "route_label": info.get("route_name") or "—",
                "point_label": (
                    f"{info.get('point_number') or collection_point_id} — {info.get('point_name') or ''}".strip(
                        " —"
                    )
                    or str(collection_point_id)
                ),
                "point_kg": point_kg,
                "point_liters": point_liters,
                "farmer_kg": farmer_kg,
                "farmer_liters": farmer_liters,
                "diff_kg": diff_kg,
                "diff_liters": diff_liters,
                "reconcile_source": choice,
                "reconcile_kg": reconcile_kg,
                "status": status,
            }
        )

    rows.sort(key=lambda r: (r["date"], r["route_label"], r["point_label"]), reverse=True)

    if status_filter == "issues":
        rows = [r for r in rows if r["status"] != "matched"]
    elif status_filter in {"matched", "mismatch", "missing_point", "missing_farmers"}:
        rows = [r for r in rows if r["status"] == status_filter]

    from .farmer_point_rollup import attach_reconcile_sync_snapshots

    rows = attach_reconcile_sync_snapshots(rows)

    totals = {
        "count": len(rows),
        "point_kg": sum((r["point_kg"] for r in rows), Decimal("0")).quantize(Decimal("0.01")),
        "point_liters": sum((r["point_liters"] for r in rows), Decimal("0")).quantize(Decimal("0.01")),
        "farmer_kg": sum((r["farmer_kg"] for r in rows), Decimal("0")).quantize(Decimal("0.01")),
        "farmer_liters": sum((r["farmer_liters"] for r in rows), Decimal("0")).quantize(Decimal("0.01")),
        "diff_kg": sum((r["diff_kg"] for r in rows), Decimal("0")).quantize(Decimal("0.01")),
        "diff_liters": sum((r["diff_liters"] for r in rows), Decimal("0")).quantize(Decimal("0.01")),
        "reconcile_kg": sum((r["reconcile_kg"] for r in rows), Decimal("0")).quantize(Decimal("0.01")),
        "issue_count": sum(1 for r in rows if r["status"] != "matched"),
    }
    return rows, totals


def _totals_from_reconcile_rows(rows):
    return {
        "count": len(rows),
        "point_kg": sum((r["point_kg"] for r in rows), Decimal("0")).quantize(Decimal("0.01")),
        "farmer_kg": sum((r["farmer_kg"] for r in rows), Decimal("0")).quantize(Decimal("0.01")),
        "diff_kg": sum((r["diff_kg"] for r in rows), Decimal("0")).quantize(Decimal("0.01")),
        "reconcile_kg": sum((r["reconcile_kg"] for r in rows), Decimal("0")).quantize(Decimal("0.01")),
        "issue_count": sum(1 for r in rows if r["status"] != "matched"),
    }


def build_branch_point_reconcile_report(branch_ids, start, end, *, status_filter=""):
    """Reconcile rows grouped by branch for a date range."""
    from branches.models import Branch

    from .models import MilkCollection

    scope_qs = MilkCollection.objects.filter(
        branch_id__in=branch_ids,
        date__gte=start,
        date__lte=end,
    )
    rows, grand_totals = build_point_farmer_reconciliation_rows(
        scope_qs,
        farmer_rollups_qs=scope_qs,
        status_filter=status_filter,
    )
    rows_by_branch = {}
    for row in rows:
        branch_key = row.get("branch_id") or row["branch_name"]
        rows_by_branch.setdefault(branch_key, []).append(row)

    branches = Branch.objects.filter(id__in=branch_ids).order_by("code", "name")
    branch_sections = []
    for branch in branches:
        branch_rows = rows_by_branch.pop(branch.id, [])
        if not branch_rows:
            continue
        branch_sections.append(
            {
                "branch": branch,
                "rows": branch_rows,
                "totals": _totals_from_reconcile_rows(branch_rows),
            }
        )

    for branch_key, branch_rows in sorted(
        rows_by_branch.items(),
        key=lambda item: str(item[0]),
    ):
        branch_sections.append(
            {
                "branch": None,
                "branch_label": branch_rows[0]["branch_name"] if branch_rows else str(branch_key),
                "rows": branch_rows,
                "totals": _totals_from_reconcile_rows(branch_rows),
            }
        )

    return branch_sections, grand_totals


def point_reconcile_report_export_matrix(branch_sections, grand_totals):
    headers = [
        "Branch",
        "Date",
        "Route",
        "Point",
        "Point kg",
        "Farmer kg",
        "Diff kg",
        "Reconcile",
        "Reconcile kg",
        "Status",
    ]
    matrix = [headers]
    for section in branch_sections:
        branch_label = (
            f"{section['branch'].code} — {section['branch'].name}"
            if section.get("branch")
            else section.get("branch_label", "—")
        )
        for row in section["rows"]:
            matrix.append(
                [
                    branch_label,
                    row["date"].isoformat(),
                    row["route_label"],
                    row["point_label"],
                    row["point_kg"],
                    row["farmer_kg"],
                    row["diff_kg"],
                    row["reconcile_source"],
                    row["reconcile_kg"],
                    row["status"],
                ]
            )
        totals = section["totals"]
        matrix.append(
            [
                branch_label,
                f"Total ({totals['count']} rows)",
                "",
                "",
                totals["point_kg"],
                totals["farmer_kg"],
                totals["diff_kg"],
                "",
                totals["reconcile_kg"],
                f"{totals['issue_count']} issue(s)",
            ]
        )
    if branch_sections:
        matrix.append(
            [
                "Grand total",
                f"({grand_totals['count']} rows)",
                "",
                "",
                grand_totals["point_kg"],
                grand_totals["farmer_kg"],
                grand_totals["diff_kg"],
                "",
                grand_totals["reconcile_kg"],
                f"{grand_totals['issue_count']} issue(s)",
            ]
        )
    return matrix
