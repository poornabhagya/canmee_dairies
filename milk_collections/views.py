import json
from urllib.parse import urlencode
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from collections import defaultdict

from canmee_dairies.constants import MILK_LITER_FACTOR
from canmee_dairies.formatting import format_money
from canmee_dairies.mixins import ModelFormPageTitleMixin
from branches.models import Branch, BranchMilkStock, BranchMilkStockAdjustment
from branches.utils import filter_by_user_branches, filter_m2m_by_branch, get_allowed_branches_qs, get_default_branch_id
from dispatch.models import MilkDistribution
from masters.models import Buyer, CollectionPoint, Farmer, Route
from suppliers.models import FarmerGoodsIssue, RawMilkSupplierCollection, Supplier
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum, Count
from django.http import HttpResponseBadRequest, HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import ListView, CreateView, UpdateView
from suppliers.services import fifo_price_map_for_issues
from .forms import MilkCollectionForm, MilkFactorForm
from .models import CollectionSource, MilkCollection, MilkCollectionAdjustment, MilkFactor
from .totals import kg_liters_totals
from .point_reconciliation import build_point_farmer_reconciliation_rows
from .farmer_point_rollup import (
    reverse_collection_point_sync_from_snapshot,
    sync_collection_point_total_from_farmers,
)
from .point_collection_transfer import transfer_point_collections
from .reconcile_payment import set_reconcile_choice
from .tally_sheet import build_tally_sheet_context, save_tally_cell, save_tally_sheet, tally_sheet_query_params
from .models import CollectionPointMilkReconcileChoice


def _is_point_uniqueness_violation(exc):
    text = ""
    if isinstance(exc, ValidationError):
        if getattr(exc, "error_dict", None):
            for msgs in exc.error_dict.values():
                for item in msgs:
                    text += str(item) + " "
        if getattr(exc, "messages", None):
            text += " ".join(str(m) for m in exc.messages)
        if not text.strip():
            text = str(exc)
    else:
        text = str(exc)
    return "uniq_daily_route_point" in text


def _is_farmer_uniqueness_violation(exc):
    text = ""
    if isinstance(exc, ValidationError):
        if getattr(exc, "error_dict", None):
            for msgs in exc.error_dict.values():
                for item in msgs:
                    text += str(item) + " "
        if getattr(exc, "messages", None):
            text += " ".join(str(m) for m in exc.messages)
        if not text.strip():
            text = str(exc)
    else:
        text = str(exc)
    return "uniq_daily_route_farmer" in text


def _reconcile_return_redirect(request, redirect_query):
    if (request.POST.get("return_to") or "").strip() == "tally":
        point = (request.POST.get("point") or "").strip()
        from_d = (request.POST.get("from") or "").strip()
        to_d = (request.POST.get("to") or "").strip()
        period = (request.POST.get("period") or "").strip()
        unit = (request.POST.get("unit") or "").strip()
        branch = (request.POST.get("branch") or "").strip()
        if point.isdigit() and (period or (from_d and to_d)):
            params = {"point": point}
            if period:
                params["period"] = period
            elif from_d and to_d:
                params["from"] = from_d
                params["to"] = to_d
            if unit:
                params["unit"] = unit
            if branch.isdigit():
                params["branch"] = branch
            return redirect(f"{reverse('milk-collection-tally')}?{urlencode(params)}")
    if redirect_query:
        return redirect(f"{reverse('milk-collection-list')}?{redirect_query}")
    return redirect(f"{reverse('milk-collection-list')}?tab=reconcile")


def _existing_point_row(date, route, collection_point):
    if not date or not route or not collection_point:
        return None
    return (
        MilkCollection.objects.filter(
            date=date,
            route=route,
            collection_point=collection_point,
            source=CollectionSource.POINT,
        )
        .only("id", "kg")
        .first()
    )


def _existing_farmer_row(date, route, farmer):
    if not date or not route or not farmer:
        return None
    return (
        MilkCollection.objects.filter(
            date=date,
            route=route,
            farmer=farmer,
            source=CollectionSource.FARMER,
        )
        .only("id", "kg")
        .first()
    )


def _conflict_json(existing_row, attempted_kg, kind="point"):
    return JsonResponse(
        {
            "ok": False,
            "conflict": True,
            "conflict_kind": kind,
            "milk_collection_id": existing_row.pk,
            "current_kg": str(existing_row.kg),
            "attempted_kg": str(attempted_kg),
        }
    )


def _collection_unit_for_row(row):
    branch = getattr(row, "branch", None)
    if branch is not None and getattr(branch, "collection_unit", None):
        return branch.collection_unit
    route = getattr(row, "route", None)
    if route is not None:
        route_branch = getattr(route, "branch", None)
        if route_branch is not None and getattr(route_branch, "collection_unit", None):
            return route_branch.collection_unit
    return Branch.CollectionUnit.KG


def _last_user_collection_payload(user):
    """Most recent milk collection row entered by this user (branch-scoped)."""
    if not user or not getattr(user, "pk", None):
        return None
    qs = (
        MilkCollection.objects.filter(created_by_id=user.pk, is_deleted=False)
        .select_related("collection_point", "farmer", "route", "route__branch", "branch")
        .order_by("-created_at", "-id")
    )
    qs = filter_by_user_branches(qs, user, "branch_id")
    row = qs.first()
    if not row:
        return None
    if row.collection_point_id and row.collection_point:
        target = f"{row.collection_point.number} - {row.collection_point.name}"
    elif row.farmer_id and row.farmer:
        target = (row.farmer.common_name or row.farmer.full_name or "").strip() or "—"
    else:
        target = "—"
    route_label = "—"
    if row.route_id and row.route:
        route_label = (row.route.name or "").strip() or "—"
    return {
        "id": row.id,
        "date": row.date.isoformat() if row.date else "",
        "time": timezone.localtime(row.created_at).strftime("%H:%M"),
        "source": row.get_source_display(),
        "target": target,
        "route": route_label,
        "kg": str(row.kg),
        "liters": str(row.liters),
        "collection_unit": _collection_unit_for_row(row),
    }


def _resolve_collection_date(raw):
    """Parse YYYY-MM-DD; fall back to local today when missing or invalid."""
    if raw:
        parsed = parse_date(str(raw).strip())
        if parsed:
            return parsed
    return timezone.localdate()


def _today_collection_rows_payload(user, collection_date=None, branch_id=None):
    collection_date = _resolve_collection_date(collection_date)
    today_rows_qs = (
        MilkCollection.objects.filter(date=collection_date)
        .select_related("collection_point", "farmer", "route", "route__branch", "branch")
        .order_by("-created_at")
    )
    today_rows_qs = filter_by_user_branches(today_rows_qs, user, "branch_id")
    if branch_id:
        today_rows_qs = today_rows_qs.filter(branch_id=branch_id)
    today_rows = list(today_rows_qs)
    row_ids = [r.id for r in today_rows]
    factor_key_map = {}
    for row in today_rows:
        factor_key_map[(row.date, row.route_id, row.collection_point_id, row.farmer_id)] = row.id

    adjustment_map = {}
    if row_ids:
        adjustment_rows = (
            MilkCollectionAdjustment.objects.filter(milk_collection_id__in=row_ids)
            .order_by("-created_at", "-id")
            .values("milk_collection_id", "delta_kg", "created_at")
        )
        for item in adjustment_rows:
            rid = item["milk_collection_id"]
            adjustment_map.setdefault(rid, []).append(
                {
                    "delta_kg": str(item["delta_kg"] or Decimal("0.00")),
                    "time": timezone.localtime(item["created_at"]).strftime("%H:%M"),
                }
            )
    factor_map = {}
    if today_rows:
        factor_qs = MilkFactor.objects.filter(
            date=collection_date,
            route_id__in={r.route_id for r in today_rows},
        ).values("date", "route_id", "collection_point_id", "farmer_id", "fat", "snf")
        for fac in factor_qs:
            row_id = factor_key_map.get(
                (fac["date"], fac["route_id"], fac["collection_point_id"], fac["farmer_id"])
            )
            if row_id:
                factor_map[row_id] = {
                    "fat": str(fac["fat"]) if fac["fat"] is not None else "",
                    "snf": str(fac["snf"]) if fac["snf"] is not None else "",
                }

    return [
        {
            "id": row.id,
            "point_id": row.collection_point_id,
            "farmer_id": row.farmer_id,
            "route_id": row.route_id,
            "source": row.get_source_display(),
            "target": (
                f"{row.collection_point.number} - {row.collection_point.name}"
                if row.collection_point_id and row.collection_point
                else (
                    row.farmer.common_name or row.farmer.full_name
                    if row.farmer_id and row.farmer
                    else "—"
                )
            ),
            "kg": str(row.kg),
            "liters": str(row.liters),
            "collection_unit": _collection_unit_for_row(row),
            "time": timezone.localtime(row.created_at).strftime("%H:%M"),
            "adjustments": adjustment_map.get(row.id, []),
            "factor": factor_map.get(row.id),
        }
        for row in today_rows
    ]


