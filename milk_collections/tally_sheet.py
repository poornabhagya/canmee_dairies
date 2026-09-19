"""Collection-point tally sheet: farmers × days grid with quantity and fat."""

from __future__ import annotations

import calendar
import json
from datetime import date
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date

from branches.models import Branch
from branches.utils import filter_by_user_branches, get_allowed_branches_qs
from canmee_dairies.constants import MILK_LITER_FACTOR
from masters.models import CollectionPoint, Farmer
from reports.payslip_labels import payment_sheet_period_english
from reports.services import date_range_inclusive

from .forms import parse_kg_expression
from .models import CollectionSource, MilkCollection, MilkFactor


def default_tally_period(reference: date | None = None) -> tuple[date, date]:
    """First half or second half of the month (matches common 1–15 tally sheets)."""
    today = reference or timezone.localdate()
    if today.day <= 15:
        return date(today.year, today.month, 1), date(today.year, today.month, 15)
    last_day = calendar.monthrange(today.year, today.month)[1]
    return date(today.year, today.month, 16), date(today.year, today.month, last_day)


def tally_period_value(start: date, end: date) -> str:
    return f"{start.isoformat()}_{end.isoformat()}"


def parse_tally_period_value(raw: str) -> tuple[date | None, date | None]:
    parts = (raw or "").strip().split("_", 1)
    if len(parts) != 2:
        return None, None
    start = parse_date(parts[0])
    end = parse_date(parts[1])
    if not start or not end:
        return None, None
    if start > end:
        start, end = end, start
    return start, end


def _month_half_periods(year: int, month: int) -> list[tuple[date, date]]:
    last_day = calendar.monthrange(year, month)[1]
    return [
        (date(year, month, 1), date(year, month, 15)),
        (date(year, month, 16), date(year, month, last_day)),
    ]


def iter_tally_periods(anchor: date, months_back: int = 12, months_forward: int = 1):
    start_year, start_month = anchor.year, anchor.month
    for _ in range(months_back):
        start_month -= 1
        if start_month < 1:
            start_month = 12
            start_year -= 1

    end_year, end_month = anchor.year, anchor.month
    for _ in range(months_forward):
        end_month += 1
        if end_month > 12:
            end_month = 1
            end_year += 1

    periods: list[tuple[date, date]] = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        periods.extend(_month_half_periods(year, month))
        month += 1
        if month > 12:
            month = 1
            year += 1
    return periods


def build_tally_period_choices(
    selected_start: date | None,
    selected_end: date | None,
    reference: date | None = None,
) -> list[dict]:
    anchor = reference or timezone.localdate()
    choices: list[dict] = []
    seen: set[str] = set()

    def add_choice(start: date, end: date):
        value = tally_period_value(start, end)
        if value in seen:
            return
        seen.add(value)
        choices.append(
            {
                "value": value,
                "label": payment_sheet_period_english(start, end),
                "start": start,
                "end": end,
            }
        )

    for start, end in reversed(iter_tally_periods(anchor)):
        add_choice(start, end)

    if selected_start and selected_end:
        add_choice(selected_start, selected_end)
        choices.sort(key=lambda row: row["start"], reverse=True)
    return choices


def _parse_period(request) -> tuple[date, date]:
    period_raw = (request.GET.get("period") or request.POST.get("period") or "").strip()
    if period_raw:
        start, end = parse_tally_period_value(period_raw)
        if start and end:
            return start, end

    from_raw = (request.GET.get("from") or request.POST.get("from") or "").strip()
    to_raw = (request.GET.get("to") or request.POST.get("to") or "").strip()
    if from_raw and to_raw:
        start = parse_date(from_raw)
        end = parse_date(to_raw)
        if start and end:
            if start > end:
                start, end = end, start
            return start, end
    return default_tally_period()


def _tally_branches_qs(user):
    return get_allowed_branches_qs(user).order_by("name")


def _show_tally_branch_filter(user) -> bool:
    if user.is_superuser:
        return True
    return _tally_branches_qs(user).count() > 1