class MilkCollectionFormContextMixin:
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pts = CollectionPoint.objects.select_related("route", "route__branch").order_by(
            "route__code", "number"
        )
        farmers_qs = Farmer.objects.select_related("route", "branch").order_by(
            "common_name", "full_name"
        )
        route_unit_qs = Route.objects.select_related("branch").order_by("code")
        if not self.request.user.is_superuser:
            allowed_ids = list(get_allowed_branches_qs(self.request.user).values_list("id", flat=True))
            pts = pts.filter(route__branch_id__in=allowed_ids) if allowed_ids else pts.none()
            farmers_qs = farmers_qs.filter(branch_id__in=allowed_ids) if allowed_ids else farmers_qs.none()
            route_unit_qs = (
                route_unit_qs.filter(branch_id__in=allowed_ids) if allowed_ids else route_unit_qs.none()
            )
        ctx["collection_points_json"] = [
            {
                "id": p.pk,
                "number": p.number,
                "route_id": p.route_id,
                "route_code": p.route.code,
                "route_name": p.route.name,
                "point_name": p.name,
                "label": f"{p.route.code} - {p.number} - {p.name}",
                "branch_id": p.route.branch_id,
                "collection_unit": (
                    p.route.branch.collection_unit
                    if p.route.branch_id
                    else Branch.CollectionUnit.KG
                ),
            }
            for p in pts
        ]
        _pk = 888888888
        ctx["milk_adjust_url_wildcard"] = reverse("milk-collection-adjust", kwargs={"pk": _pk})
        ctx["milk_adjust_url_pk_token"] = str(_pk)
        ctx["farmers_route_json"] = [
            {
                "id": f.pk,
                "route_id": f.route_id,
                "branch_id": f.branch_id,
                "registration_number": (f.registration_number or ""),
                "common_name": (f.common_name or f.full_name or "").strip(),
                "collection_unit": (
                    f.branch.collection_unit
                    if f.branch_id
                    else Branch.CollectionUnit.KG
                ),
            }
            for f in farmers_qs
        ]
        ctx["route_collection_unit_json"] = {
            str(r.pk): (
                r.branch.collection_unit if r.branch_id else Branch.CollectionUnit.KG
            )
            for r in route_unit_qs
        }
        ctx["routes_branch_json"] = {str(r.pk): r.branch_id for r in route_unit_qs}
        allowed_branches = get_allowed_branches_qs(self.request.user).order_by("code", "name")
        branch_count = allowed_branches.count()
        obj = getattr(self, "object", None)
        initial_branch_id = None
        if obj is not None and getattr(obj, "pk", None):
            initial_branch_id = getattr(obj, "branch_id", None)
            if not initial_branch_id and getattr(obj, "route_id", None) and obj.route:
                initial_branch_id = obj.route.branch_id
        if initial_branch_id is None:
            initial_branch_id = get_default_branch_id(self.request.user)
        if initial_branch_id is None and branch_count >= 1:
            first_branch = allowed_branches.first()
            initial_branch_id = first_branch.pk if first_branch else None
        ctx["milk_branches_json"] = [
            {"id": b.id, "label": f"{b.code} - {b.name}"} for b in allowed_branches
        ]
        ctx["milk_branch_select_count"] = branch_count
        ctx["milk_show_branch_select"] = self.request.user.is_superuser or branch_count > 1
        ctx["milk_initial_branch_id"] = initial_branch_id
        collection_date = timezone.localdate()
        if obj is not None and getattr(obj, "date", None):
            collection_date = obj.date
        ctx["collection_date_iso"] = collection_date.isoformat()
        ctx["today_collection_rows_json"] = _today_collection_rows_payload(
            self.request.user, collection_date, branch_id=initial_branch_id
        )
        ctx["last_user_collection_json"] = _last_user_collection_payload(self.request.user)
        ctx["milk_collection_initial_qty_unit"] = ""
        if obj is not None and getattr(obj, "pk", None):
            br = getattr(obj, "branch", None)
            if br is None and getattr(obj, "branch_id", None):
                br = Branch.objects.only("collection_unit").filter(pk=obj.branch_id).first()
            if br is not None:
                ctx["milk_collection_initial_qty_unit"] = br.collection_unit
        return ctx


class MilkCollectionTodayHistoryJsonView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "collections.view_milkcollection"

    def get(self, request, *args, **kwargs):
        collection_date = _resolve_collection_date(request.GET.get("date"))
        branch_id = None
        branch_raw = (request.GET.get("branch") or "").strip()
        if branch_raw.isdigit():
            branch_id = int(branch_raw)
            if not request.user.is_superuser:
                allowed_ids = list(
                    get_allowed_branches_qs(request.user).values_list("id", flat=True)
                )
                if branch_id not in allowed_ids:
                    branch_id = None
        return JsonResponse(
            {
                "ok": True,
                "date": collection_date.isoformat(),
                "rows": _today_collection_rows_payload(
                    request.user, collection_date, branch_id=branch_id
                ),
                "last_user_collection": _last_user_collection_payload(request.user),
            }
        )


class MilkFactorDuplicateCheckView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "collections.view_milkfactor"

    def get(self, request, *args, **kwargs):
        source = (request.GET.get("source") or "").strip()
        date_raw = request.GET.get("date")
        route_raw = request.GET.get("route")
        point_raw = request.GET.get("point")
        farmer_raw = request.GET.get("farmer")
        d = parse_date(date_raw or "")
        if source not in {CollectionSource.POINT, CollectionSource.FARMER}:
            return JsonResponse({"ok": False, "exists": False, "error": "Invalid source."}, status=400)
        if not d or not route_raw or not str(route_raw).isdigit():
            return JsonResponse({"ok": False, "exists": False}, status=200)
        route_id = int(route_raw)
        route = Route.objects.filter(pk=route_id).first()
        if not route:
            return JsonResponse({"ok": False, "exists": False}, status=200)
        if not request.user.is_superuser:
            allowed_ids = list(get_allowed_branches_qs(request.user).values_list("id", flat=True))
            if route.branch_id not in allowed_ids:
                return JsonResponse({"ok": False, "exists": False}, status=200)
        qs = MilkFactor.objects.filter(date=d, route_id=route_id, source=source)
        if source == CollectionSource.POINT:
            if not point_raw or not str(point_raw).isdigit():
                return JsonResponse({"ok": False, "exists": False}, status=200)
            qs = qs.filter(collection_point_id=int(point_raw))
        else:
            if not farmer_raw or not str(farmer_raw).isdigit():
                return JsonResponse({"ok": False, "exists": False}, status=200)
            qs = qs.filter(farmer_id=int(farmer_raw))
        exists = qs.exists()
        return JsonResponse({"ok": True, "exists": exists, "kind": source}, status=200)


class MilkFactorFormContextMixin:
    """Collection points + farmer→route JSON for the same UX as milk collection."""

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        pts = CollectionPoint.objects.select_related("route", "route__branch").order_by(
            "route__code", "number"
        )
        farmers_qs = Farmer.objects.select_related("route", "branch").order_by(
            "common_name", "full_name"
        )
        if not self.request.user.is_superuser:
            allowed_ids = list(get_allowed_branches_qs(self.request.user).values_list("id", flat=True))
            pts = pts.filter(route__branch_id__in=allowed_ids) if allowed_ids else pts.none()
            farmers_qs = farmers_qs.filter(branch_id__in=allowed_ids) if allowed_ids else farmers_qs.none()
        ctx["collection_points_json"] = [
            {
                "id": p.pk,
                "number": p.number,
                "route_id": p.route_id,
                "route_code": p.route.code,
                "route_name": p.route.name,
                "point_name": p.name,
                "label": f"{p.route.code} - {p.number} - {p.name}",
                "branch_id": p.route.branch_id,
                "collection_unit": (
                    p.route.branch.collection_unit
                    if p.route.branch_id
                    else Branch.CollectionUnit.KG
                ),
            }
            for p in pts
        ]
        ctx["farmers_route_json"] = [
            {
                "id": f.pk,
                "route_id": f.route_id,
                "branch_id": f.branch_id,
                "registration_number": (f.registration_number or ""),
                "common_name": (f.common_name or f.full_name or "").strip(),
                "collection_unit": (
                    f.branch.collection_unit
                    if f.branch_id
                    else Branch.CollectionUnit.KG
                ),
            }
            for f in farmers_qs
        ]
        return ctx


class MilkCollectionListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = MilkCollection
    paginate_by = 250
    page_size_choices = (100, 250, 500, 1000)
    per_page_all = "all"
    template_name = "collections/milk_collection_list.html"
    permission_required = "collections.view_milkcollection"

    def dispatch(self, request, *args, **kwargs):
        tab = (request.GET.get("tab") or "route").strip().lower()
        if tab == "reconcile" and not request.user.has_perm(
            "collections.view_milkcollectionreconcile"
        ):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)

    def _milk_list_scope_qs(self, *, apply_search=True):
        qs = MilkCollection.objects.select_related(
            "route", "collection_point", "farmer", "created_by", "branch"
        ).order_by("-date", "route__code", "collection_point__number", "farmer__full_name")
        qs = filter_by_user_branches(qs, self.request.user, "branch_id")
        branch_raw = (self.request.GET.get("branch") or "").strip()
        if branch_raw.isdigit():
            qs = qs.filter(branch_id=int(branch_raw))
        else:
            branch_ids = self.request.GET.getlist("branches")
            if branch_ids:
                safe_ids = [int(b) for b in branch_ids if str(b).isdigit()]
                if safe_ids:
                    qs = qs.filter(branch_id__in=safe_ids)
        if apply_search:
            tab = (self.request.GET.get("tab") or "route").strip().lower()
            q = self.request.GET.get("q")
            if q and tab not in {"route", "point", "farmer"}:
                qs = qs.filter(
                    Q(route__name__icontains=q)
                    | Q(route__code__icontains=q)
                    | Q(collection_point__number__icontains=q)
                    | Q(collection_point__name__icontains=q)
                    | Q(farmer__full_name__icontains=q)
                    | Q(farmer__common_name__icontains=q)
                )
        date_from = self.request.GET.get("date_from")
        if date_from:
            d = parse_date(date_from)
            if d:
                qs = qs.filter(date__gte=d)
        date_to = self.request.GET.get("date_to")
        if date_to:
            d = parse_date(date_to)
            if d:
                qs = qs.filter(date__lte=d)
        if self.request.user.has_perm("collections.view_milkcollectionpaymentstatus"):
            payment_status = (self.request.GET.get("payment_status") or "").strip().lower()
            if payment_status == "paid":
                qs = qs.filter(is_paid=True)
            elif payment_status == "unpaid":
                qs = qs.filter(is_paid=False)
        route_raw = (self.request.GET.get("route") or "").strip()
        if route_raw.isdigit():
            qs = qs.filter(route_id=int(route_raw))
        point_raw = (self.request.GET.get("collection_point") or "").strip()
        if point_raw.isdigit():
            point_id = int(point_raw)
            qs = qs.filter(
                Q(collection_point_id=point_id)
                | Q(farmer__collection_point_id=point_id)
            )
        farmer_raw = (self.request.GET.get("farmer") or "").strip()
        if farmer_raw.isdigit():
            qs = qs.filter(farmer_id=int(farmer_raw))
        return qs

    def _milk_list_base_qs(self):
        return self._milk_list_scope_qs(apply_search=True)

    def _route_point_totals_qs(self):
        """Collection point milk only — route totals sum POINT rows per date/route."""
        return self._milk_list_base_qs().filter(source=CollectionSource.POINT)

    def get_queryset(self):
        tab = self.request.GET.get("tab", "route")
        self.tab = tab
        base = self._milk_list_base_qs()
        if tab == "point":
            return base.filter(source=CollectionSource.POINT)
        if tab == "farmer":
            return base.filter(source=CollectionSource.FARMER)
        if tab == "reconcile":
            return MilkCollection.objects.none()
        return MilkCollection.objects.none()

    def _resolve_per_page(self):
        raw = (self.request.GET.get("per_page") or "").strip().lower()
        if raw == self.per_page_all:
            return self.per_page_all
        if raw.isdigit():
            size = int(raw)
            if size in self.page_size_choices:
                return size
        return self.paginate_by

    def get_paginate_by(self, queryset):
        tab = getattr(self, "tab", self.request.GET.get("tab", "route"))
        if tab in {"route", "reconcile"}:
            return None
        per = self._resolve_per_page()
        if per == self.per_page_all:
            return None
        return per

    @staticmethod
    def _pagination_query(request):
        q = request.GET.copy()
        q.pop("page", None)
        return q.urlencode()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["tab"] = getattr(self, "tab", self.request.GET.get("tab", "route"))
        allowed_qs = get_allowed_branches_qs(self.request.user)
        ctx["branches"] = allowed_qs if not self.request.user.is_superuser else Branch.objects.all()
        selected_branch = (self.request.GET.get("branch") or "").strip()
        if not selected_branch:
            legacy_branches = self.request.GET.getlist("branches")
            if len(legacy_branches) == 1 and str(legacy_branches[0]).isdigit():
                selected_branch = legacy_branches[0]
        ctx["selected_branch"] = selected_branch
        route_select_qs = Route.objects.all().order_by("code", "name")
        if not self.request.user.is_superuser:
            allowed_ids = list(allowed_qs.values_list("id", flat=True))
            route_select_qs = route_select_qs.filter(branch_id__in=allowed_ids)
        if selected_branch.isdigit():
            route_select_qs = route_select_qs.filter(branch_id=int(selected_branch))
        ctx["point_summary_routes"] = [
            {"id": r.pk, "label": f"{r.code} — {r.name}", "branch_id": r.branch_id}
            for r in route_select_qs
        ]
        ctx["filter_routes"] = [
            {"id": r.pk, "label": r.name, "branch_id": r.branch_id}
            for r in route_select_qs
        ]
        selected_route = (self.request.GET.get("route") or "").strip()
        selected_collection_point = (self.request.GET.get("collection_point") or "").strip()
        ctx["selected_route"] = selected_route
        ctx["selected_collection_point"] = selected_collection_point
        point_filter_qs = (
            CollectionPoint.objects.select_related("route", "route__branch")
            .order_by("route__name", "number", "name")
        )
        if not self.request.user.is_superuser:
            allowed_ids = list(allowed_qs.values_list("id", flat=True))
            point_filter_qs = point_filter_qs.filter(route__branch_id__in=allowed_ids)
        if selected_branch.isdigit():
            point_filter_qs = point_filter_qs.filter(route__branch_id=int(selected_branch))
        # Route → point narrowing is handled client-side in the list filters.
        ctx["filter_points"] = point_filter_qs
        branch_select_qs = allowed_qs.order_by("code", "name") if not self.request.user.is_superuser else Branch.objects.order_by("code", "name")
        ctx["summary_branches"] = [
            {"id": b.pk, "label": f"{b.code} - {b.name}"}
            for b in branch_select_qs
        ]
        if self.request.user.is_superuser:
            ctx["transfer_branches"] = Branch.objects.order_by("name")
            ctx["transfer_points"] = (
                CollectionPoint.objects.select_related("route", "route__branch")
                .order_by("route__branch__name", "route__code", "number")
            )
        if ctx["tab"] == "route":
            raw = (
                self._route_point_totals_qs()
                .values("date", "route_id", "route__code", "route__name", "branch__name")
                .annotate(total_kg=Sum("kg"), total_liters=Sum("liters"))
                .order_by("-date", "route__code")
            )
            ctx["route_rows"] = [
                {
                    "date": r["date"],
                    "route_id": r["route_id"],
                    "branch_name": r["branch__name"],
                    "route_name": r["route__name"],
                    "route_label": f"{r['route__code']} — {r['route__name']}",
                    "total_kg": r["total_kg"],
                    "total_liters": r["total_liters"],
                }
                for r in raw
            ]
            route_opts = {}
            for r in raw:
                rid = r["route_id"]
                if rid not in route_opts:
                    route_opts[rid] = f"{r['route__code']} — {r['route__name']}"
            ctx["route_options"] = [{"id": rid, "label": label} for rid, label in route_opts.items()]
            route_totals = kg_liters_totals(self._route_point_totals_qs())
            route_totals["count"] = len(ctx["route_rows"])
            ctx["route_table_totals"] = route_totals
            ctx["list_table_totals"] = None
            ctx["reconcile_rows"] = None
            ctx["reconcile_table_totals"] = None
        elif ctx["tab"] == "reconcile":
            status_filter = (self.request.GET.get("reconcile_status") or "").strip().lower()
            reconcile_rows, reconcile_totals = build_point_farmer_reconciliation_rows(
                self._milk_list_base_qs(),
                farmer_rollups_qs=self._milk_list_scope_qs(apply_search=False),
                status_filter=status_filter,
            )
            ctx["reconcile_rows"] = reconcile_rows
            ctx["reconcile_table_totals"] = reconcile_totals
            ctx["reconcile_status"] = status_filter
            ctx["route_rows"] = None
            ctx["route_table_totals"] = None
            ctx["route_options"] = []
            ctx["list_table_totals"] = None
        else:
            ctx["route_rows"] = None
            ctx["route_table_totals"] = None
            ctx["route_options"] = []
            if ctx["tab"] == "point":
                ctx["list_table_totals"] = kg_liters_totals(
                    self._milk_list_base_qs().filter(source=CollectionSource.POINT)
                )
            elif ctx["tab"] == "farmer":
                ctx["list_table_totals"] = kg_liters_totals(
                    self._milk_list_base_qs().filter(source=CollectionSource.FARMER)
                )
            else:
                ctx["list_table_totals"] = None
            ctx["reconcile_rows"] = None
            ctx["reconcile_table_totals"] = None
        ctx["can_sync_point_totals"] = self.request.user.has_perm(
            "collections.sync_milkcollectionreconcile"
        )
        ctx["can_set_reconcile_choice"] = self.request.user.has_perm(
            "collections.change_milkcollectionreconcilechoice"
        )
        ctx["can_view_reconcile"] = self.request.user.has_perm(
            "collections.view_milkcollectionreconcile"
        )
        ctx["point_summary_route"] = (self.request.GET.get("point_summary_route") or "").strip()
        ctx["point_summary_branch"] = (self.request.GET.get("branch") or "").strip()
        ctx["point_summary_from"] = (self.request.GET.get("point_summary_from") or self.request.GET.get("from") or "").strip()
        ctx["point_summary_to"] = (self.request.GET.get("point_summary_to") or self.request.GET.get("to") or "").strip()
        ctx["date_from"] = (self.request.GET.get("date_from") or "").strip()
        ctx["date_to"] = (self.request.GET.get("date_to") or "").strip()
        ctx["can_view_payment_status"] = self.request.user.has_perm(
            "collections.view_milkcollectionpaymentstatus"
        )
        ctx["can_view_collection_user"] = (
            self.request.user.is_superuser or self.request.user.is_staff
        )
        ctx["can_use_point_row_menu"] = ctx["can_view_collection_user"]
        point_trailing = 1
        if ctx["can_view_payment_status"]:
            point_trailing += 1
        if ctx["can_view_collection_user"]:
            point_trailing += 1
        ctx["point_list_label_colspan"] = 4 + (1 if self.request.user.is_superuser else 0)
        ctx["point_list_trailing_colspan"] = point_trailing
        ctx["point_table_col_count"] = (
            4 + 2 + 1 + (1 if self.request.user.is_superuser else 0)
            + (1 if ctx["can_view_payment_status"] else 0)
            + (1 if ctx["can_view_collection_user"] else 0)
        )
        ctx["trailing_footer_cols"] = 3 if ctx["can_view_payment_status"] else 2
        ctx["payment_status"] = (
            (self.request.GET.get("payment_status") or "").strip().lower()
            if ctx["can_view_payment_status"]
            else ""
        )
        filter_q = self.request.GET.copy()
        filter_q.pop("page", None)
        filter_q.pop("tab", None)
        ctx["milk_filter_query"] = filter_q.urlencode()
        if ctx["tab"] not in {"route", "reconcile"}:
            per = self._resolve_per_page()
            ctx["show_all_rows"] = per == self.per_page_all
            ctx["per_page"] = "All" if per == self.per_page_all else per
            ctx["per_page_choices"] = self.page_size_choices
            ctx["pagination_query"] = self._pagination_query(self.request)
            if ctx["show_all_rows"]:
                obj_list = ctx.get("object_list")
                ctx["list_total_count"] = len(obj_list) if obj_list is not None else 0
        return ctx

    def post(self, request, *args, **kwargs):
        if not request.user.has_perm("collections.delete_milkcollection"):
            messages.error(request, "You do not have permission to delete milk collections.")
            return redirect("milk-collection-list")

        action = (request.POST.get("bulk_action") or "").strip()
        redirect_query = (request.POST.get("redirect_query") or "").strip()
        tab = (request.POST.get("tab") or request.GET.get("tab") or "route").strip()

        if action not in {"delete_one", "delete_selected", "delete_all"}:
            messages.error(request, "Invalid bulk action.")
            return self._redirect_with_query(redirect_query, tab)

        base_qs = self._milk_list_base_qs()

        if action == "delete_all":
            if not request.user.is_superuser:
                messages.error(request, "Only superuser can delete all records.")
                return self._redirect_with_query(redirect_query, tab)
            target_qs = base_qs
        elif action == "delete_one":
            one_id = request.POST.get("one_id")
            target_qs = base_qs.filter(pk=one_id) if str(one_id or "").isdigit() else base_qs.none()
        else:
            selected_ids = [int(x) for x in request.POST.getlist("selected_ids") if str(x).isdigit()]
            target_qs = base_qs.filter(pk__in=selected_ids) if selected_ids else base_qs.none()

        deleted_count = target_qs.count()
        if deleted_count == 0:
            messages.warning(request, "No matching milk collections selected.")
            return self._redirect_with_query(redirect_query, tab)

        target_qs.delete()
        messages.success(request, f"Deleted {deleted_count} milk collection record(s).")
        return self._redirect_with_query(redirect_query, tab)

    def _redirect_with_query(self, redirect_query, tab):
        if redirect_query:
            return redirect(f"{reverse('milk-collection-list')}?{redirect_query}")
        return redirect(f"{reverse('milk-collection-list')}?tab={tab}")