def _parse_tally_branch_id(request, user) -> int | None:
    allowed_ids = set(_tally_branches_qs(user).values_list("pk", flat=True))
    if not _show_tally_branch_filter(user):
        if len(allowed_ids) == 1:
            return next(iter(allowed_ids))
        return None

    raw = (request.GET.get("branch") or request.POST.get("branch") or "").strip()
    if raw.isdigit():
        branch_id = int(raw)
        if user.is_superuser:
            if Branch.objects.filter(pk=branch_id).exists():
                return branch_id
        elif branch_id in allowed_ids:
            return branch_id
    return None


def tally_point_label(point) -> str:
    number = (point.number or "").strip()
    name = (point.name or "").strip()
    if number and name and name != number:
        return f"{number} — {name}"
    return name or number or "—"


def tally_sheet_query_params(
    *,
    point_id,
    start: date,
    end: date,
    unit: str = "",
    branch_id: int | None = None,
) -> dict[str, str]:
    params = {
        "point": str(point_id),
        "from": start.isoformat(),
        "to": end.isoformat(),
    }
    if unit:
        params["unit"] = unit
    if branch_id:
        params["branch"] = str(branch_id)
    return params


def _allowed_points_qs(user, branch_id=None):
    qs = CollectionPoint.objects.select_related("route", "route__branch").order_by(
        "route__branch__name", "route__code", "number"
    )
    qs = filter_by_user_branches(qs, user, "route__branch_id")
    if branch_id:
        qs = qs.filter(route__branch_id=branch_id)
    return qs


def get_object_or_404_point(request, point_id):
    from django.shortcuts import get_object_or_404

    return get_object_or_404(_allowed_points_qs(request.user), pk=point_id)


def _farmers_for_point(point):
    return list(
        Farmer.objects.filter(collection_point=point)
        .select_related("route", "branch")
        .order_by("registration_number", "common_name", "full_name")
    )


def _quantize(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _display_qty(kg: Decimal, unit: str) -> str:
    if kg is None or kg == 0:
        return ""
    if unit == Branch.CollectionUnit.LITERS:
        return format(_quantize(kg * MILK_LITER_FACTOR), "f")
    return format(_quantize(kg), "f")


def _parse_display_qty(raw, unit: str) -> Decimal | None:
    text = str(raw or "").strip()
    if not text or text in {"-", "—"}:
        return None
    try:
        val = parse_kg_expression(text)
    except ValueError as exc:
        raise ValidationError(f"Invalid quantity: {text!r}") from exc
    if val < 0:
        raise ValidationError("Quantity cannot be negative.")
    if unit == Branch.CollectionUnit.LITERS:
        val = (val / MILK_LITER_FACTOR).quantize(Decimal("0.01"))
    return val.quantize(Decimal("0.01"))


def _parse_factor_field(raw, label: str) -> Decimal | None:
    text = str(raw or "").strip()
    if not text or text in {"-", "—"}:
        return None
    try:
        val = Decimal(text.replace(",", "."))
    except InvalidOperation as exc:
        raise ValidationError(f"Invalid {label}: {text!r}") from exc
    if val < 0:
        raise ValidationError(f"{label} cannot be negative.")
    return val.quantize(Decimal("0.01"))


def _parse_fat(raw) -> Decimal | None:
    return _parse_factor_field(raw, "fat %")


def _parse_lr(raw) -> Decimal | None:
    return _parse_factor_field(raw, "LR")


def _format_sheet_qty(kg, unit: str) -> str:
    """Format stored kg for sheet display (shows 0; keeps sign for diffs)."""
    kg = Decimal(str(kg or 0))
    if unit == Branch.CollectionUnit.LITERS:
        val = _quantize(kg * MILK_LITER_FACTOR)
    else:
        val = _quantize(kg)
    return format(val, "f")


def _enrich_reconcile_display(rows, totals, qty_unit: str):
    """Add display qty fields for the active sheet unit (kg stored in DB)."""
    for row in rows:
        row["point_qty"] = _format_sheet_qty(row["point_kg"], qty_unit)
        row["farmer_qty"] = _format_sheet_qty(row["farmer_kg"], qty_unit)
        row["diff_qty"] = _format_sheet_qty(row["diff_kg"], qty_unit)
        row["reconcile_qty"] = _format_sheet_qty(row["reconcile_kg"], qty_unit)
    if totals:
        totals["point_qty"] = _format_sheet_qty(totals["point_kg"], qty_unit)
        totals["farmer_qty"] = _format_sheet_qty(totals["farmer_kg"], qty_unit)
        totals["diff_qty"] = _format_sheet_qty(totals["diff_kg"], qty_unit)
        totals["reconcile_qty"] = _format_sheet_qty(totals["reconcile_kg"], qty_unit)
    return rows, totals


def build_tally_reconcile_rows(point, start, end):
    """Point vs farmer reconcile rows for one collection point over a period."""
    from .point_reconciliation import build_point_farmer_reconciliation_rows

    if not point or not start or not end:
        return [], None

    base_qs = MilkCollection.objects.filter(
        branch_id=point.route.branch_id,
        date__gte=start,
        date__lte=end,
    )
    rows, _totals = build_point_farmer_reconciliation_rows(base_qs)
    filtered = [
        row
        for row in rows
        if row["collection_point_id"] == point.pk and row["route_id"] == point.route_id
    ]
    filtered.sort(key=lambda r: r["date"])
    if not filtered:
        return [], None

    totals = {
        "count": len(filtered),
        "point_kg": sum((r["point_kg"] for r in filtered), Decimal("0")).quantize(Decimal("0.01")),
        "farmer_kg": sum((r["farmer_kg"] for r in filtered), Decimal("0")).quantize(Decimal("0.01")),
        "diff_kg": sum((r["diff_kg"] for r in filtered), Decimal("0")).quantize(Decimal("0.01")),
        "reconcile_kg": sum((r["reconcile_kg"] for r in filtered), Decimal("0")).quantize(
            Decimal("0.01")
        ),
    }
    return filtered, totals


def build_tally_sheet_context(request) -> dict:
    start, end = _parse_period(request)
    branch_id = _parse_tally_branch_id(request, request.user)
    branches = list(_tally_branches_qs(request.user))
    show_branch_filter = _show_tally_branch_filter(request.user)

    point_id = (request.GET.get("point") or request.POST.get("point") or "").strip()
    all_points = list(_allowed_points_qs(request.user, branch_id=None))
    points = list(_allowed_points_qs(request.user, branch_id))
    for p in all_points:
        p.tally_label = tally_point_label(p)
    point = None
    if point_id.isdigit():
        point = next((p for p in points if p.pk == int(point_id)), None)
    if point is None and points:
        point = points[0]

    qty_unit = (request.GET.get("unit") or request.POST.get("unit") or "").strip()
    farmers = []
    days = date_range_inclusive(start, end) if start and end else []
    rows = []
    branch_unit = Branch.CollectionUnit.KG

    if point is not None:
        branch_unit = (
            point.route.branch.collection_unit
            if point.route.branch_id
            else Branch.CollectionUnit.KG
        )
        if qty_unit not in {Branch.CollectionUnit.KG, Branch.CollectionUnit.LITERS}:
            qty_unit = branch_unit
        farmers = _farmers_for_point(point)
        farmer_ids = [f.pk for f in farmers]

        collections = {
            (row.farmer_id, row.date): row
            for row in MilkCollection.objects.filter(
                source=CollectionSource.FARMER,
                route_id=point.route_id,
                farmer_id__in=farmer_ids,
                date__gte=start,
                date__lte=end,
            )
        }
        factors = {
            (row.farmer_id, row.date): row
            for row in MilkFactor.objects.filter(
                source=CollectionSource.FARMER,
                route_id=point.route_id,
                farmer_id__in=farmer_ids,
                date__gte=start,
                date__lte=end,
            )
        }

        day_kg_totals = [Decimal("0")] * len(days)
        for index, farmer in enumerate(farmers, start=1):
            cells = []
            total_kg = Decimal("0")
            for day_idx, day in enumerate(days):
                mc = collections.get((farmer.pk, day))
                mf = factors.get((farmer.pk, day))
                kg = mc.kg if mc else Decimal("0")
                total_kg += kg
                day_kg_totals[day_idx] += kg
                cells.append(
                    {
                        "date": day,
                        "date_label": f"{day.day:02d}",
                        "qty": _display_qty(kg, qty_unit) if mc else "",
                        "fat": format(mf.fat, "f") if mf and mf.fat else "",
                        "lr": format(mf.lr, "f") if mf and mf.lr else "",
                        "is_paid": bool(mc and mc.is_paid),
                        "mc_id": mc.pk if mc else None,
                        "mf_id": mf.pk if mf else None,
                    }
                )
            rows.append(
                {
                    "index": index,
                    "farmer": farmer,
                    "name": (farmer.common_name or farmer.full_name or "").strip() or "—",
                    "cells": cells,
                    "total_qty": _display_qty(total_kg, qty_unit) if total_kg else "",
                    "total_kg": total_kg,
                }
            )
        grand_total_kg = sum(day_kg_totals, Decimal("0"))
        day_totals = [
            {
                "date": day,
                "qty": _display_qty(day_kg, qty_unit) if day_kg else "",
            }
            for day, day_kg in zip(days, day_kg_totals)
        ]
        grand_total_qty = _display_qty(grand_total_kg, qty_unit) if grand_total_kg else ""
    else:
        qty_unit = branch_unit
        day_totals = []
        grand_total_qty = ""

    reconcile_rows = []
    reconcile_totals = None
    if point is not None and start and end:
        reconcile_rows, reconcile_totals = build_tally_reconcile_rows(point, start, end)
        if reconcile_rows:
            reconcile_rows, reconcile_totals = _enrich_reconcile_display(
                reconcile_rows, reconcile_totals, qty_unit
            )

    return {
        "points": points,
        "all_points": all_points,
        "point": point,
        "branches": branches,
        "selected_branch_id": branch_id,
        "show_branch_filter": show_branch_filter,
        "start": start,
        "end": end,
        "days": days,
        "rows": rows,
        "day_totals": day_totals,
        "grand_total_qty": grand_total_qty,
        "qty_unit": qty_unit,
        "branch_unit": branch_unit,
        "period_label": payment_sheet_period_english(start, end) if start and end else "",
        "point_display_name": tally_point_label(point) if point else "",
        "reconcile_rows": reconcile_rows,
        "reconcile_totals": reconcile_totals,
        "can_view_reconcile": request.user.has_perm("collections.view_milkcollectionreconcile"),
        "can_set_reconcile_choice": request.user.has_perm(
            "collections.change_milkcollectionreconcilechoice"
        ),
        "can_sync_point_totals": request.user.has_perm("collections.sync_milkcollectionreconcile"),
        "can_edit_qty": request.user.has_perm("collections.change_milkcollectiontallysheet"),
        "can_save_fat": request.user.has_perm("collections.change_milkcollectiontallyfactor"),
    }


def _parse_tally_cells(request, farmers, start, end):
    """Cells from compact JSON payload (large sheets) or legacy per-field POST."""
    farmer_ids = {f.pk for f in farmers}
    valid_days = {d.isoformat() for d in date_range_inclusive(start, end)}
    payload_raw = (request.POST.get("tally_data") or "").strip()

    if payload_raw:
        try:
            raw_cells = json.loads(payload_raw)
        except json.JSONDecodeError as exc:
            raise ValidationError("Invalid tally sheet data.") from exc
        if not isinstance(raw_cells, list):
            raise ValidationError("Invalid tally sheet data.")
        cells = []
        for item in raw_cells:
            if not isinstance(item, dict):
                continue
            try:
                farmer_id = int(item.get("farmer_id"))
            except (TypeError, ValueError):
                continue
            day_str = str(item.get("date") or "").strip()
            day = parse_date(day_str)
            if farmer_id not in farmer_ids or day is None or day_str not in valid_days:
                continue
            cells.append(
                {
                    "farmer_id": farmer_id,
                    "date": day,
                    "qty": item.get("qty"),
                    "fat": item.get("fat"),
                    "lr": item.get("lr"),
                }
            )
        return cells

    cells = []
    for farmer in farmers:
        for day in date_range_inclusive(start, end):
            cells.append(
                {
                    "farmer_id": farmer.pk,
                    "date": day,
                    "qty": request.POST.get(f"qty_{farmer.pk}_{day.isoformat()}"),
                    "fat": request.POST.get(f"fat_{farmer.pk}_{day.isoformat()}"),
                    "lr": request.POST.get(f"lr_{farmer.pk}_{day.isoformat()}"),
                }
            )
    return cells


def _save_tally_qty_cell(point, farmer, day, qty_raw, qty_unit, user) -> dict:
    existing_mc = MilkCollection.objects.filter(
        source=CollectionSource.FARMER,
        route_id=point.route_id,
        farmer_id=farmer.pk,
        date=day,
    ).first()
    if existing_mc and existing_mc.is_paid:
        if (qty_raw or "").strip():
            return {"saved": False, "skipped_paid": True, "display": _display_qty(existing_mc.kg, qty_unit)}
        return {"saved": False, "skipped_paid": False, "display": _display_qty(existing_mc.kg, qty_unit)}

    qty_kg = _parse_display_qty(qty_raw, qty_unit)
    if qty_kg is None:
        if existing_mc:
            existing_mc.delete()
        return {"saved": True, "skipped_paid": False, "display": ""}

    if existing_mc:
        existing_mc.kg = qty_kg
        existing_mc.save(update_fields=["kg", "liters", "updated_at"])
    else:
        MilkCollection.objects.create(
            date=day,
            route_id=point.route_id,
            source=CollectionSource.FARMER,
            farmer_id=farmer.pk,
            collection_point=None,
            kg=qty_kg,
            created_by=user,
        )
    return {"saved": True, "skipped_paid": False, "display": _display_qty(qty_kg, qty_unit)}


def _save_tally_factor_cell(point, farmer, day, field, value_raw, user) -> dict:
    existing_mf = MilkFactor.objects.filter(
        source=CollectionSource.FARMER,
        route_id=point.route_id,
        farmer_id=farmer.pk,
        date=day,
    ).first()
    if field == "fat":
        fat_val = _parse_fat(value_raw)
        fat_stored = fat_val if fat_val is not None else Decimal("0")
        lr_stored = existing_mf.lr if existing_mf else Decimal("0")
        display = format(fat_stored, "f") if fat_stored else ""
    else:
        lr_val = _parse_lr(value_raw)
        lr_stored = lr_val if lr_val is not None else Decimal("0")
        fat_stored = existing_mf.fat if existing_mf else Decimal("0")
        display = format(lr_stored, "f") if lr_stored else ""

    if fat_stored == 0 and lr_stored == 0:
        if existing_mf:
            existing_mf.delete()
        return {"saved": True, "display": ""}

    if existing_mf:
        existing_mf.fat = fat_stored
        existing_mf.lr = lr_stored
        existing_mf.save(update_fields=["fat", "lr", "updated_at"])
    else:
        MilkFactor.objects.create(
            date=day,
            route_id=point.route_id,
            source=CollectionSource.FARMER,
            farmer_id=farmer.pk,
            collection_point=None,
            fat=fat_stored,
            lr=lr_stored,
        )
    return {"saved": True, "display": display}


def _save_tally_factor_row(point, farmer, day, fat_raw, lr_raw, user) -> bool:
    """Save fat and LR together (bulk sheet). Returns True when a row was stored."""
    existing_mf = MilkFactor.objects.filter(
        source=CollectionSource.FARMER,
        route_id=point.route_id,
        farmer_id=farmer.pk,
        date=day,
    ).first()
    fat_val = _parse_fat(fat_raw)
    lr_val = _parse_lr(lr_raw)
    if fat_val is None and lr_val is None:
        if existing_mf:
            existing_mf.delete()
        return False
    fat_stored = fat_val if fat_val is not None else Decimal("0")
    lr_stored = lr_val if lr_val is not None else Decimal("0")
    if fat_stored == 0 and lr_stored == 0:
        if existing_mf:
            existing_mf.delete()
        return False
    if existing_mf:
        existing_mf.fat = fat_stored
        existing_mf.lr = lr_stored
        existing_mf.save(update_fields=["fat", "lr", "updated_at"])
    else:
        MilkFactor.objects.create(
            date=day,
            route_id=point.route_id,
            source=CollectionSource.FARMER,
            farmer_id=farmer.pk,
            collection_point=None,
            fat=fat_stored,
            lr=lr_stored,
        )
    return True


@transaction.atomic
def save_tally_cell(request) -> dict:
    """Save one tally cell (qty, fat, or lr) and return display values for the client."""
    field = (request.POST.get("field") or "").strip().lower()
    if field == "qty":
        if not request.user.has_perm("collections.change_milkcollectiontallysheet"):
            raise PermissionDenied
    elif field in {"fat", "lr"}:
        if not request.user.has_perm("collections.change_milkcollectiontallyfactor"):
            raise PermissionDenied
    else:
        raise ValidationError("Invalid field.")

    point_id = (request.POST.get("point") or "").strip()
    farmer_id_raw = (request.POST.get("farmer_id") or "").strip()
    date_raw = (request.POST.get("date") or "").strip()
    value_raw = request.POST.get("value", "")

    if not point_id.isdigit() or not farmer_id_raw.isdigit():
        raise ValidationError("Invalid tally cell.")
    if field not in {"qty", "fat", "lr"}:
        raise ValidationError("Invalid field.")

    point = get_object_or_404_point(request, int(point_id))
    farmer_id = int(farmer_id_raw)
    day = parse_date(date_raw)
    if day is None:
        raise ValidationError("Invalid date.")

    start, end = _parse_period(request)
    if day < start or day > end:
        raise ValidationError("Date is outside the tally period.")

    farmer = next((f for f in _farmers_for_point(point) if f.pk == farmer_id), None)
    if farmer is None:
        raise ValidationError("Farmer is not assigned to this collection point.")

    qty_unit = (request.POST.get("unit") or "").strip()
    branch_unit = (
        point.route.branch.collection_unit
        if point.route.branch_id
        else Branch.CollectionUnit.KG
    )
    if qty_unit not in {Branch.CollectionUnit.KG, Branch.CollectionUnit.LITERS}:
        qty_unit = branch_unit

    if field == "qty":
        result = _save_tally_qty_cell(point, farmer, day, value_raw, qty_unit, request.user)
    else:
        result = _save_tally_factor_cell(point, farmer, day, field, value_raw, request.user)

    return {
        "field": field,
        "farmer_id": farmer_id,
        "date": day.isoformat(),
        **result,
    }


@transaction.atomic
def save_tally_sheet(request) -> dict:
    if not request.user.has_perm("collections.change_milkcollectiontallysheet"):
        raise PermissionDenied
    can_save_fat = request.user.has_perm("collections.change_milkcollectiontallyfactor")
    point_id = (request.POST.get("point") or "").strip()
    if not point_id.isdigit():
        raise ValidationError("Select a collection point.")
    point = get_object_or_404_point(request, int(point_id))
    start, end = _parse_period(request)
    qty_unit = (request.POST.get("unit") or "").strip()
    branch_unit = (
        point.route.branch.collection_unit
        if point.route.branch_id
        else Branch.CollectionUnit.KG
    )
    if qty_unit not in {Branch.CollectionUnit.KG, Branch.CollectionUnit.LITERS}:
        qty_unit = branch_unit

    farmers = _farmers_for_point(point)
    farmer_by_id = {f.pk: f for f in farmers}
    saved_qty = 0
    saved_factor = 0
    skipped_paid = 0

    for cell in _parse_tally_cells(request, farmers, start, end):
        farmer = farmer_by_id[cell["farmer_id"]]
        day = cell["date"]
        qty_raw = cell["qty"]
        fat_raw = cell["fat"]
        lr_raw = cell["lr"]

        qty_result = _save_tally_qty_cell(point, farmer, day, qty_raw, qty_unit, request.user)
        if qty_result.get("skipped_paid") and (qty_raw or "").strip():
            skipped_paid += 1
        elif qty_result.get("saved") and qty_result.get("display"):
            saved_qty += 1

        if can_save_fat and _save_tally_factor_row(point, farmer, day, fat_raw, lr_raw, request.user):
            saved_factor += 1

    return {
        "saved_qty": saved_qty,
        "saved_factor": saved_factor,
        "skipped_paid": skipped_paid,
        "point": point,
        "start": start,
        "end": end,
    }