class MilkCollectionPointTransferView(LoginRequiredMixin, View):
    """Superuser: move selected POINT milk collections to another collection point."""

    def post(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied

        redirect_query = (request.POST.get("redirect_query") or "").strip()
        tab = (request.POST.get("tab") or "point").strip()
        target_raw = (request.POST.get("target_collection_point") or "").strip()
        selected_ids = [int(x) for x in request.POST.getlist("selected_ids") if str(x).isdigit()]

        if not target_raw.isdigit():
            messages.error(request, "Select a destination collection point.")
            return self._redirect(redirect_query, tab)

        try:
            result = transfer_point_collections(
                user=request.user,
                record_ids=selected_ids,
                target_point_id=int(target_raw),
            )
        except CollectionPoint.DoesNotExist:
            messages.error(request, "Destination collection point not found.")
            return self._redirect(redirect_query, tab)
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
            return self._redirect(redirect_query, tab)

        msg = f"Transferred {result['moved']} record(s) to {result['target_label']}."
        if result["merged"]:
            msg += f" {result['merged']} merged with existing row(s) at the destination."
        if result["skipped_paid"]:
            msg += f" {result['skipped_paid']} paid row(s) skipped."
        messages.success(request, msg)
        return self._redirect(redirect_query, tab)

    def _redirect(self, redirect_query, tab):
        if redirect_query:
            return redirect(f"{reverse('milk-collection-list')}?{redirect_query}")
        return redirect(f"{reverse('milk-collection-list')}?tab={tab}")


class MilkCollectionPointReconcileSyncView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Set the collection point row kg to the sum of linked farmer collections for that date/route."""

    permission_required = "collections.sync_milkcollectionreconcile"

    def post(self, request, *args, **kwargs):
        date_raw = (request.POST.get("date") or "").strip()
        route_id = request.POST.get("route")
        point_id = request.POST.get("collection_point")
        redirect_query = (request.POST.get("redirect_query") or "").strip()

        d = parse_date(date_raw)
        if not d or not str(route_id or "").isdigit() or not str(point_id or "").isdigit():
            messages.error(request, "Invalid reconcile sync request.")
            return _reconcile_return_redirect(request, redirect_query)

        route = get_object_or_404(Route, pk=int(route_id))
        point = get_object_or_404(CollectionPoint, pk=int(point_id), route=route)
        if not request.user.is_superuser:
            allowed_ids = list(get_allowed_branches_qs(request.user).values_list("id", flat=True))
            if route.branch_id not in allowed_ids:
                messages.error(request, "No access to this branch.")
                return _reconcile_return_redirect(request, redirect_query)

        sync_collection_point_total_from_farmers(
            d, route.pk, point.pk, request.user, save_snapshot=True
        )
        messages.success(
            request,
            f"Point total updated from farmer collections for {point.number} — {point.name} on {d}.",
        )
        return _reconcile_return_redirect(request, redirect_query)

    def _redirect(self, redirect_query):
        return _reconcile_return_redirect(self.request, redirect_query)


class MilkCollectionPointReconcileReverseSyncView(LoginRequiredMixin, View):
    """Restore the point kg saved before the last Sync (superuser only)."""

    def post(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            messages.error(request, "Only superusers can reverse a sync.")
            return _reconcile_return_redirect(request, (request.POST.get("redirect_query") or "").strip())

        date_raw = (request.POST.get("date") or "").strip()
        route_id = request.POST.get("route")
        point_id = request.POST.get("collection_point")
        redirect_query = (request.POST.get("redirect_query") or "").strip()

        d = parse_date(date_raw)
        if not d or not str(route_id or "").isdigit() or not str(point_id or "").isdigit():
            messages.error(request, "Invalid reverse sync request.")
            return _reconcile_return_redirect(request, redirect_query)

        route = get_object_or_404(Route, pk=int(route_id))
        point = get_object_or_404(CollectionPoint, pk=int(point_id), route=route)
        restored = reverse_collection_point_sync_from_snapshot(d, route.pk, point.pk, request.user)
        if restored is None:
            messages.warning(request, "No sync snapshot to reverse for this day.")
        else:
            messages.success(
                request,
                f"Sync reversed for {point.number} — {point.name} on {d} "
                f"(point kg restored to {restored}).",
            )
        return _reconcile_return_redirect(request, redirect_query)


class MilkCollectionReconcileChoiceView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Save point vs farmer choice for payment reconcile on a given day."""

    permission_required = "collections.change_milkcollectionreconcilechoice"

    def post(self, request, *args, **kwargs):
        date_raw = (request.POST.get("date") or "").strip()
        route_id = request.POST.get("route")
        point_id = request.POST.get("collection_point")
        source = (request.POST.get("source") or "").strip().lower()
        redirect_query = (request.POST.get("redirect_query") or "").strip()
        wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        valid_sources = {
            CollectionPointMilkReconcileChoice.Source.POINT,
            CollectionPointMilkReconcileChoice.Source.FARMER,
        }
        d = parse_date(date_raw)
        if (
            not d
            or not str(route_id or "").isdigit()
            or not str(point_id or "").isdigit()
            or source not in valid_sources
        ):
            if wants_json:
                return JsonResponse({"ok": False, "error": "Invalid reconcile choice."}, status=400)
            messages.error(request, "Invalid reconcile choice.")
            return _reconcile_return_redirect(request, redirect_query)

        route = get_object_or_404(Route, pk=int(route_id))
        point = get_object_or_404(CollectionPoint, pk=int(point_id), route=route)
        if not request.user.is_superuser:
            allowed_ids = list(get_allowed_branches_qs(request.user).values_list("id", flat=True))
            if route.branch_id not in allowed_ids:
                if wants_json:
                    return JsonResponse({"ok": False, "error": "No access to this branch."}, status=403)
                messages.error(request, "No access to this branch.")
                return _reconcile_return_redirect(request, redirect_query)

        set_reconcile_choice(
            collection_point=point,
            route=route,
            date=d,
            source=source,
            updated_by=request.user,
        )
        if wants_json:
            from .point_reconciliation import build_point_farmer_reconciliation_rows

            base_qs = MilkCollection.objects.filter(branch_id=route.branch_id)
            rows, _totals = build_point_farmer_reconciliation_rows(base_qs.filter(date=d))
            row = next(
                (
                    r
                    for r in rows
                    if r["collection_point_id"] == point.pk
                    and r["route_id"] == route.pk
                    and r["date"] == d
                ),
                None,
            )
            if row:
                row = {
                    **row,
                    "date": row["date"].isoformat(),
                    "point_kg": str(row["point_kg"]),
                    "point_liters": str(row["point_liters"]),
                    "farmer_kg": str(row["farmer_kg"]),
                    "farmer_liters": str(row["farmer_liters"]),
                    "diff_kg": str(row["diff_kg"]),
                    "diff_liters": str(row["diff_liters"]),
                    "reconcile_kg": str(row["reconcile_kg"]),
                }
            return JsonResponse({"ok": True, "row": row})
        messages.success(request, f"Payment reconcile set to {source} for {point.number} — {point.name}.")
        return _reconcile_return_redirect(request, redirect_query)

    def _redirect(self, redirect_query):
        return _reconcile_return_redirect(self.request, redirect_query)


class MilkCollectionRouteReceiptPrintView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """
    Printable route + date: either the day summary table (kind=summary) or per-point Sinhala slips (kind=receipts).
    Optional fat/SNF/other from MilkFactor when recorded for that point and date.
    """

    permission_required = "collections.view_milkcollection"

    def get(self, request, *args, **kwargs):
        date_raw = request.GET.get("date")
        route_id = request.GET.get("route")
        kind = (request.GET.get("kind") or "summary").strip().lower()
        if kind not in ("summary", "receipts"):
            return HttpResponseBadRequest("kind must be summary or receipts.")
        goods_flag = (request.GET.get("include_goods") or request.GET.get("goods") or "").strip().lower()
        include_goods = goods_flag in ("1", "true", "yes", "on")
        if include_goods and kind != "receipts":
            include_goods = False
        if not date_raw or not route_id:
            return HttpResponseBadRequest("Provide date (YYYY-MM-DD) and route (id) as query parameters.")
        d = parse_date(date_raw)
        if not d:
            return HttpResponseBadRequest("Invalid date.")
        try:
            route_pk = int(route_id)
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Invalid route id.")

        route = get_object_or_404(Route, pk=route_pk)
        if not self.request.user.is_superuser:
            allowed_ids = list(get_allowed_branches_qs(self.request.user).values_list("id", flat=True))
            if route.branch_id not in allowed_ids:
                return HttpResponseBadRequest("No access to selected branch.")
        point_rows = (
            MilkCollection.objects.filter(
                date=d,
                route=route,
                source=CollectionSource.POINT,
            )
            .select_related("collection_point")
            .order_by("collection_point__number", "collection_point__name", "pk")
        )
        factors = {
            f.collection_point_id: f
            for f in MilkFactor.objects.filter(date=d, route=route)
        }

        lines = []
        total_kg = Decimal("0")
        total_liters = Decimal("0")
        for mc in point_rows:
            fac = factors.get(mc.collection_point_id)
            cp = mc.collection_point
            row = {
                "point_id": mc.collection_point_id,
                "point_number": str(cp.number).strip(),
                "point_name": (cp.name or "").strip(),
                "point_label": str(cp),
                "liters": mc.liters,
                "kg": mc.kg,
                "fat": fac.fat if fac else None,
                "snf": fac.snf if fac else None,
                "price": None,
                "other": fac.kq if fac else None,
            }
            if include_goods:
                row["issue_receipt_sections"] = _build_issue_receipt_sections(cp, d)
            lines.append(row)
            total_kg += mc.kg
            total_liters += mc.liters

        receipt_no = f"{route.code}-{d.strftime('%Y%m%d')}"
        ctx = {
            "route": route,
            "collection_date": d,
            "receipt_no": receipt_no,
            "lines": lines,
            "total_kg": total_kg,
            "total_liters": total_liters,
            "printed_at": timezone.now(),
            "receipt_kind": kind,
            "include_goods": include_goods,
        }
        return render(request, "collections/milk_collection_route_receipt.html", ctx)


class MilkCollectionRoutePeriodReceiptView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "collections.view_milkcollection"

    def get(self, request, *args, **kwargs):
        route_id = request.GET.get("route")
        from_raw = request.GET.get("from")
        to_raw = request.GET.get("to")
        if not route_id or not from_raw or not to_raw:
            return HttpResponseBadRequest("Provide route, from and to query parameters.")
        from_date = parse_date(from_raw)
        to_date = parse_date(to_raw)
        if not from_date or not to_date:
            return HttpResponseBadRequest("Invalid date range.")
        if from_date > to_date:
            return HttpResponseBadRequest("From date must be on or before To date.")
        try:
            route_pk = int(route_id)
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Invalid route id.")

        route = get_object_or_404(Route, pk=route_pk)
        if not self.request.user.is_superuser:
            allowed_ids = list(get_allowed_branches_qs(self.request.user).values_list("id", flat=True))
            if route.branch_id not in allowed_ids:
                return HttpResponseBadRequest("No access to selected branch.")

        base_qs = MilkCollection.objects.filter(
            route=route,
            source=CollectionSource.POINT,
            date__gte=from_date,
            date__lte=to_date,
        )
        rows = (
            base_qs
            .values("date")
            .annotate(total_entries=Count("id"), total_kg=Sum("kg"), total_liters=Sum("liters"))
            .order_by("date")
        )
        summary = base_qs.aggregate(
            total_days=Count("date", distinct=True),
            total_entries=Count("id"),
            total_kg=Sum("kg"),
            total_liters=Sum("liters"),
        )
        return render(
            request,
            "collections/milk_collection_route_period_receipt.html",
            {
                "route": route,
                "rows": rows,
                "from_date": from_date,
                "to_date": to_date,
                "total_days": summary["total_days"] or 0,
                "total_entries": summary["total_entries"] or 0,
                "total_kg": summary["total_kg"] or Decimal("0.00"),
                "total_liters": summary["total_liters"] or Decimal("0.00"),
            },
        )


class MilkCollectionPointSummaryPrintView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """
    Printable route-period summary grouped by collection point.
    Shows day-wise rows under each point and a total per point.
    """

    permission_required = "collections.view_milkcollection"

    def get(self, request, *args, **kwargs):
        route_id = request.GET.get("route")
        from_raw = request.GET.get("from")
        to_raw = request.GET.get("to")
        if not route_id or not from_raw or not to_raw:
            return HttpResponseBadRequest("Provide route, from and to query parameters.")
        from_date = parse_date(from_raw)
        to_date = parse_date(to_raw)
        if not from_date or not to_date:
            return HttpResponseBadRequest("Invalid date range.")
        if from_date > to_date:
            return HttpResponseBadRequest("From date must be on or before To date.")
        try:
            route_pk = int(route_id)
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Invalid route id.")

        route = get_object_or_404(Route, pk=route_pk)
        if not self.request.user.is_superuser:
            allowed_ids = list(get_allowed_branches_qs(self.request.user).values_list("id", flat=True))
            if route.branch_id not in allowed_ids:
                return HttpResponseBadRequest("No access to selected branch.")

        rows = (
            MilkCollection.objects.filter(
                route=route,
                source=CollectionSource.POINT,
                date__gte=from_date,
                date__lte=to_date,
            )
            .select_related("collection_point")
            .order_by("collection_point__number", "collection_point__name", "date", "pk")
        )

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

        overall_total = rows.aggregate(total_kg=Sum("kg"), total_liters=Sum("liters"), total_entries=Count("id"))

        return render(
            request,
            "collections/milk_collection_point_summary_receipt.html",
            {
                "route": route,
                "from_date": from_date,
                "to_date": to_date,
                "grouped_points": grouped_points,
                "total_entries": overall_total["total_entries"] or 0,
                "total_kg": overall_total["total_kg"] or Decimal("0.00"),
                "total_liters": overall_total["total_liters"] or Decimal("0.00"),
                "printed_at": timezone.now(),
            },
        )


class MilkCollectionRouteSummaryPrintView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Printable landscape matrix: points as rows, dates as columns, totals."""

    permission_required = "collections.view_milkcollection"

    def get(self, request, *args, **kwargs):
        route_id = request.GET.get("route")
        from_raw = request.GET.get("from")
        to_raw = request.GET.get("to")
        if not route_id or not from_raw or not to_raw:
            return HttpResponseBadRequest("Provide route, from and to query parameters.")
        from_date = parse_date(from_raw)
        to_date = parse_date(to_raw)
        if not from_date or not to_date:
            return HttpResponseBadRequest("Invalid date range.")
        if from_date > to_date:
            return HttpResponseBadRequest("From date must be on or before To date.")
        try:
            route_pk = int(route_id)
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Invalid route id.")

        route = get_object_or_404(Route, pk=route_pk)
        if not self.request.user.is_superuser:
            allowed_ids = list(get_allowed_branches_qs(self.request.user).values_list("id", flat=True))
            if route.branch_id not in allowed_ids:
                return HttpResponseBadRequest("No access to selected branch.")

        date_list = []
        current = from_date
        while current <= to_date:
            date_list.append(current)
            current += timedelta(days=1)

        rows = (
            MilkCollection.objects.filter(
                route=route,
                source=CollectionSource.POINT,
                date__gte=from_date,
                date__lte=to_date,
            )
            .select_related("collection_point")
            .order_by("collection_point__number", "collection_point__name", "date", "pk")
        )

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

        return render(
            request,
            "collections/milk_collection_route_summary_receipt.html",
            {
                "route": route,
                "from_date": from_date,
                "to_date": to_date,
                "date_list": date_list,
                "point_rows": point_rows,
                "day_totals": day_totals,
                "grand_total": grand_total,
                "printed_at": timezone.now(),
            },
        )


class MilkCollectionBranchSummaryPrintView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Printable landscape matrix: routes as rows, dates as columns, for one branch."""

    permission_required = "collections.view_milkcollection"

    def get(self, request, *args, **kwargs):
        branch_id = request.GET.get("branch")
        from_raw = request.GET.get("from")
        to_raw = request.GET.get("to")
        if not branch_id or not from_raw or not to_raw:
            return HttpResponseBadRequest("Provide branch, from and to query parameters.")
        from_date = parse_date(from_raw)
        to_date = parse_date(to_raw)
        if not from_date or not to_date:
            return HttpResponseBadRequest("Invalid date range.")
        if from_date > to_date:
            return HttpResponseBadRequest("From date must be on or before To date.")
        try:
            branch_pk = int(branch_id)
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Invalid branch id.")

        branch = get_object_or_404(Branch, pk=branch_pk)
        if not request.user.is_superuser:
            allowed_ids = list(get_allowed_branches_qs(request.user).values_list("id", flat=True))
            if branch.pk not in allowed_ids:
                return HttpResponseBadRequest("No access to selected branch.")

        date_list = []
        current = from_date
        while current <= to_date:
            date_list.append(current)
            current += timedelta(days=1)

        rows = (
            MilkCollection.objects.filter(
                route__branch=branch,
                source=CollectionSource.POINT,
                date__gte=from_date,
                date__lte=to_date,
            )
            .select_related("route")
            .order_by("route__code", "route__name", "date", "pk")
        )

        route_map = {}
        for row in rows:
            rid = row.route_id
            if rid not in route_map:
                route = row.route
                route_map[rid] = {
                    "route_code": route.code,
                    "route_name": route.name,
                    "sort_key": (route.code or "", route.name or ""),
                    "day_map": {},
                    "row_total": Decimal("0.00"),
                }
            curr = route_map[rid]["day_map"].get(row.date, Decimal("0.00"))
            route_map[rid]["day_map"][row.date] = curr + row.kg
            route_map[rid]["row_total"] += row.kg

        route_rows = []
        for item in sorted(route_map.values(), key=lambda x: x["sort_key"]):
            values = [item["day_map"].get(day, Decimal("0.00")) for day in date_list]
            route_rows.append(
                {
                    "route_code": item["route_code"],
                    "route_name": item["route_name"],
                    "values": values,
                    "row_total": item["row_total"],
                }
            )

        raw_rows_qs = (
            RawMilkSupplierCollection.objects.filter(
                branch=branch,
                date__gte=from_date,
                date__lte=to_date,
            )
            .exclude(status=RawMilkSupplierCollection.CollectionStatus.CANCELLED)
            .select_related("supplier")
            .order_by("supplier__name", "date", "pk")
        )

        bulk_map = {}
        for row in raw_rows_qs:
            sid = row.supplier_id
            if sid not in bulk_map:
                bulk_map[sid] = {
                    "supplier_name": row.supplier.name,
                    "sort_key": row.supplier.name or "",
                    "day_map": {},
                    "row_total": Decimal("0.00"),
                }
            curr = bulk_map[sid]["day_map"].get(row.date, Decimal("0.00"))
            bulk_map[sid]["day_map"][row.date] = curr + row.kg
            bulk_map[sid]["row_total"] += row.kg

        bulk_rows = []
        for supplier in filter_m2m_by_branch(
            Supplier.objects.filter(category=Supplier.Category.RAW_MILK_SUPPLIER),
            branch.pk,
        ).order_by("name"):
            item = bulk_map.get(supplier.pk)
            if item:
                values = [item["day_map"].get(day, Decimal("0.00")) for day in date_list]
                row_total = item["row_total"]
            else:
                values = [Decimal("0.00") for _ in date_list]
                row_total = Decimal("0.00")
            bulk_rows.append(
                {
                    "supplier_name": supplier.name,
                    "values": values,
                    "row_total": row_total,
                }
            )

        day_totals = []
        grand_total = Decimal("0.00")
        for day in date_list:
            day_total = Decimal("0.00")
            for item in route_map.values():
                day_total += item["day_map"].get(day, Decimal("0.00"))
            for item in bulk_map.values():
                day_total += item["day_map"].get(day, Decimal("0.00"))
            day_totals.append(day_total)
            grand_total += day_total

        return render(
            request,
            "collections/milk_collection_branch_summary_receipt.html",
            {
                "branch": branch,
                "from_date": from_date,
                "to_date": to_date,
                "date_list": date_list,
                "route_rows": route_rows,
                "bulk_rows": bulk_rows,
                "day_totals": day_totals,
                "grand_total": grand_total,
                "printed_at": timezone.now(),
            },
        )


def _quantize_kg(value):
    return (value or Decimal("0")).quantize(Decimal("0.01"))


def _kg_to_liters(kg):
    return (kg * MILK_LITER_FACTOR).quantize(Decimal("0.01"))


def _branch_balance_kg(branch, through_date, *, inclusive=True):
    """Milk stock balance in kg at end of through_date (or start if inclusive=False)."""
    stock = BranchMilkStock.objects.filter(branch=branch).only("opening_kg").first()
    opening = stock.opening_kg if stock else Decimal("0")
    date_kw = "date__lte" if inclusive else "date__lt"

    def _sum_kg(qs, field="kg"):
        return qs.aggregate(total=Sum(field)).get("total") or Decimal("0")

    collected = _sum_kg(
        MilkCollection.objects.route_collections().filter(branch=branch, **{date_kw: through_date})
    )
    raw_collected = _sum_kg(
        RawMilkSupplierCollection.objects.filter(branch=branch, **{date_kw: through_date}).exclude(
            status=RawMilkSupplierCollection.CollectionStatus.CANCELLED
        )
    )
    dispatched = _sum_kg(
        MilkDistribution.objects.filter(branch=branch, **{date_kw: through_date}).exclude(
            status=MilkDistribution.DistributionStatus.CANCELLED
        )
    )
    returns_received = _sum_kg(
        MilkDistribution.objects.filter(
            returned_branch=branch,
            status=MilkDistribution.DistributionStatus.RETURN,
            **{date_kw: through_date},
        ),
        "buyer_result_quantity",
    )
    branch_transfers_in = _sum_kg(
        MilkDistribution.objects.filter(destination_branch=branch, **{date_kw: through_date}).exclude(
            status=MilkDistribution.DistributionStatus.CANCELLED
        )
    )
    adj_qs = BranchMilkStockAdjustment.objects.filter(branch=branch, **{date_kw: through_date})
    shortage = _sum_kg(
        adj_qs.filter(adjustment_type=BranchMilkStockAdjustment.AdjustmentType.SHORTAGE)
    )
    excess = _sum_kg(
        adj_qs.filter(adjustment_type=BranchMilkStockAdjustment.AdjustmentType.EXCESS)
    )
    net_adjustment = excess - shortage
    total = opening + collected + raw_collected - dispatched + returns_received + branch_transfers_in + net_adjustment
    return _quantize_kg(total)


class MilkCollectionDaySummaryPrintView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Printable branch day summary: route collections, buyer dispatches, and balances."""

    permission_required = "collections.view_milkcollection"

    def get(self, request, *args, **kwargs):
        branch_id = request.GET.get("branch")
        date_raw = request.GET.get("from") or request.GET.get("date")
        if not branch_id or not date_raw:
            return HttpResponseBadRequest("Provide branch and from (date) query parameters.")
        report_date = parse_date(date_raw)
        if not report_date:
            return HttpResponseBadRequest("Invalid date.")
        try:
            branch_pk = int(branch_id)
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Invalid branch id.")

        branch = get_object_or_404(Branch, pk=branch_pk)
        if not request.user.is_superuser:
            allowed_ids = list(get_allowed_branches_qs(request.user).values_list("id", flat=True))
            if branch.pk not in allowed_ids:
                return HttpResponseBadRequest("No access to selected branch.")

        coll_by_route = {
            row["route_id"]: row
            for row in MilkCollection.objects.route_collections()
            .filter(branch=branch, date=report_date)
            .values("route_id")
            .annotate(total_kg=Sum("kg"), total_liters=Sum("liters"))
        }

        route_rows = []
        collection_total_kg = Decimal("0.00")
        collection_total_liters = Decimal("0.00")
        for route_num, route in enumerate(
            Route.objects.filter(branch=branch).order_by("code", "name"),
            start=1,
        ):
            data = coll_by_route.get(route.pk, {})
            kg = _quantize_kg(data.get("total_kg"))
            liters = _quantize_kg(data.get("total_liters")) if data.get("total_liters") is not None else _kg_to_liters(kg)
            route_rows.append(
                {
                    "route_num": route_num,
                    "route_name": route.name,
                    "kg": kg,
                    "liters": liters,
                }
            )
            collection_total_kg += kg
            collection_total_liters += liters

        bulk_by_supplier = {
            row["supplier_id"]: row
            for row in RawMilkSupplierCollection.objects.filter(branch=branch, date=report_date)
            .exclude(status=RawMilkSupplierCollection.CollectionStatus.CANCELLED)
            .values("supplier_id")
            .annotate(total_kg=Sum("kg"), total_liters=Sum("liters"))
        }
        bulk_rows = []
        bulk_total_kg = Decimal("0.00")
        bulk_total_liters = Decimal("0.00")
        for supplier in (
            filter_m2m_by_branch(
                Supplier.objects.filter(category=Supplier.Category.RAW_MILK_SUPPLIER),
                branch.pk,
            ).order_by("name")
        ):
            data = bulk_by_supplier.get(supplier.pk, {})
            kg = _quantize_kg(data.get("total_kg"))
            liters = _quantize_kg(data.get("total_liters")) if data.get("total_liters") is not None else _kg_to_liters(kg)
            bulk_rows.append({"name": supplier.name, "kg": kg, "liters": liters})
            bulk_total_kg += kg
            bulk_total_liters += liters

        buyer_dispatch_map = defaultdict(lambda: {"kg": Decimal("0.00"), "liters": Decimal("0.00")})
        transfer_rows = []
        dispatches = (
            MilkDistribution.objects.filter(branch=branch, date=report_date)
            .exclude(status=MilkDistribution.DistributionStatus.CANCELLED)
            .select_related("buyer", "destination_branch")
        )
        for dispatch in dispatches:
            kg = _quantize_kg(dispatch.kg)
            liters = _quantize_kg(dispatch.liters_equivalent)
            if dispatch.buyer_id:
                buyer_dispatch_map[dispatch.buyer_id]["kg"] += kg
                buyer_dispatch_map[dispatch.buyer_id]["liters"] += liters
            elif dispatch.destination_branch_id:
                dest = dispatch.destination_branch
                transfer_rows.append(
                    {
                        "name": f"→ {dest.code} — {dest.name}",
                        "kg": kg,
                        "liters": liters,
                    }
                )

        buyer_rows = []
        dispatch_total_kg = Decimal("0.00")
        dispatch_total_liters = Decimal("0.00")
        for buyer in filter_m2m_by_branch(Buyer.objects.all(), branch.pk).order_by("name"):
            totals = buyer_dispatch_map.get(buyer.pk, {"kg": Decimal("0.00"), "liters": Decimal("0.00")})
            kg = _quantize_kg(totals["kg"])
            liters = _quantize_kg(totals["liters"]) if totals["liters"] else _kg_to_liters(kg)
            buyer_rows.append({"name": buyer.name, "kg": kg, "liters": liters})
            dispatch_total_kg += kg
            dispatch_total_liters += liters

        for row in transfer_rows:
            buyer_rows.append(row)
            dispatch_total_kg += row["kg"]
            dispatch_total_liters += row["liters"]

        balance_cf = _branch_balance_kg(branch, report_date, inclusive=False)
        balance_bf = _branch_balance_kg(branch, report_date, inclusive=True)
        balance_today_kg = balance_bf - balance_cf
        balance_today_liters = _kg_to_liters(balance_today_kg)

        return render(
            request,
            "collections/milk_collection_day_summary_receipt.html",
            {
                "branch": branch,
                "report_date": report_date,
                "route_rows": route_rows,
                "collection_total_kg": collection_total_kg,
                "collection_total_liters": collection_total_liters,
                "bulk_rows": bulk_rows,
                "bulk_total_kg": bulk_total_kg,
                "bulk_total_liters": bulk_total_liters,
                "dispatch_rows": buyer_rows,
                "dispatch_total_kg": dispatch_total_kg,
                "dispatch_total_liters": dispatch_total_liters,
                "balance_cf_kg": balance_cf,
                "balance_cf_liters": _kg_to_liters(balance_cf),
                "balance_today_kg": balance_today_kg,
                "balance_today_liters": balance_today_liters,
                "balance_bf_kg": balance_bf,
                "balance_bf_liters": _kg_to_liters(balance_bf),
                "printed_at": timezone.now(),
            },
        )


def _farmer_goods_issues_for_point_receipt(collection_point, collection_date):
    """
    Issues to this center on the same *local* calendar day as the milk receipt.
    Matches issue_to_id to the collection point PK, and (for legacy rows) the numeric
    point number if it is a base-10 integer.
    """
    tz = timezone.get_current_timezone()
    day_start = timezone.make_aware(datetime.combine(collection_date, datetime.min.time()), tz)
    day_end = day_start + timedelta(days=1)
    id_candidates = {collection_point.pk}
    try:
        id_candidates.add(int(str(collection_point.number).strip()))
    except (ValueError, TypeError):
        pass
    return (
        FarmerGoodsIssue.objects.filter(
            issue_to_type=FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
            issue_to_id__in=id_candidates,
            date__gte=day_start,
            date__lt=day_end,
            driver_status=FarmerGoodsIssue.DriverStatus.ACCEPTED,
        )
        .exclude(Q(batch_ref="") | Q(batch_ref__isnull=True))
        .select_related("product")
        .order_by("batch_ref", "id")
    )


def _build_issue_receipt_sections(collection_point, collection_date):
    """Group farmer goods issues for a center into print sections (by batch_ref)."""
    issue_receipt_sections = []
    goods_issues = _farmer_goods_issues_for_point_receipt(collection_point, collection_date)
    line_price_map = fifo_price_map_for_issues(goods_issues)
    by_ref = {}
    for iss in goods_issues:
        by_ref.setdefault(iss.batch_ref, []).append(iss)
    for ref, rows in by_ref.items():
        lines = [
            {
                "name": x.product.name,
                "qty": x.quantity,
                "price": line_price_map.get(x.id, Decimal("0")),
                "price_display": format_money(line_price_map.get(x.id, Decimal("0")) or Decimal("0"), empty="0.00"),
            }
            for x in rows
        ]
        total_price = sum((line["price"] for line in lines), Decimal("0"))
        issue_receipt_sections.append(
            {
                "batch_ref": ref,
                "ref_display": str(ref).strip()[:8].upper(),
                "lines": lines,
                "total_price": total_price,
                "total_price_display": format_money(total_price or Decimal("0"), empty="0.00"),
            }
        )
    return issue_receipt_sections


class MilkCollectionPointReceiptPrintView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Single collection-point receipt (Sinhala 5-column slip) for one date and point."""

    permission_required = "collections.view_milkcollection"

    def get(self, request, *args, **kwargs):
        date_raw = request.GET.get("date")
        point_raw = request.GET.get("point")
        if not date_raw or not point_raw:
            return HttpResponseBadRequest("Provide date (YYYY-MM-DD) and point (collection point id).")
        d = parse_date(date_raw)
        if not d:
            return HttpResponseBadRequest("Invalid date.")
        try:
            point_pk = int(point_raw)
        except (TypeError, ValueError):
            return HttpResponseBadRequest("Invalid point id.")

        cp = get_object_or_404(CollectionPoint.objects.select_related("route"), pk=point_pk)
        if not self.request.user.is_superuser:
            allowed_ids = list(get_allowed_branches_qs(self.request.user).values_list("id", flat=True))
            if cp.route.branch_id not in allowed_ids:
                return HttpResponseBadRequest("No access to selected branch.")
        mc = (
            MilkCollection.objects.filter(
                date=d,
                route_id=cp.route_id,
                collection_point=cp,
                source=CollectionSource.POINT,
            )
            .select_related("collection_point", "route")
            .first()
        )
        if not mc:
            return HttpResponseBadRequest("No collection point milk entry for this date and center.")

        fac = (
            MilkFactor.objects.filter(date=d, route_id=cp.route_id, collection_point=cp).first()
        )
        line = {
            "point_id": cp.pk,
            "point_number": str(cp.number).strip(),
            "point_name": (cp.name or "").strip(),
            "point_label": str(cp),
            "liters": mc.liters,
            "kg": mc.kg,
            "fat": fac.fat if fac else None,
            "snf": fac.snf if fac else None,
            "price": None,
            "other": fac.kq if fac else None,
        }
        issue_receipt_sections = _build_issue_receipt_sections(cp, d)
        ctx = {
            "line": line,
            "collection_date": d,
            "route": cp.route,
            "printed_at": timezone.now(),
            "issue_receipt_sections": issue_receipt_sections,
        }
        return render(request, "collections/milk_collection_point_receipt.html", ctx)


class MilkCollectionCreateView(
    LoginRequiredMixin,
    PermissionRequiredMixin,
    ModelFormPageTitleMixin,
    MilkCollectionFormContextMixin,
    CreateView,
):
    permission_required = 'collections.add_milkcollection'
    model = MilkCollection
    form_class = MilkCollectionForm
    template_name = 'collections/milk_collection_form.html'

    def get_success_url(self):
        return f"{reverse('milk-collection-add')}?next=1"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        inst = form.instance
        cd = form.cleaned_data
        is_ajax = self.request.headers.get("X-Requested-With") == "XMLHttpRequest"

        if not inst.pk and cd.get("source") == CollectionSource.POINT and cd.get("collection_point"):
            existing = _existing_point_row(cd["date"], cd["route"], cd["collection_point"])
            if existing:
                if is_ajax:
                    return _conflict_json(existing, cd["kg"], "point")
                form.add_error(
                    None,
                    ValidationError(
                        "This collection point already has an entry for this date. "
                        "Save again from this screen (with JavaScript enabled) to add or subtract kg, "
                        "or use Edit on the list.",
                    ),
                )
                return self.form_invalid(form)

        if not inst.pk and cd.get("source") == CollectionSource.FARMER and cd.get("farmer") and cd.get("route"):
            existing = _existing_farmer_row(cd["date"], cd["route"], cd["farmer"])
            if existing:
                if is_ajax:
                    return _conflict_json(existing, cd["kg"], "farmer")
                form.add_error(
                    None,
                    ValidationError(
                        "This farmer already has a collection entry for this date and route. "
                        "Save again from this screen (with JavaScript enabled) to add or subtract kg, "
                        "or use Edit on the list.",
                    ),
                )
                return self.form_invalid(form)

        try:
            if is_ajax:
                with transaction.atomic():
                    self.object = form.save()
                return JsonResponse(
                    {
                        "ok": True,
                        "last_user_collection": _last_user_collection_payload(self.request.user),
                    }
                )
            with transaction.atomic():
                response = super().form_valid(form)
            return response
        except ValidationError as e:
            if _is_point_uniqueness_violation(e):
                existing = _existing_point_row(
                    cd.get("date"),
                    cd.get("route"),
                    cd.get("collection_point"),
                )
                if existing and is_ajax:
                    return _conflict_json(existing, cd.get("kg"), "point")
                if existing:
                    form.add_error(
                        None,
                        ValidationError(
                            "This collection point already has an entry for this date. "
                            "Choose add or subtract when prompted (JavaScript), or edit the existing row."
                        ),
                    )
                    return self.form_invalid(form)
            elif _is_farmer_uniqueness_violation(e):
                existing = _existing_farmer_row(
                    cd.get("date"),
                    cd.get("route"),
                    cd.get("farmer"),
                )
                if existing and is_ajax:
                    return _conflict_json(existing, cd.get("kg"), "farmer")
                if existing:
                    form.add_error(
                        None,
                        ValidationError(
                            "This farmer already has a collection entry for this date and route. "
                            "Choose add or subtract when prompted (JavaScript), or edit the existing row."
                        ),
                    )
                    return self.form_invalid(form)
            raise
        except IntegrityError:
            if cd.get("source") == CollectionSource.POINT:
                existing = _existing_point_row(
                    cd.get("date"),
                    cd.get("route"),
                    cd.get("collection_point"),
                )
                if existing and is_ajax:
                    return _conflict_json(existing, cd.get("kg"), "point")
                if existing:
                    form.add_error(
                        None,
                        ValidationError(
                            "This collection point already has an entry for this date. "
                            "Enable JavaScript for add/subtract prompts, or edit the existing row."
                        ),
                    )
                    return self.form_invalid(form)
            elif cd.get("source") == CollectionSource.FARMER:
                existing = _existing_farmer_row(
                    cd.get("date"),
                    cd.get("route"),
                    cd.get("farmer"),
                )
                if existing and is_ajax:
                    return _conflict_json(existing, cd.get("kg"), "farmer")
                if existing:
                    form.add_error(
                        None,
                        ValidationError(
                            "This farmer already has a collection entry for this date and route. "
                            "Enable JavaScript for add/subtract prompts, or edit the existing row."
                        ),
                    )
                    return self.form_invalid(form)
            raise

    def form_invalid(self, form):
        if self.request.headers.get("X-Requested-With") == "XMLHttpRequest":
            errs_text = str(form.errors)
            source = form.data.get("source")
            kg = form.cleaned_data.get("kg") if hasattr(form, "cleaned_data") else None
            if kg is None:
                kg = form.data.get("kg")

            if "uniq_daily_route_point_collection" in errs_text and source == CollectionSource.POINT:
                existing = _existing_point_row(
                    form.cleaned_data.get("date"),
                    form.cleaned_data.get("route"),
                    form.cleaned_data.get("collection_point"),
                )
                if existing:
                    return _conflict_json(existing, kg, "point")

            if "uniq_daily_route_farmer_collection" in errs_text and source == CollectionSource.FARMER:
                existing = _existing_farmer_row(
                    form.cleaned_data.get("date"),
                    form.cleaned_data.get("route"),
                    form.cleaned_data.get("farmer"),
                )
                if existing:
                    return _conflict_json(existing, kg, "farmer")

            # Use 200 so the browser does not log a spurious "failed to load" for normal validation.
            return JsonResponse(
                {"ok": False, "errors": json.loads(form.errors.as_json())},
                status=200,
            )
        return super().form_invalid(form)


class MilkCollectionAdjustView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Apply a signed kg change to an existing milk collection row; creates MilkCollectionAdjustment audit rows."""

    permission_required = "collections.add_milkcollection"

    def get(self, request, *args, **kwargs):
        return HttpResponseNotAllowed(["POST"])

    def post(self, request, *args, **kwargs):
        operation = (request.POST.get("operation") or "").lower()
        amount_raw = request.POST.get("kg_amount")

        if operation not in ("add", "subtract"):
            return JsonResponse({"ok": False, "error": "Missing or invalid operation."}, status=400)

        try:
            amount = Decimal(str(amount_raw or "0"))
        except (InvalidOperation, TypeError):
            return JsonResponse({"ok": False, "error": "Invalid amount."}, status=400)

        if amount <= 0:
            return JsonResponse({"ok": False, "error": "Amount must be greater than zero."}, status=400)

        mc_pk = kwargs["pk"]
        get_object_or_404(MilkCollection.objects.only("id"), pk=mc_pk)

        delta = amount if operation == "add" else -amount

        try:
            with transaction.atomic():
                mc = MilkCollection.objects.select_for_update().get(pk=mc_pk)
                new_kg = (mc.kg + delta).quantize(Decimal("0.01"))
                if new_kg < 0:
                    return JsonResponse(
                        {
                            "ok": False,
                            "error": f"Cannot subtract {amount} kg; current total is only {mc.kg} kg.",
                        },
                        status=400,
                    )
                MilkCollectionAdjustment.objects.create(
                    milk_collection=mc,
                    delta_kg=delta,
                    created_by=request.user,
                )
                mc.kg = new_kg
                mc.save()
        except ValidationError as exc:
            msgs = getattr(exc, "messages", None) or getattr(exc, "message_dict", None)
            if isinstance(msgs, dict):
                err = "; ".join(str(v) for v in msgs.values())
            elif msgs:
                err = "; ".join(str(m) for m in msgs)
            else:
                err = str(exc)
            return JsonResponse({"ok": False, "error": err}, status=400)

        return JsonResponse({"ok": True, "new_kg": str(mc.kg), "new_liters": str(mc.liters)})


class MilkCollectionInlineQuantityView(LoginRequiredMixin, View):
    """Superuser-only inline kg/liters edit from the milk collection list."""

    def post(self, request, pk):
        if not request.user.is_superuser:
            return JsonResponse(
                {"ok": False, "error": "Only superusers can edit quantity inline."},
                status=403,
            )
        field = (request.POST.get("field") or "").strip().lower()
        value_raw = (request.POST.get("value") or "").replace(",", "").strip()
        if field not in {"kg", "liters"}:
            return JsonResponse({"ok": False, "error": "Invalid field."}, status=400)
        try:
            value = Decimal(value_raw)
        except (InvalidOperation, TypeError):
            return JsonResponse({"ok": False, "error": "Invalid quantity."}, status=400)
        if value < 0:
            return JsonResponse({"ok": False, "error": "Quantity cannot be negative."}, status=400)

        if field == "liters":
            new_kg = (value / MILK_LITER_FACTOR).quantize(Decimal("0.01"))
        else:
            new_kg = value.quantize(Decimal("0.01"))

        qs = filter_by_user_branches(
            MilkCollection.objects.select_related("branch"),
            request.user,
            "branch_id",
        )
        try:
            with transaction.atomic():
                mc = qs.select_for_update().get(pk=pk)
                delta = (new_kg - mc.kg).quantize(Decimal("0.01"))
                if delta != Decimal("0.00"):
                    MilkCollectionAdjustment.objects.create(
                        milk_collection=mc,
                        delta_kg=delta,
                        created_by=request.user,
                    )
                    mc.kg = new_kg
                    mc.save()
        except MilkCollection.DoesNotExist:
            return JsonResponse({"ok": False, "error": "Record not found."}, status=404)
        except ValidationError as exc:
            msgs = getattr(exc, "messages", None) or getattr(exc, "message_dict", None)
            if isinstance(msgs, dict):
                err = "; ".join(str(v) for v in msgs.values())
            elif msgs:
                err = "; ".join(str(m) for m in msgs)
            else:
                err = str(exc)
            return JsonResponse({"ok": False, "error": err}, status=400)

        return JsonResponse(
            {
                "ok": True,
                "kg": str(mc.kg),
                "liters": str(mc.liters),
            }
        )


class MilkCollectionAdjustmentHistoryView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = MilkCollectionAdjustment
    template_name = "collections/milk_collection_adjustments.html"
    context_object_name = "adjustments"
    permission_required = "collections.view_milkcollectionadjustment"

    def get_queryset(self):
        return (
            MilkCollectionAdjustment.objects.filter(
                milk_collection_id=self.kwargs["pk"],
                is_deleted=False,
            )
            .select_related("created_by", "milk_collection")
            .order_by("created_at", "id")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["milk_collection"] = get_object_or_404(
            MilkCollection.objects.select_related("route", "collection_point", "farmer"),
            pk=self.kwargs["pk"],
        )
        return ctx


class MilkCollectionUpdateView(
    LoginRequiredMixin,
    PermissionRequiredMixin,
    ModelFormPageTitleMixin,
    MilkCollectionFormContextMixin,
    UpdateView,
):
    permission_required = 'collections.change_milkcollection'
    model = MilkCollection
    form_class = MilkCollectionForm
    template_name = 'collections/milk_collection_form.html'

    def get_queryset(self):
        qs = super().get_queryset().select_related("branch")
        return filter_by_user_branches(qs, self.request.user, "branch_id")

    def get_success_url(self):
        return reverse("milk-collection-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        with transaction.atomic():
            return super().form_valid(form)

class MilkFactorListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = MilkFactor
    template_name = "collections/milk_factor_list.html"
    permission_required = "collections.view_milkfactor"

    def get_queryset(self):
        qs = (
            super()
            .get_queryset()
            .select_related("route", "collection_point", "farmer", "branch")
            .order_by("-date", "route__code", "collection_point__number", "farmer__full_name")
        )
        qs = filter_by_user_branches(qs, self.request.user, "branch_id")
        branch_raw = (self.request.GET.get("branch") or "").strip()
        if branch_raw.isdigit():
            qs = qs.filter(branch_id=int(branch_raw))
        route_raw = (self.request.GET.get("route") or "").strip()
        if route_raw.isdigit():
            qs = qs.filter(route_id=int(route_raw))
        source = (self.request.GET.get("source") or "").strip().lower()
        if source in {CollectionSource.POINT, CollectionSource.FARMER}:
            qs = qs.filter(source=source)
        date_from = parse_date((self.request.GET.get("date_from") or "").strip())
        if date_from:
            qs = qs.filter(date__gte=date_from)
        date_to = parse_date((self.request.GET.get("date_to") or "").strip())
        if date_to:
            qs = qs.filter(date__lte=date_to)
        farmer_raw = (self.request.GET.get("farmer") or "").strip()
        if farmer_raw.isdigit():
            qs = qs.filter(farmer_id=int(farmer_raw))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        allowed_branches = get_allowed_branches_qs(self.request.user).order_by("code", "name")
        if self.request.user.is_superuser:
            allowed_branches = Branch.objects.order_by("code", "name")
        selected_branch = (self.request.GET.get("branch") or "").strip()
        selected_route = (self.request.GET.get("route") or "").strip()
        selected_source = (self.request.GET.get("source") or "").strip().lower()
        date_from = (self.request.GET.get("date_from") or "").strip()
        date_to = (self.request.GET.get("date_to") or "").strip()
        route_qs = Route.objects.select_related("branch").order_by("code", "name")
        if not self.request.user.is_superuser:
            allowed_ids = list(allowed_branches.values_list("id", flat=True))
            route_qs = route_qs.filter(branch_id__in=allowed_ids) if allowed_ids else route_qs.none()
        if selected_branch.isdigit():
            route_qs = route_qs.filter(branch_id=int(selected_branch))
        ctx["filter_branches"] = allowed_branches
        ctx["filter_routes"] = [
            {"id": r.pk, "label": f"{r.code} — {r.name}", "branch_id": r.branch_id}
            for r in route_qs
        ]
        ctx["selected_branch"] = selected_branch
        ctx["selected_route"] = selected_route
        ctx["selected_source"] = selected_source
        ctx["date_from"] = date_from
        ctx["date_to"] = date_to
        ctx["has_active_filters"] = bool(
            selected_branch or selected_route or selected_source or date_from or date_to
        )
        return ctx

    def post(self, request, *args, **kwargs):
        if not request.user.has_perm("collections.delete_milkfactor"):
            messages.error(request, "You do not have permission to delete milk factors.")
            return redirect("milk-factor-list")

        action = (request.POST.get("bulk_action") or "").strip()
        if action not in {"delete_one", "delete_selected", "delete_all"}:
            messages.error(request, "Invalid bulk action.")
            return redirect("milk-factor-list")

        base_qs = self.get_queryset()
        if action == "delete_all":
            if not request.user.is_superuser:
                messages.error(request, "Only superuser can delete all records.")
                return redirect("milk-factor-list")
            target_qs = base_qs
        elif action == "delete_one":
            one_id = request.POST.get("one_id")
            target_qs = base_qs.filter(pk=one_id) if str(one_id or "").isdigit() else base_qs.none()
        else:
            selected_ids = [int(x) for x in request.POST.getlist("selected_ids") if str(x).isdigit()]
            target_qs = base_qs.filter(pk__in=selected_ids) if selected_ids else base_qs.none()

        deleted_count = target_qs.count()
        if deleted_count == 0:
            messages.warning(request, "No matching milk factors selected.")
            return redirect("milk-factor-list")

        target_qs.delete()
        messages.success(request, f"Deleted {deleted_count} milk factor record(s).")
        return redirect("milk-factor-list")


class MilkFactorCreateView(
    LoginRequiredMixin,
    PermissionRequiredMixin,
    ModelFormPageTitleMixin,
    MilkFactorFormContextMixin,
    CreateView,
):
    permission_required = "collections.add_milkfactor"
    model = MilkFactor
    form_class = MilkFactorForm
    template_name = "collections/milk_factor_form.html"
    success_url = reverse_lazy("milk-factor-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_success_url(self):
        if str(self.request.POST.get("standalone") or "").strip() == "1":
            return f"{reverse('milk-factor-add')}?standalone=1"
        return str(self.success_url)

    def form_valid(self, form):
        is_ajax = self.request.headers.get("X-Requested-With") == "XMLHttpRequest"
        if is_ajax:
            self.object = form.save()
            return JsonResponse({"ok": True, "redirect_url": self.get_success_url()})
        return super().form_valid(form)

    def form_invalid(self, form):
        if self.request.headers.get("X-Requested-With") == "XMLHttpRequest":
            errs_text = str(form.errors)
            if "uniq_daily_route_point_factor" in errs_text:
                return JsonResponse({"ok": False, "duplicate": True, "duplicate_kind": "point"}, status=200)
            if "uniq_daily_route_farmer_factor" in errs_text:
                return JsonResponse({"ok": False, "duplicate": True, "duplicate_kind": "farmer"}, status=200)
            return JsonResponse({"ok": False, "errors": json.loads(form.errors.as_json())}, status=200)
        return super().form_invalid(form)


class MilkFactorUpdateView(
    LoginRequiredMixin,
    PermissionRequiredMixin,
    ModelFormPageTitleMixin,
    MilkFactorFormContextMixin,
    UpdateView,
):
    permission_required = "collections.change_milkfactor"
    model = MilkFactor
    form_class = MilkFactorForm
    template_name = "collections/milk_factor_form.html"
    success_url = reverse_lazy("milk-factor-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs


class MilkCollectionTallyCellSaveView(LoginRequiredMixin, View):
    http_method_names = ["post"]

    def post(self, request):
        try:
            result = save_tally_cell(request)
        except ValidationError as exc:
            msg = exc.messages[0] if exc.messages else str(exc)
            return JsonResponse({"ok": False, "error": msg}, status=400)
        except PermissionDenied:
            return JsonResponse({"ok": False, "error": "Permission denied."}, status=403)
        return JsonResponse({"ok": True, **result})


class MilkCollectionTallySheetView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "collections.view_milkcollectiontallysheet"
    template_name = "collections/milk_collection_tally_sheet.html"

    def get(self, request):
        ctx = build_tally_sheet_context(request)
        return render(request, self.template_name, ctx)

    def post(self, request):
        try:
            result = save_tally_sheet(request)
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
            point_id = (request.POST.get("point") or "").strip()
            unit = (request.POST.get("unit") or "").strip()
            branch = (request.POST.get("branch") or "").strip()
            period = (request.POST.get("period") or "").strip()
            from_date = (request.POST.get("from") or "").strip()
            to_date = (request.POST.get("to") or "").strip()
            params = {"point": point_id}
            if from_date and to_date:
                params["from"] = from_date
                params["to"] = to_date
            elif period:
                params["period"] = period
            if unit:
                params["unit"] = unit
            if branch.isdigit():
                params["branch"] = branch
            return redirect(f"{reverse('milk-collection-tally')}?{urlencode(params)}")
        except PermissionDenied:
            raise
        msg = f"Tally sheet saved ({result['saved_qty']} quantities"
        if result["saved_factor"]:
            msg += f", {result['saved_factor']} factor values"
        msg += ")."
        if result["skipped_paid"]:
            msg += f" {result['skipped_paid']} paid cell(s) were not changed."
        messages.success(request, msg)
        point = result["point"]
        branch_raw = (request.POST.get("branch") or "").strip()
        branch_id = int(branch_raw) if branch_raw.isdigit() else None
        params = tally_sheet_query_params(
            point_id=point.pk,
            start=result["start"],
            end=result["end"],
            unit=(request.POST.get("unit") or "").strip(),
            branch_id=branch_id,
        )
        return redirect(f"{reverse('milk-collection-tally')}?{urlencode(params)}")
