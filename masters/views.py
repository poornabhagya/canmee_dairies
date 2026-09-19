from canmee_dairies.mixins import AnyPermissionRequiredMixin, ModelFormPageTitleMixin, RedirectGetDeleteMixin
from branches.utils import filter_by_user_branches, filter_m2m_by_branch, filter_m2m_by_user_branches, get_allowed_branches_qs
from django.contrib import messages
import csv
import re
from django.db.models import Prefetch, Count, Max, Q, Sum, DecimalField, Value, ProtectedError, F
from django.db import transaction
from django.db.models.functions import Coalesce
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin, UserPassesTestMixin
from django.http import HttpResponse, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, render, redirect
from django.urls import reverse, reverse_lazy
from django.utils.dateparse import parse_date
from django.views import View
from django.views.generic import ListView, CreateView, UpdateView, DetailView, DeleteView, TemplateView
from datetime import date
from decimal import Decimal, InvalidOperation
from canmee_dairies.constants import MILK_LITER_FACTOR
from canmee_dairies.excel_io import cell_missing, get_pandas
from canmee_dairies.formatting import format_money
from .farmer_delete import delete_farmer_blocker_record, format_farmer_delete_blocked_payload
from .forms import (
    RouteForm,
    CollectionPointForm,
    CollectionPointCreateForm,
    CollectionPointFeeUpdateForm,
    CollectionPointBankAccountForm,
    FarmerBankAccountForm,
    CompanyProfileForm,
    CompanyBankAccountForm,
    BuyerForm,
    BuyerPaymentReceiveForm,
    BuyerRateUpdateForm,
    FarmerForm,
    FarmerRateUpdateForm,
    FarmerResignationForm,
)
from .models import (
    Route,
    CollectionPoint,
    CollectionPointBankAccount,
    FarmerBankAccount,
    CompanyProfile,
    CompanyBankAccount,
    CollectionPointFeeHistory,
    Buyer,
    BuyerRateHistory,
    Farmer,
    FarmerRateHistory,
)
from .buyer_rates import annotate_rate_history_windows
from user_management.templatetags.user_tags import user_is_admin_or_superuser
from .farmer_financials import (
    build_farmer_financial_context,
    build_farmer_milk_collection_context,
    bulk_farmer_avg_daily_milk_kg,
    build_farmer_statement_context,
    parse_statement_period,
)
from .farmer_list_metrics import (
    annotate_farmer_list_queryset,
    build_farmer_account_summaries,
    summarize_farmer_list_rows,
)
from .point_list_metrics import (
    attach_point_list_milk_metrics,
    build_point_account_summaries,
    bulk_point_avg_daily_milk_kg,
    summarize_point_list_rows,
)
from .point_financials import (
    build_collection_point_financial_context,
    build_collection_point_statement_context,
    build_point_additional_farmer_totals,
    build_point_milk_collection_context,
)

class RouteListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = Route
    template_name = 'masters/route_list.html'
    permission_required = "masters.view_route"

    def get_queryset(self):
        from milk_collections.models import CollectionSource

        today = date.today()
        qs = super().get_queryset().select_related("branch")
        qs = filter_by_user_branches(qs, self.request.user, "branch_id")
        return qs.annotate(
            today_milk_kg=Coalesce(
                Sum(
                    "milkcollection__kg",
                    filter=Q(
                        milkcollection__date=today,
                        milkcollection__source=CollectionSource.POINT,
                    ),
                ),
                Value(Decimal("0.00"), output_field=DecimalField(max_digits=10, decimal_places=2)),
            )
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        total_milk = Decimal("0.00")
        active_today = 0
        no_driver = 0
        for route in ctx.get("object_list", []):
            milk_kg = route.today_milk_kg or Decimal("0")
            total_milk += milk_kg
            is_active = milk_kg > Decimal("0")
            if is_active:
                active_today += 1
            if not (route.driver_name or "").strip():
                no_driver += 1
            route.is_active_today = is_active
            route.is_inactive_today = not is_active
            route.has_no_driver = not (route.driver_name or "").strip()
        routes = list(ctx.get("object_list", []))
        ctx["route_summary"] = {
            "count": len(routes),
            "today_milk_kg": total_milk,
            "today_milk_liters": (total_milk * MILK_LITER_FACTOR).quantize(Decimal("0.01")),
            "active_today": active_today,
            "inactive_today": len(routes) - active_today,
            "no_driver": no_driver,
        }
        if self.request.user.has_perm("masters.add_route"):
            ctx["route_create_form"] = RouteForm(user=self.request.user)
        if self.request.user.has_perm("masters.change_route"):
            ctx["route_edit_form"] = RouteForm(user=self.request.user)
        ctx["route_branch_options"] = get_allowed_branches_qs(self.request.user).order_by("code")
        return ctx


class RouteDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = Route
    template_name = "masters/route_detail.html"
    context_object_name = "route"
    permission_required = "masters.view_route"

    def get_queryset(self):
        ordered_points = CollectionPoint.objects.order_by("number", "name")
        qs = (
            super()
            .get_queryset()
            .select_related("branch")
            .prefetch_related(Prefetch("collection_points", queryset=ordered_points))
        )
        return filter_by_user_branches(qs, self.request.user, "branch_id")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from milk_collections.models import CollectionSource, MilkCollection

        route_summary = (
            MilkCollection.objects.filter(route=self.object, source=CollectionSource.POINT)
            .aggregate(
                total_entries=Count("id"),
                total_kg=Sum("kg"),
                total_liters=Sum("liters"),
                latest_date=Max("date"),
            )
        )
        farmer_entries = MilkCollection.objects.filter(
            route=self.object, source=CollectionSource.FARMER
        ).count()
        ctx["route_collection"] = {
            "total_entries": route_summary["total_entries"] or 0,
            "point_entries": route_summary["total_entries"] or 0,
            "farmer_entries": farmer_entries,
            "total_kg": route_summary["total_kg"] or 0,
            "total_liters": route_summary["total_liters"] or 0,
            "latest_date": route_summary["latest_date"],
        }
        ctx["route_smart_stats"] = {
            "collection_points": self.object.collection_points.count(),
            "farmers": Farmer.objects.filter(route_id=self.object.pk).count(),
            "milk_entries": route_summary["total_entries"] or 0,
        }
        ctx["route_point_collections"] = (
            MilkCollection.objects.filter(route=self.object, source=CollectionSource.POINT)
            .select_related("branch", "route", "collection_point", "created_by")
            .order_by("-date", "collection_point__number")
        )
        return ctx


class RouteDeleteView(LoginRequiredMixin, PermissionRequiredMixin, RedirectGetDeleteMixin, DeleteView):
    model = Route
    success_url = reverse_lazy("route-list")
    permission_required = "masters.delete_route"

    def get_queryset(self):
        qs = super().get_queryset().select_related("branch")
        return filter_by_user_branches(qs, self.request.user, "branch_id")


class RouteQuickCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.add_route"

    def post(self, request):
        form = RouteForm(data=request.POST, user=request.user)
        if not form.is_valid():
            errs = {name: [str(e) for e in items] for name, items in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errs}, status=400)
        route = form.save()
        payload = {
            "id": route.pk,
            "code": route.code,
            "name": route.name,
            "detail_url": reverse("route-detail", kwargs={"pk": route.pk}),
        }
        if user_is_admin_or_superuser(request.user):
            payload["driver_access_code"] = route.driver_access_code or ""
        return JsonResponse({"ok": True, "route": payload})


class RouteQuickUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.change_route"

    def post(self, request, pk):
        route_qs = filter_by_user_branches(Route.objects.filter(pk=pk), request.user, "branch_id")
        route = get_object_or_404(route_qs)
        form = RouteForm(data=request.POST, instance=route, user=request.user)
        if not form.is_valid():
            errs = {name: [str(e) for e in items] for name, items in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errs}, status=400)
        route = form.save()
        payload = {
            "id": route.pk,
            "name": route.name,
            "branch_id": route.branch_id,
            "vehicle_name": route.vehicle_name or "",
            "driver_name": route.driver_name or "",
            "driver_can_view_route_summary": route.driver_can_view_route_summary,
            "driver_can_view_point_summary": route.driver_can_view_point_summary,
            "driver_can_view_farmer_goods": route.driver_can_view_farmer_goods,
            "detail_url": reverse("route-detail", kwargs={"pk": route.pk}),
        }
        if user_is_admin_or_superuser(request.user):
            payload["driver_access_code"] = route.driver_access_code or ""
        return JsonResponse({"ok": True, "route": payload})


class RouteCollectionPointQuickCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.add_collectionpoint"

    def post(self, request, pk):
        route_qs = filter_by_user_branches(Route.objects.filter(pk=pk), request.user, "branch_id")
        route = get_object_or_404(route_qs)
        data = request.POST.copy()
        data["route"] = str(route.pk)
        form = CollectionPointForm(data=data, user=request.user)
        if not form.is_valid():
            errs = {name: [str(e) for e in items] for name, items in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errs}, status=400)
        point = form.save()
        return JsonResponse(
            {
                "ok": True,
                "point": {
                    "id": point.pk,
                    "number": point.number,
                    "name": point.name,
                    "location": point.location or "",
                    "collector_fee": str(point.collector_fee),
                    "additional": str(point.additional),
                },
            }
        )


class RouteCollectionPointQuickUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.change_collectionpoint"

    def post(self, request, pk, point_id):
        route_qs = filter_by_user_branches(Route.objects.filter(pk=pk), request.user, "branch_id")
        route = get_object_or_404(route_qs)
        point = get_object_or_404(CollectionPoint.objects.filter(pk=point_id, route=route))
        data = request.POST.copy()
        data["route"] = str(route.pk)
        form = CollectionPointForm(data=data, instance=point, user=request.user)
        if not form.is_valid():
            errs = {name: [str(e) for e in items] for name, items in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errs}, status=400)
        point = form.save()
        return JsonResponse(
            {
                "ok": True,
                "point": {
                    "id": point.pk,
                    "number": point.number,
                    "name": point.name,
                    "location": point.location or "",
                    "collector_fee": str(point.collector_fee),
                    "additional": str(point.additional),
                },
            }
        )


class RouteCollectionPointQuickDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.delete_collectionpoint"

    def post(self, request, pk, point_id):
        route_qs = filter_by_user_branches(Route.objects.filter(pk=pk), request.user, "branch_id")
        route = get_object_or_404(route_qs)
        point = get_object_or_404(CollectionPoint.objects.filter(pk=point_id, route=route))
        try:
            point.delete()
        except Exception:
            return JsonResponse(
                {
                    "ok": False,
                    "error": (
                        "Could not delete this collection point. "
                        "It may still be used by farmers, collections, or factors."
                    ),
                },
                status=409,
            )
        return JsonResponse({"ok": True})


class RouteMilkCollectionQuickDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "collections.delete_milkcollection"

    def post(self, request, pk, collection_id):
        from milk_collections.models import CollectionSource, MilkCollection

        route_qs = filter_by_user_branches(Route.objects.filter(pk=pk), request.user, "branch_id")
        route = get_object_or_404(route_qs)
        row = get_object_or_404(
            MilkCollection.objects.filter(pk=collection_id, route=route, source=CollectionSource.POINT)
        )
        try:
            row.delete()
        except Exception:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Could not delete this milk collection record.",
                },
                status=409,
            )
        return JsonResponse({"ok": True})

class RouteCreateView(LoginRequiredMixin, PermissionRequiredMixin, ModelFormPageTitleMixin, CreateView):
    permission_required = 'masters.add_route'
    model = Route
    form_class = RouteForm
    template_name = 'masters/form.html'
    success_url = reverse_lazy('route-list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

class RouteUpdateView(LoginRequiredMixin, PermissionRequiredMixin, ModelFormPageTitleMixin, UpdateView):
    permission_required = 'masters.change_route'
    model = Route
    form_class = RouteForm
    template_name = 'masters/form.html'
    success_url = reverse_lazy('route-list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

class CollectionPointListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = CollectionPoint
    template_name = 'masters/collection_point_list.html'
    permission_required = "masters.view_collectionpoint"

    def get_queryset(self):
        qs = filter_by_user_branches(
            super()
            .get_queryset()
            .select_related("route", "route__branch"),
            self.request.user,
            "route__branch_id",
        )
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = date.today()
        points = list(ctx.get("object_list", []))
        attach_point_list_milk_metrics(points, today)
        status_counts = summarize_point_list_rows(points, today=today)
        avg_daily_milk = bulk_point_avg_daily_milk_kg([p.pk for p in points], today=today)
        for point in points:
            point.avg_daily_milk_kg = avg_daily_milk.get(point.pk)
        branches_qs = get_allowed_branches_qs(self.request.user).order_by("code")
        branch_ids = list(branches_qs.values_list("id", flat=True))
        ctx["point_branch_options"] = branches_qs
        routes_qs = filter_by_user_branches(
            Route.objects.select_related("branch").order_by("name"),
            self.request.user,
            "branch_id",
        )
        ctx["point_filter_routes"] = [
            {"id": route.pk, "branch_id": route.branch_id, "label": route.name}
            for route in routes_qs
        ]
        can_view_account = self.request.user.has_perm("reports.view_collectionpointpaymentsheet")
        ctx["can_view_point_account"] = can_view_account
        if can_view_account and points:
            account_map = build_point_account_summaries(points, branch_ids, today=today)
            for point in points:
                point.account_summary = account_map.get(point.pk)
        else:
            for point in points:
                point.account_summary = None
        total_milk = sum((p.today_milk_kg or Decimal("0") for p in points), Decimal("0"))
        ctx["point_summary"] = {
            "count": len(points),
            "today_milk_kg": total_milk,
            "today_milk_liters": (total_milk * MILK_LITER_FACTOR).quantize(Decimal("0.01")),
            **status_counts,
        }
        if self.request.user.has_perm("masters.add_collectionpoint"):
            ctx["point_create_form"] = CollectionPointForm(user=self.request.user)
        if self.request.user.has_perm("masters.change_collectionpoint"):
            ctx["point_edit_form"] = CollectionPointForm(user=self.request.user)
        ctx["point_route_options"] = routes_qs
        return ctx


class CollectionPointQuickCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.add_collectionpoint"

    def post(self, request):
        form = CollectionPointForm(data=request.POST, user=request.user)
        if not form.is_valid():
            errs = {name: [str(e) for e in items] for name, items in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errs}, status=400)
        point = form.save()
        return JsonResponse(
            {
                "ok": True,
                "point": {
                    "id": point.pk,
                    "route_id": point.route_id,
                    "number": point.number,
                    "name": point.name,
                    "location": point.location or "",
                    "collector_fee": str(point.collector_fee),
                    "additional": str(point.additional),
                },
            }
        )


class CollectionPointQuickUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.change_collectionpoint"

    def post(self, request, pk):
        point_qs = filter_by_user_branches(
            CollectionPoint.objects.filter(pk=pk).select_related("route"),
            request.user,
            "route__branch_id",
        )
        point = get_object_or_404(point_qs)
        form = CollectionPointForm(data=request.POST, instance=point, user=request.user)
        if not form.is_valid():
            errs = {name: [str(e) for e in items] for name, items in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errs}, status=400)
        point = form.save()
        return JsonResponse(
            {
                "ok": True,
                "point": {
                    "id": point.pk,
                    "route_id": point.route_id,
                    "number": point.number,
                    "name": point.name,
                    "location": point.location or "",
                    "collector_fee": str(point.collector_fee),
                    "additional": str(point.additional),
                },
            }
        )


class CollectionPointQuickDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.delete_collectionpoint"

    def post(self, request, pk):
        point_qs = filter_by_user_branches(
            CollectionPoint.objects.filter(pk=pk),
            request.user,
            "route__branch_id",
        )
        point = get_object_or_404(point_qs)
        try:
            point.delete()
        except Exception:
            return JsonResponse(
                {
                    "ok": False,
                    "error": (
                        "Could not delete this collection point. "
                        "It may still be used by farmers, collections, or factors."
                    ),
                },
                status=409,
            )
        return JsonResponse({"ok": True})


class CollectionPointDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = CollectionPoint
    template_name = "masters/collection_point_detail.html"
    context_object_name = "point"
    permission_required = "masters.view_collectionpoint"

    def get_queryset(self):
        return filter_by_user_branches(
            CollectionPoint.objects.select_related("route", "route__branch").prefetch_related(
                "bank_accounts"
            ),
            self.request.user,
            "route__branch_id",
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        start, end, full = parse_statement_period(self.request)
        period_active = bool(start and end)
        ctx["statement_from"] = start.isoformat() if start else ""
        ctx["statement_to"] = end.isoformat() if end else ""
        ctx["statement_period_active"] = period_active
        from django.utils import timezone as dj_timezone

        ctx["point_fee_history"] = self.object.fee_history.select_related("changed_by").all()[:20]
        ctx["fee_form"] = kwargs.get(
            "fee_form",
            CollectionPointFeeUpdateForm(
                initial={
                    "collector_fee": self.object.collector_fee,
                    "additional": self.object.additional,
                    "effective_from": dj_timezone.localdate(),
                }
            ),
        )
        ctx["bank_account_form"] = kwargs.get(
            "bank_account_form",
            CollectionPointBankAccountForm(),
        )
        ctx.update(
            build_point_milk_collection_context(
                self.object,
                start if period_active else None,
                end if period_active else None,
            )
        )
        today = date.today()
        point_farmers_qs = annotate_farmer_list_queryset(
            Farmer.objects.filter(collection_point=self.object)
            .select_related("branch", "route", "collection_point")
            .order_by("common_name", "full_name"),
            today=today,
        )
        point_farmers = list(point_farmers_qs)
        status_counts = summarize_farmer_list_rows(point_farmers, today=today)
        avg_daily_milk = bulk_farmer_avg_daily_milk_kg([f.pk for f in point_farmers])
        for farmer in point_farmers:
            farmer.avg_daily_milk_kg = avg_daily_milk.get(farmer.pk)
        can_view_account = self.request.user.has_perm("reports.view_farmerpaymentsheet")
        ctx["can_view_farmer_account"] = can_view_account
        if can_view_account and point_farmers:
            branch_ids = list(
                get_allowed_branches_qs(self.request.user).values_list("id", flat=True)
            )
            account_map = build_farmer_account_summaries(
                [f.pk for f in point_farmers],
                branch_ids,
                today=today,
            )
            for farmer in point_farmers:
                farmer.account_summary = account_map.get(farmer.pk)
        else:
            for farmer in point_farmers:
                farmer.account_summary = None
        ctx["point_farmers"] = point_farmers
        ctx["point_farmer_summary"] = {
            "count": len(point_farmers),
            **status_counts,
        }
        selected_additional_ids = set(
            self.object.additional_farmers.values_list("id", flat=True)
        )
        ctx["additional_farmer_ids"] = selected_additional_ids
        ctx["additional_uses_all_farmers"] = not selected_additional_ids
        period_start = start if period_active else None
        period_end = end if period_active else None
        ctx["additional_farmer_totals"] = build_point_additional_farmer_totals(
            self.object,
            start=period_start,
            end=period_end,
        )
        ctx["can_edit_additional_farmers"] = self.request.user.has_perm(
            "masters.change_collectionpoint"
        )
        ctx["can_view_milk_payment_status"] = self.request.user.has_perm(
            "collections.view_milkcollectionpaymentstatus"
        )
        ctx["can_view_farmer_advances"] = self.request.user.has_perm(
            "masters.view_farmeradvancepayment"
        )
        ctx["can_view_point_advances"] = self.request.user.has_perm(
            "masters.view_collectionpointadvancepayment"
        )
        ctx["can_view_farmer_goods"] = self.request.user.has_perm(
            "suppliers.view_farmergoodsissue"
        )
        ctx["can_change_farmer_goods"] = self.request.user.has_perm(
            "suppliers.change_farmergoodsissue"
        )
        ctx["can_delete_farmer_goods"] = self.request.user.has_perm(
            "suppliers.delete_farmergoodsissue"
        )
        ctx["can_view_farmer_loans"] = self.request.user.has_perm(
            "farmer_loans.view_farmerloan"
        )
        ctx["can_view_cp_loans"] = self.request.user.has_perm(
            "collection_point_loans.view_collectionpointloan"
        )
        ctx["can_view_farmer_payments"] = self.request.user.has_perm(
            "reports.view_paymentsettlement"
        )
        if (
            ctx["can_view_farmer_advances"]
            or ctx["can_view_point_advances"]
            or ctx["can_view_farmer_goods"]
            or ctx["can_view_farmer_loans"]
            or ctx["can_view_cp_loans"]
            or ctx["can_view_farmer_payments"]
        ):
            period_start = start if period_active else None
            period_end = end if period_active else None
            ctx.update(
                build_collection_point_financial_context(
                    self.object, period_start, period_end
                )
            )
            from urllib.parse import urlencode

            goods_report_params = [("collection_point", self.object.pk)]
            if self.object.route_id:
                goods_report_params.append(("route", self.object.route_id))
                if self.object.route.branch_id:
                    goods_report_params.append(("branch", self.object.route.branch_id))
            if period_active:
                goods_report_params.extend(
                    [("start", start.isoformat()), ("end", end.isoformat())]
                )
            ctx["point_goods_summary_report_query"] = urlencode(goods_report_params)
        return ctx

    def post(self, request, *args, **kwargs):
        """Superuser bulk delete for milk collections listed on this collection point detail page."""
        self.object = self.get_object()
        if not request.user.is_superuser:
            messages.error(request, "Only superusers can bulk delete milk collections.")
            return self._redirect_to_detail(request)

        action = (request.POST.get("bulk_action") or "").strip()
        if action not in {"delete_selected", "delete_all"}:
            messages.error(request, "Invalid bulk action.")
            return self._redirect_to_detail(request)

        from milk_collections.models import CollectionSource, MilkCollection

        start, end, _full = parse_statement_period(request)
        from_raw = (request.POST.get("from") or "").strip()
        to_raw = (request.POST.get("to") or "").strip()
        if from_raw and to_raw:
            parsed_start = parse_date(from_raw)
            parsed_end = parse_date(to_raw)
            if parsed_start and parsed_end:
                if parsed_start > parsed_end:
                    parsed_start, parsed_end = parsed_end, parsed_start
                start, end = parsed_start, parsed_end

        base_qs = MilkCollection.objects.filter(
            collection_point=self.object,
            source=CollectionSource.POINT,
        )
        if start and end:
            base_qs = base_qs.filter(date__gte=start, date__lte=end)

        if action == "delete_all":
            target_qs = base_qs
        else:
            selected_ids = [int(x) for x in request.POST.getlist("selected_ids") if str(x).isdigit()]
            target_qs = base_qs.filter(pk__in=selected_ids) if selected_ids else base_qs.none()

        deleted_count = target_qs.count()
        if deleted_count == 0:
            messages.warning(request, "No matching milk collections selected.")
            return self._redirect_to_detail(request, start=start, end=end)

        target_qs.delete()
        messages.success(request, f"Deleted {deleted_count} milk collection record(s).")
        return self._redirect_to_detail(request, start=start, end=end)

    def _redirect_to_detail(self, request, start=None, end=None):
        url = reverse("point-detail", kwargs={"pk": self.object.pk})
        if start and end:
            return redirect(f"{url}?from={start.isoformat()}&to={end.isoformat()}")
        redirect_query = (request.POST.get("redirect_query") or "").strip()
        if redirect_query:
            return redirect(f"{url}?{redirect_query}")
        return redirect(url)


class CollectionPointFeeUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.change_collectionpoint"

    def post(self, request, pk):
        point_qs = filter_by_user_branches(
            CollectionPoint.objects.select_related("route", "route__branch"),
            request.user,
            "route__branch_id",
        )
        point = get_object_or_404(point_qs, pk=pk)
        form = CollectionPointFeeUpdateForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Please enter a valid fee update.")
            detail_view = CollectionPointDetailView()
            detail_view.request = request
            detail_view.object = point
            detail_view.kwargs = {"pk": point.pk}
            context = detail_view.get_context_data(fee_form=form)
            return render(request, "masters/collection_point_detail.html", context, status=400)
        point.collector_fee = form.cleaned_data["collector_fee"]
        point.additional = form.cleaned_data["additional"]
        point._fee_effective_from = form.cleaned_data["effective_from"]
        point._fee_changed_by = request.user
        point.save()
        messages.success(
            request,
            f"Collector fee and Additional updated from {form.cleaned_data['effective_from']}.",
        )
        return redirect("point-detail", pk=point.pk)


class CollectionPointBankAccountsUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.change_collectionpoint"

    def _point(self, request, pk):
        return get_object_or_404(
            filter_by_user_branches(
                CollectionPoint.objects.select_related("route", "route__branch"),
                request.user,
                "route__branch_id",
            ),
            pk=pk,
        )

    def _render_detail(self, request, point, form, status=400):
        messages.error(request, "Please check the bank account details and try again.")
        detail_view = CollectionPointDetailView()
        detail_view.request = request
        detail_view.object = point
        detail_view.kwargs = {"pk": point.pk}
        context = detail_view.get_context_data(bank_account_form=form)
        return render(request, "masters/collection_point_detail.html", context, status=status)

    def post(self, request, pk):
        point = self._point(request, pk)
        account_id = (request.POST.get("account_id") or "").strip()
        instance = None
        if account_id:
            instance = get_object_or_404(
                CollectionPointBankAccount,
                pk=account_id,
                collection_point=point,
            )
        form = CollectionPointBankAccountForm(request.POST, instance=instance)
        if not form.is_valid():
            return self._render_detail(request, point, form)
        account = form.save(commit=False)
        account.collection_point = point
        account.save()
        messages.success(request, "Bank account saved.")
        return redirect("point-detail", pk=point.pk)


class CollectionPointBankAccountDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.change_collectionpoint"

    def post(self, request, pk, account_pk):
        point = get_object_or_404(
            filter_by_user_branches(
                CollectionPoint.objects.select_related("route", "route__branch"),
                request.user,
                "route__branch_id",
            ),
            pk=pk,
        )
        account = get_object_or_404(
            CollectionPointBankAccount,
            pk=account_pk,
            collection_point=point,
        )
        account.delete()
        remaining = list(point.bank_accounts.order_by("id"))
        if remaining and not any(item.is_primary for item in remaining):
            first = remaining[0]
            first.is_primary = True
            first.save(update_fields=["is_primary", "updated_at"])
        messages.success(request, "Bank account removed.")
        return redirect("point-detail", pk=point.pk)


class CollectionPointBankAccountPrimaryView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.change_collectionpoint"

    def post(self, request, pk, account_pk):
        point = get_object_or_404(
            filter_by_user_branches(
                CollectionPoint.objects.select_related("route", "route__branch"),
                request.user,
                "route__branch_id",
            ),
            pk=pk,
        )
        account = get_object_or_404(
            CollectionPointBankAccount,
            pk=account_pk,
            collection_point=point,
        )
        account.is_primary = True
        account.save(update_fields=["is_primary", "updated_at"])
        messages.success(request, "Primary bank account updated.")
        return redirect("point-detail", pk=point.pk)


class CompanyProfileView(LoginRequiredMixin, UserPassesTestMixin, TemplateView):
    template_name = "masters/company_profile.html"

    def test_func(self):
        user = self.request.user
        return bool(
            user.is_superuser
            or user.has_perm("masters.view_companyprofile")
            or user.has_perm("masters.change_companyprofile")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        company = CompanyProfile.get_solo()
        ctx["company"] = company
        ctx["profile_form"] = kwargs.get("profile_form") or CompanyProfileForm(instance=company)
        ctx["bank_account_form"] = kwargs.get("bank_account_form") or CompanyBankAccountForm(
            initial={"is_active": True}
        )
        return ctx

    def post(self, request, *args, **kwargs):
        if not (request.user.is_superuser or request.user.has_perm("masters.change_companyprofile")):
            messages.error(request, "You do not have permission to update the company profile.")
            return redirect("company-profile")
        company = CompanyProfile.get_solo()
        form = CompanyProfileForm(request.POST, instance=company)
        if form.is_valid():
            form.save()
            messages.success(request, "Company profile saved.")
            return redirect("company-profile")
        return self.render_to_response(self.get_context_data(profile_form=form))


class CompanyBankAccountsUpdateView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        user = self.request.user
        return bool(user.is_superuser or user.has_perm("masters.change_companyprofile"))

    def post(self, request):
        company = CompanyProfile.get_solo()
        account_id = (request.POST.get("account_id") or "").strip()
        instance = None
        if account_id:
            instance = get_object_or_404(CompanyBankAccount, pk=account_id, company=company)
        form = CompanyBankAccountForm(request.POST, instance=instance)
        if not form.is_valid():
            messages.error(request, "Please check the bank account details and try again.")
            view = CompanyProfileView()
            view.request = request
            return render(
                request,
                "masters/company_profile.html",
                view.get_context_data(bank_account_form=form),
                status=400,
            )
        account = form.save(commit=False)
        account.company = company
        account.save()
        messages.success(request, "Company bank account saved.")
        return redirect("company-profile")


class CompanyBankAccountDeleteView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        user = self.request.user
        return bool(user.is_superuser or user.has_perm("masters.change_companyprofile"))

    def post(self, request, account_pk):
        company = CompanyProfile.get_solo()
        account = get_object_or_404(CompanyBankAccount, pk=account_pk, company=company)
        account.delete()
        remaining = list(company.bank_accounts.filter(is_active=True).order_by("id"))
        if remaining and not any(item.is_primary for item in remaining):
            first = remaining[0]
            first.is_primary = True
            first.save(update_fields=["is_primary", "updated_at"])
        messages.success(request, "Company bank account removed.")
        return redirect("company-profile")


class CompanyBankAccountPrimaryView(LoginRequiredMixin, UserPassesTestMixin, View):
    def test_func(self):
        user = self.request.user
        return bool(user.is_superuser or user.has_perm("masters.change_companyprofile"))

    def post(self, request, account_pk):
        company = CompanyProfile.get_solo()
        account = get_object_or_404(CompanyBankAccount, pk=account_pk, company=company)
        account.is_primary = True
        account.is_active = True
        account.save(update_fields=["is_primary", "is_active", "updated_at"])
        messages.success(request, "Primary company bank account updated.")
        return redirect("company-profile")


class FarmerBankAccountsUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.change_farmer"

    def _farmer(self, request, pk):
        return get_object_or_404(
            filter_by_user_branches(
                Farmer.objects.select_related("branch", "route", "collection_point"),
                request.user,
                "branch_id",
            ),
            pk=pk,
        )

    def _render_detail(self, request, farmer, form, status=400):
        messages.error(request, "Please check the bank account details and try again.")
        detail_view = FarmerDetailView()
        detail_view.request = request
        detail_view.object = farmer
        detail_view.kwargs = {"pk": farmer.pk}
        context = detail_view.get_context_data(bank_account_form=form)
        return render(request, "masters/farmer_detail.html", context, status=status)

    def post(self, request, pk):
        farmer = self._farmer(request, pk)
        account_id = (request.POST.get("account_id") or "").strip()
        instance = None
        if account_id:
            instance = get_object_or_404(
                FarmerBankAccount,
                pk=account_id,
                farmer=farmer,
            )
        form = FarmerBankAccountForm(request.POST, instance=instance)
        if not form.is_valid():
            return self._render_detail(request, farmer, form)
        account = form.save(commit=False)
        account.farmer = farmer
        account.save()
        messages.success(request, "Bank account saved.")
        return redirect("farmer-detail", pk=farmer.pk)


class FarmerBankAccountDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.change_farmer"

    def post(self, request, pk, account_pk):
        farmer = get_object_or_404(
            filter_by_user_branches(
                Farmer.objects.select_related("branch", "route"),
                request.user,
                "branch_id",
            ),
            pk=pk,
        )
        account = get_object_or_404(FarmerBankAccount, pk=account_pk, farmer=farmer)
        account.delete()
        remaining = list(farmer.bank_accounts.order_by("id"))
        if remaining and not any(item.is_primary for item in remaining):
            first = remaining[0]
            first.is_primary = True
            first.save(update_fields=["is_primary", "updated_at"])
        messages.success(request, "Bank account removed.")
        return redirect("farmer-detail", pk=farmer.pk)


class FarmerBankAccountPrimaryView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.change_farmer"

    def post(self, request, pk, account_pk):
        farmer = get_object_or_404(
            filter_by_user_branches(
                Farmer.objects.select_related("branch", "route"),
                request.user,
                "branch_id",
            ),
            pk=pk,
        )
        account = get_object_or_404(FarmerBankAccount, pk=account_pk, farmer=farmer)
        account.is_primary = True
        account.save(update_fields=["is_primary", "updated_at"])
        messages.success(request, "Primary bank account updated.")
        return redirect("farmer-detail", pk=farmer.pk)


class CollectionPointFeeCellSaveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Inline Collector fee / Additional save from the Collection points list."""

    permission_required = "masters.change_collectionpoint"

    def post(self, request):
        point_id = self._parse_int(request.POST.get("point_id"))
        if not point_id:
            return JsonResponse({"ok": False, "error": "Collection point is required."}, status=400)

        field = (request.POST.get("field") or "").strip()
        if field not in ("collector_fee", "additional"):
            return JsonResponse({"ok": False, "error": "Invalid fee field."}, status=400)

        value_raw = (request.POST.get("value") or "").strip()
        try:
            value_val = Decimal(value_raw)
        except (InvalidOperation, TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "Enter a valid amount."}, status=400)
        if value_val < 0:
            return JsonResponse({"ok": False, "error": "Amount cannot be negative."}, status=400)

        point_qs = filter_by_user_branches(
            CollectionPoint.objects.filter(pk=point_id),
            request.user,
            "route__branch_id",
        )
        point = get_object_or_404(point_qs, pk=point_id)

        prior = getattr(point, field)
        changed = prior != value_val
        if changed:
            setattr(point, field, value_val)
            point._fee_changed_by = request.user
            point.save()

        current = getattr(point, field)
        return JsonResponse(
            {
                "ok": True,
                "point_id": point.pk,
                "field": field,
                "value": str(current),
                "value_display": format_money(current),
                "collector_fee": str(point.collector_fee),
                "additional": str(point.additional),
                "saved": changed,
            }
        )

    @staticmethod
    def _parse_int(raw):
        if raw is None:
            return None
        text = str(raw).strip()
        if not text.isdigit():
            return None
        return int(text)


class CollectionPointFeeHistoryDeleteView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Superuser-only delete of a collection point fee history row."""

    def test_func(self):
        return bool(self.request.user and self.request.user.is_superuser)

    def post(self, request, point_pk, fee_pk):
        point = get_object_or_404(
            filter_by_user_branches(
                CollectionPoint.objects.select_related("route"),
                request.user,
                "route__branch_id",
            ),
            pk=point_pk,
        )
        fee_row = get_object_or_404(
            CollectionPointFeeHistory, pk=fee_pk, collection_point=point
        )
        fee_row.delete()

        latest = (
            CollectionPointFeeHistory.objects.filter(collection_point=point)
            .order_by("-effective_at", "-id")
            .first()
        )
        if latest:
            CollectionPoint.objects.filter(pk=point.pk).update(
                collector_fee=latest.collector_fee,
                additional=latest.additional,
            )
        else:
            CollectionPoint.objects.filter(pk=point.pk).update(
                collector_fee=Decimal("5.00"),
                additional=Decimal("0.00"),
            )

        messages.success(request, "Fee history row deleted.")
        return redirect("point-detail", pk=point.pk)


class CollectionPointAdditionalFarmersView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Save which farmers are used for Additional qty (empty = all farmers at the point)."""

    permission_required = "masters.change_collectionpoint"

    def post(self, request, pk):
        point = get_object_or_404(
            filter_by_user_branches(
                CollectionPoint.objects.select_related("route", "route__branch"),
                request.user,
                "route__branch_id",
            ),
            pk=pk,
        )
        all_ids = set(
            Farmer.objects.filter(collection_point=point).values_list("id", flat=True)
        )
        raw_ids = request.POST.getlist("farmer_ids")
        selected = {int(v) for v in raw_ids if str(v).isdigit() and int(v) in all_ids}
        if not selected or selected == all_ids:
            point.additional_farmers.clear()
            uses_all = True
            applied_ids = list(all_ids)
        else:
            point.additional_farmers.set(selected)
            uses_all = False
            applied_ids = sorted(selected)

        start, end, _full = parse_statement_period(request)
        period_active = bool(start and end)
        totals = build_point_additional_farmer_totals(
            point,
            farmer_ids=applied_ids,
            start=start if period_active else None,
            end=end if period_active else None,
        )
        return JsonResponse(
            {
                "ok": True,
                "uses_all_farmers": uses_all,
                "farmer_ids": applied_ids,
                "totals": {
                    "selected_count": totals["selected_count"],
                    "all_count": totals["all_count"],
                    "total_entries": totals["total_entries"],
                    "total_kg": str(totals["total_kg"]),
                    "total_liters": str(totals["total_liters"]),
                    "latest_date": totals["latest_date"].isoformat() if totals["latest_date"] else "",
                    "additional_rate": str(totals["additional_rate"]),
                    "additional_amount": str(totals["additional_amount"]),
                    "collector_fee_rate": str(totals["collector_fee_rate"]),
                    "collector_fee_amount": str(totals["collector_fee_amount"]),
                },
            }
        )


class CollectionPointStatementPrintView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "masters/collection_point_statement_print.html"
    permission_required = "masters.view_collectionpoint"

    def get_queryset(self):
        return filter_by_user_branches(
            CollectionPoint.objects.select_related("route", "route__branch"),
            self.request.user,
            "route__branch_id",
        )

    def get_point(self):
        if not hasattr(self, "_point"):
            self._point = get_object_or_404(self.get_queryset(), pk=self.kwargs["pk"])
        return self._point

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        point = self.get_point()
        start, end, full = parse_statement_period(self.request)
        if not full and not (start and end):
            full = True
        ctx.update(build_collection_point_statement_context(point, start, end, full=full))
        ctx["can_view_milk_payment_status"] = self.request.user.has_perm(
            "collections.view_milkcollectionpaymentstatus"
        )
        ctx["can_view_farmer_advances"] = self.request.user.has_perm(
            "masters.view_farmeradvancepayment"
        )
        ctx["can_view_point_advances"] = self.request.user.has_perm(
            "masters.view_collectionpointadvancepayment"
        )
        ctx["can_view_farmer_goods"] = self.request.user.has_perm(
            "suppliers.view_farmergoodsissue"
        )
        ctx["can_view_farmer_loans"] = self.request.user.has_perm(
            "farmer_loans.view_farmerloan"
        )
        ctx["can_view_cp_loans"] = self.request.user.has_perm(
            "collection_point_loans.view_collectionpointloan"
        )
        ctx["can_view_farmer_payments"] = self.request.user.has_perm(
            "reports.view_paymentsettlement"
        )
        return ctx


class CollectionPointPeriodReceiptView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "collections.view_milkcollection"

    def get(self, request, pk):
        from milk_collections.models import CollectionSource, MilkCollection

        point_qs = filter_by_user_branches(
            CollectionPoint.objects.filter(pk=pk).select_related("route", "route__branch"),
            request.user,
            "route__branch_id",
        )
        point = get_object_or_404(point_qs)
        from_date_raw = request.GET.get("from")
        to_date_raw = request.GET.get("to")
        from_date = parse_date(from_date_raw or "")
        to_date = parse_date(to_date_raw or "")
        if not from_date or not to_date:
            return HttpResponseBadRequest("Provide from and to dates in YYYY-MM-DD format.")
        if from_date > to_date:
            return HttpResponseBadRequest("From date must be on or before to date.")
        rows = (
            MilkCollection.objects.filter(
                source=CollectionSource.POINT,
                collection_point=point,
                date__gte=from_date,
                date__lte=to_date,
            )
            .select_related("route", "collection_point")
            .order_by("date")
        )
        totals = rows.aggregate(total_kg=Sum("kg"), total_liters=Sum("liters"), total_entries=Count("id"))
        return render(
            request,
            "collections/point_period_receipt.html",
            {
                "point": point,
                "rows": rows,
                "from_date": from_date,
                "to_date": to_date,
                "total_kg": totals["total_kg"] or Decimal("0.00"),
                "total_liters": totals["total_liters"] or Decimal("0.00"),
                "total_entries": totals["total_entries"] or 0,
            },
        )


class FarmerPeriodReceiptView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "collections.view_milkcollection"

    def get(self, request, point_pk, farmer_pk):
        from milk_collections.models import CollectionSource, MilkCollection

        point_qs = filter_by_user_branches(
            CollectionPoint.objects.filter(pk=point_pk).select_related("route", "route__branch"),
            request.user,
            "route__branch_id",
        )
        point = get_object_or_404(point_qs)
        farmer = get_object_or_404(Farmer.objects.filter(pk=farmer_pk, collection_point=point))
        from_date_raw = request.GET.get("from")
        to_date_raw = request.GET.get("to")
        from_date = parse_date(from_date_raw or "")
        to_date = parse_date(to_date_raw or "")
        if not from_date or not to_date:
            return HttpResponseBadRequest("Provide from and to dates in YYYY-MM-DD format.")
        if from_date > to_date:
            return HttpResponseBadRequest("From date must be on or before to date.")
        rows = (
            MilkCollection.objects.filter(
                source=CollectionSource.FARMER,
                farmer=farmer,
                date__gte=from_date,
                date__lte=to_date,
            )
            .select_related("route", "farmer")
            .order_by("date")
        )
        totals = rows.aggregate(total_kg=Sum("kg"), total_liters=Sum("liters"), total_entries=Count("id"))
        return render(
            request,
            "collections/farmer_period_receipt.html",
            {
                "point": point,
                "farmer": farmer,
                "rows": rows,
                "from_date": from_date,
                "to_date": to_date,
                "total_kg": totals["total_kg"] or Decimal("0.00"),
                "total_liters": totals["total_liters"] or Decimal("0.00"),
                "total_entries": totals["total_entries"] or 0,
            },
        )

class CollectionPointCreateView(LoginRequiredMixin, PermissionRequiredMixin, ModelFormPageTitleMixin, CreateView):
    permission_required = 'masters.add_collectionpoint'
    model = CollectionPoint
    form_class = CollectionPointCreateForm
    template_name = 'masters/form.html'
    success_url = reverse_lazy('point-list')

    def _resolved_return_route_id(self):
        raw = self.request.POST.get("return_to_route") or self.request.GET.get("route")
        if not raw or not str(raw).isdigit():
            return None
        pk = int(raw)
        if not filter_by_user_branches(Route.objects.filter(pk=pk), self.request.user, "branch_id").exists():
            return None
        return pk

    def get_initial(self):
        initial = super().get_initial()
        route_id = self._resolved_return_route_id()
        if route_id is not None and not self.request.POST:
            initial["route"] = route_id
            initial["return_to_route"] = route_id
        return initial

    def get_success_url(self):
        route_id = self._resolved_return_route_id()
        if route_id is not None:
            return reverse("route-detail", kwargs={"pk": route_id})
        return reverse("point-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

class CollectionPointUpdateView(LoginRequiredMixin, PermissionRequiredMixin, ModelFormPageTitleMixin, UpdateView):
    permission_required = 'masters.change_collectionpoint'
    model = CollectionPoint
    form_class = CollectionPointForm
    template_name = 'masters/form.html'
    success_url = reverse_lazy('point-list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

class BuyerListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = Buyer
    template_name = 'masters/buyer_list.html'
    permission_required = "masters.view_buyer"

    def get_queryset(self):
        return filter_m2m_by_user_branches(
            Buyer.objects.prefetch_related("branches"),
            self.request.user,
        ).order_by("name")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        buyers = list(ctx.get("object_list", []))
        ctx["buyer_summary"] = {"count": len(buyers)}
        ctx["buyer_branch_options"] = get_allowed_branches_qs(self.request.user).order_by("name")
        if self.request.user.has_perm("masters.add_buyer"):
            buyer_create_form = BuyerForm(user=self.request.user)
            buyer_create_form.fields["branches"].widget.attrs["id"] = "buyer-add-branches"
            ctx["buyer_create_form"] = buyer_create_form
        if self.request.user.has_perm("masters.change_buyer"):
            buyer_edit_form = BuyerForm(user=self.request.user)
            buyer_edit_form.fields["branches"].widget.attrs["id"] = "buyer-edit-branches"
            ctx["buyer_edit_form"] = buyer_edit_form
            ctx["buyer_edit_rows"] = [
                {
                    "id": b.pk,
                    "name": b.name or "",
                    "contact_person": b.contact_person or "",
                    "phone": b.phone or "",
                    "email": b.email or "",
                    "address": b.address or "",
                    "branch_ids": list(b.branches.values_list("id", flat=True)),
                }
                for b in buyers
            ]
        return ctx


class BuyerQuickCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.add_buyer"

    def post(self, request):
        form = BuyerForm(data=request.POST, user=request.user)
        if not form.is_valid():
            errs = {name: [str(e) for e in items] for name, items in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errs}, status=400)
        buyer = form.save()
        return JsonResponse({"ok": True, "buyer": {"id": buyer.pk, "name": buyer.name}})


class BuyerQuickUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.change_buyer"

    def post(self, request, pk):
        buyer = get_object_or_404(
            filter_m2m_by_user_branches(Buyer.objects.all(), request.user),
            pk=pk,
        )
        form = BuyerForm(data=request.POST, instance=buyer, user=request.user)
        if not form.is_valid():
            errs = {name: [str(e) for e in items] for name, items in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errs}, status=400)
        form.save()
        return JsonResponse({"ok": True})


class BuyerQuickDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.delete_buyer"

    def post(self, request, pk):
        buyer = get_object_or_404(
            filter_m2m_by_user_branches(Buyer.objects.all(), request.user),
            pk=pk,
        )
        try:
            buyer.delete()
        except Exception:
            return JsonResponse(
                {
                    "ok": False,
                    "error": "Could not delete this buyer. It may still be used by dispatch or collection records.",
                },
                status=409,
            )
        return JsonResponse({"ok": True})


class BuyerDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = Buyer
    template_name = "masters/buyer_detail.html"
    context_object_name = "buyer"
    permission_required = "masters.view_buyer"

    def get_queryset(self):
        return filter_m2m_by_user_branches(Buyer.objects.prefetch_related("branches"), self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from dispatch.models import MilkDistribution
        from dispatch.buyer_billing import buyer_account_summary
        from dispatch.views import dispatch_list_totals

        base_qs = filter_by_user_branches(
            MilkDistribution.objects.filter(buyer=self.object).select_related(
                "branch", "buyer", "returned_branch"
            ),
            self.request.user,
            "branch_id",
        )

        date_from_raw = (self.request.GET.get("date_from") or "").strip()
        date_to_raw = (self.request.GET.get("date_to") or "").strip()
        date_from = parse_date(date_from_raw) if date_from_raw else None
        date_to = parse_date(date_to_raw) if date_to_raw else None
        selected_branch = (self.request.GET.get("branch") or "").strip()
        selected_status = (self.request.GET.get("status") or "").strip()
        selected_payment = (self.request.GET.get("payment_status") or "").strip()

        valid_status = {c[0] for c in MilkDistribution.DistributionStatus.choices}
        valid_payment = {c[0] for c in MilkDistribution.DistributionPaymentStatus.choices}

        period_qs = base_qs
        if date_from:
            period_qs = period_qs.filter(date__gte=date_from)
        if date_to:
            period_qs = period_qs.filter(date__lte=date_to)
        if selected_branch.isdigit():
            period_qs = period_qs.filter(branch_id=int(selected_branch))

        payment_counts = {
            row["payment_status"]: row["c"]
            for row in period_qs.values("payment_status").annotate(c=Count("id"))
        }
        status_counts = {
            row["status"]: row["c"]
            for row in period_qs.values("status").annotate(c=Count("id"))
        }

        dispatch_qs = period_qs
        if selected_status in valid_status:
            dispatch_qs = dispatch_qs.filter(status=selected_status)
        if selected_payment in valid_payment:
            dispatch_qs = dispatch_qs.filter(payment_status=selected_payment)

        summary = dispatch_qs.aggregate(
            total_entries=Count("id"),
            total_kg=Sum("kg"),
            latest_date=Max("date"),
        )
        ctx["buyer_dispatch"] = {
            "total_entries": summary["total_entries"] or 0,
            "total_kg": summary["total_kg"] or 0,
            "latest_date": summary["latest_date"],
        }
        ctx["buyer_dispatch_rows"] = dispatch_qs.order_by("-date", "-id")

        dispatch_totals = dispatch_list_totals(dispatch_qs)
        amount_summary = dispatch_qs.aggregate(
            total_amount=Sum("total_amount"),
            total_paid=Sum("paid_amount"),
        )
        dispatch_totals["total_amount"] = amount_summary["total_amount"] or Decimal("0.00")
        dispatch_totals["total_paid"] = amount_summary["total_paid"] or Decimal("0.00")
        delivered_kg = Decimal("0.00")
        delivered_liters = Decimal("0.00")
        variation_kg = Decimal("0.00")
        variation_liters = Decimal("0.00")
        delivered_count = 0
        for row in dispatch_qs:
            if row.delivered_quantity is None:
                continue
            delivered_count += 1
            delivered_kg += row.delivered_quantity
            delivered_liters += row.delivered_liters or Decimal("0.00")
            variation_kg += row.qty_variation_kg or Decimal("0.00")
            variation_liters += row.qty_variation_liters or Decimal("0.00")
        dispatch_totals["delivered_kg"] = delivered_kg
        dispatch_totals["delivered_liters"] = delivered_liters
        dispatch_totals["variation_kg"] = variation_kg
        dispatch_totals["variation_liters"] = variation_liters
        dispatch_totals["has_delivered"] = delivered_count > 0
        ctx["buyer_dispatch_totals"] = dispatch_totals

        period_active = bool(date_from or date_to or selected_branch or selected_status or selected_payment)
        ctx["dispatch_filters"] = {
            "date_from": date_from.isoformat() if date_from else "",
            "date_to": date_to.isoformat() if date_to else "",
            "branch": selected_branch if selected_branch.isdigit() else "",
            "status": selected_status if selected_status in valid_status else "",
            "payment_status": selected_payment if selected_payment in valid_payment else "",
            "period_active": period_active,
            "period_count": period_qs.count(),
            "payment_counts": {
                "all": period_qs.count(),
                "pending": payment_counts.get("pending", 0),
                "partial": payment_counts.get("partial", 0),
                "paid": payment_counts.get("paid", 0),
            },
            "status_counts": {
                "all": period_qs.count(),
                "pending": status_counts.get("pending", 0),
                "completed": status_counts.get("completed", 0),
                "return": status_counts.get("return", 0),
                "cancelled": status_counts.get("cancelled", 0),
            },
        }
        ctx["status_choices"] = MilkDistribution.DistributionStatus.choices
        ctx["payment_status_choices"] = MilkDistribution.DistributionPaymentStatus.choices
        ctx["buyer_dispatch_branches"] = get_allowed_branches_qs(self.request.user).order_by("name")
        ctx["return_branches"] = ctx["buyer_dispatch_branches"]
        if self.request.user.has_perm("masters.change_buyer"):
            ctx["buyer_edit_form"] = BuyerForm(instance=self.object, user=self.request.user)

        rate_form = kwargs.get("rate_form")
        if rate_form is None:
            rate_form = BuyerRateUpdateForm(
                initial={
                    "rate": self.object.rate,
                    "rate_unit": self.object.rate_unit,
                }
            )
        ctx["rate_form"] = rate_form
        history = list(
            BuyerRateHistory.objects.filter(buyer=self.object).order_by("-effective_from", "-id")[:50]
        )
        ctx["buyer_rate_history"] = annotate_rate_history_windows(history)
        try:
            ctx["buyer_account_summary"] = buyer_account_summary(self.object)
        except Exception:
            ctx["buyer_account_summary"] = None
        return ctx


def _apply_buyer_rate(buyer, *, rate, rate_unit, effective_from=None):
    """Update buyer rate/history and rebill unpaid dispatches. Returns (changed, effective_from)."""
    from django.utils import timezone
    from dispatch.models import MilkDistribution
    from dispatch.buyer_billing import sync_dispatch_billing

    if effective_from is None:
        effective_from = timezone.localdate()
    changed = buyer.rate != rate or (buyer.rate_unit or "") != (rate_unit or "")
    if changed:
        buyer.rate = rate
        buyer.rate_unit = rate_unit
        buyer._rate_effective_from = effective_from
        buyer.save()
        unpaid = MilkDistribution.objects.filter(buyer=buyer, paid_amount=0).exclude(
            status=MilkDistribution.DistributionStatus.CANCELLED
        )
        for dist in unpaid.iterator():
            sync_dispatch_billing(dist)
    return changed, effective_from


class BuyerRateUpdateView(LoginRequiredMixin, AnyPermissionRequiredMixin, View):
    permission_required = ("masters.change_buyerrate", "masters.change_buyer")

    def post(self, request, pk):
        buyer = get_object_or_404(
            filter_m2m_by_user_branches(Buyer.objects.all(), request.user),
            pk=pk,
        )
        form = BuyerRateUpdateForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Please enter a valid buyer rate.")
            detail_view = BuyerDetailView()
            detail_view.request = request
            detail_view.object = buyer
            detail_view.kwargs = {"pk": buyer.pk}
            context = detail_view.get_context_data(rate_form=form, object=buyer)
            return render(request, "masters/buyer_detail.html", context, status=400)

        effective_from = form.cleaned_data["effective_from"]
        _apply_buyer_rate(
            buyer,
            rate=form.cleaned_data["rate"],
            rate_unit=form.cleaned_data["rate_unit"],
            effective_from=effective_from,
        )
        messages.success(
            request,
            f"Buyer rate updated to {format_money(buyer.rate)} "
            f"{buyer.get_rate_unit_display().lower()} from {effective_from}.",
        )
        return redirect("buyer-detail", pk=buyer.pk)


class BuyerRateCellSaveView(LoginRequiredMixin, AnyPermissionRequiredMixin, View):
    """Inline rate save from the buyers list (JSON)."""

    permission_required = ("masters.change_buyerrate", "masters.change_buyer")

    def post(self, request):
        buyer_id = (request.POST.get("buyer_id") or "").strip()
        if not buyer_id.isdigit():
            return JsonResponse({"ok": False, "error": "Buyer is required."}, status=400)

        buyer = get_object_or_404(
            filter_m2m_by_user_branches(Buyer.objects.all(), request.user),
            pk=int(buyer_id),
        )
        rate_raw = (request.POST.get("rate") or "").replace(",", "").strip()
        rate_unit = (request.POST.get("rate_unit") or "").strip().lower()
        if rate_unit not in {Buyer.RateUnit.LITER, Buyer.RateUnit.KG}:
            return JsonResponse({"ok": False, "error": "Choose liter or kg."}, status=400)
        try:
            rate_val = Decimal(rate_raw)
        except (InvalidOperation, TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "Enter a valid rate."}, status=400)
        if rate_val < 0:
            return JsonResponse({"ok": False, "error": "Rate cannot be negative."}, status=400)

        changed, effective_from = _apply_buyer_rate(
            buyer, rate=rate_val, rate_unit=rate_unit
        )
        unit_short = buyer.get_rate_unit_display().replace("Per ", "").lower()
        return JsonResponse(
            {
                "ok": True,
                "buyer_id": buyer.pk,
                "rate": str(buyer.rate),
                "rate_display": format_money(buyer.rate),
                "rate_unit": buyer.rate_unit,
                "rate_unit_label": unit_short,
                "effective_from": effective_from.isoformat(),
                "saved": changed,
            }
        )


class BuyerRateHistoryDeleteView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Superuser-only delete of a buyer rate history row."""

    def test_func(self):
        return bool(self.request.user and self.request.user.is_superuser)

    def post(self, request, buyer_pk, rate_pk):
        buyer = get_object_or_404(
            filter_m2m_by_user_branches(Buyer.objects.all(), request.user),
            pk=buyer_pk,
        )
        rate_row = get_object_or_404(BuyerRateHistory, pk=rate_pk, buyer=buyer)
        rate_row.delete()

        latest = (
            BuyerRateHistory.objects.filter(buyer=buyer)
            .order_by("-effective_from", "-id")
            .first()
        )
        if latest:
            Buyer.objects.filter(pk=buyer.pk).update(
                rate=latest.rate, rate_unit=latest.rate_unit
            )
            buyer.refresh_from_db(fields=["rate", "rate_unit"])
        else:
            Buyer.objects.filter(pk=buyer.pk).update(
                rate=Decimal("0.00"), rate_unit=Buyer.RateUnit.LITER
            )
            buyer.refresh_from_db(fields=["rate", "rate_unit"])

        from dispatch.models import MilkDistribution
        from dispatch.buyer_billing import sync_dispatch_billing

        unpaid = MilkDistribution.objects.filter(buyer=buyer, paid_amount=0).exclude(
            status=MilkDistribution.DistributionStatus.CANCELLED
        )
        for dist in unpaid.iterator():
            sync_dispatch_billing(dist)

        messages.success(request, "Buyer rate history row deleted.")
        return redirect("buyer-detail", pk=buyer.pk)


class BuyerAccountView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = Buyer
    template_name = "masters/buyer_account.html"
    context_object_name = "buyer"
    permission_required = "masters.view_buyer"

    def get_queryset(self):
        return filter_m2m_by_user_branches(Buyer.objects.prefetch_related("branches"), self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from dispatch.buyer_billing import billed_dispatches_qs, buyer_account_summary, outstanding_dispatches_qs
        from dispatch.models import BuyerPayment, BuyerTransaction, MilkDistribution
        from dispatch.views import dispatch_qty_totals

        ctx["account_summary"] = buyer_account_summary(self.object)
        qty_qs = filter_by_user_branches(
            MilkDistribution.objects.filter(buyer=self.object),
            self.request.user,
            "branch_id",
        )
        ctx["buyer_dispatch_totals"] = dispatch_qty_totals(qty_qs)

        date_from_raw = (self.request.GET.get("date_from") or "").strip()
        date_to_raw = (self.request.GET.get("date_to") or "").strip()
        date_from = parse_date(date_from_raw) if date_from_raw else None
        date_to = parse_date(date_to_raw) if date_to_raw else None
        selected_branch = (self.request.GET.get("branch") or "").strip()
        pay_status = (self.request.GET.get("pay_status") or "outstanding").strip().lower()
        if pay_status not in {"all", "outstanding", "partial", "paid"}:
            pay_status = "outstanding"

        billed_qs = billed_dispatches_qs(self.object)
        outstanding_base_qs = outstanding_dispatches_qs(self.object)
        partial_base_qs = billed_qs.filter(paid_amount__gt=0, total_amount__gt=F("paid_amount"))
        paid_base_qs = billed_qs.filter(paid_amount__gte=F("total_amount"))

        dispatch_qs = billed_qs
        if pay_status == "outstanding":
            dispatch_qs = outstanding_base_qs
        elif pay_status == "partial":
            dispatch_qs = partial_base_qs
        elif pay_status == "paid":
            dispatch_qs = paid_base_qs

        if date_from:
            dispatch_qs = dispatch_qs.filter(date__gte=date_from)
        if date_to:
            dispatch_qs = dispatch_qs.filter(date__lte=date_to)
        if selected_branch.isdigit():
            dispatch_qs = dispatch_qs.filter(branch_id=int(selected_branch))

        dispatches = list(dispatch_qs)
        ctx["outstanding_dispatches"] = dispatches
        ctx["has_allocatable_dispatches"] = any((d.outstanding_amount or 0) > 0 for d in dispatches)
        period_active = bool(date_from or date_to or selected_branch)
        pay_q = self.request.GET.copy()
        chip_urls = {}
        for key in ("outstanding", "partial", "paid", "all"):
            q = pay_q.copy()
            q.pop("page", None)
            if key == "outstanding":
                q.pop("pay_status", None)
            else:
                q["pay_status"] = key
            chip_urls[key] = q.urlencode()
        ctx["account_filters"] = {
            "date_from": date_from.isoformat() if date_from else "",
            "date_to": date_to.isoformat() if date_to else "",
            "branch": selected_branch if selected_branch.isdigit() else "",
            "pay_status": pay_status,
            "period_active": period_active,
            "count": len(dispatches),
            "all_count": billed_qs.count(),
            "outstanding_count": outstanding_base_qs.count(),
            "partial_count": partial_base_qs.count(),
            "paid_count": paid_base_qs.count(),
            "chip_urls": chip_urls,
        }
        ctx["account_branch_options"] = get_allowed_branches_qs(self.request.user).order_by("name")

        ledger_from_raw = (self.request.GET.get("ledger_from") or "").strip()
        ledger_to_raw = (self.request.GET.get("ledger_to") or "").strip()
        ledger_from = parse_date(ledger_from_raw) if ledger_from_raw else None
        ledger_to = parse_date(ledger_to_raw) if ledger_to_raw else None
        ledger_type = (self.request.GET.get("ledger_type") or "").strip().lower()
        if ledger_type not in {"credit", "debit"}:
            ledger_type = ""

        base_ledger_qs = BuyerTransaction.objects.filter(buyer=self.object)
        period_ledger_qs = base_ledger_qs
        if ledger_from:
            period_ledger_qs = period_ledger_qs.filter(date__date__gte=ledger_from)
        if ledger_to:
            period_ledger_qs = period_ledger_qs.filter(date__date__lte=ledger_to)
        ledger_qs = period_ledger_qs
        if ledger_type:
            ledger_qs = ledger_qs.filter(transaction_type=ledger_type)

        type_counts = {
            row["transaction_type"]: row["c"]
            for row in period_ledger_qs.values("transaction_type").annotate(c=Count("id"))
        }

        def _signed_totals(qs):
            totals = {"credit": Decimal("0.00"), "debit": Decimal("0.00")}
            for row in qs.values("transaction_type").annotate(total=Sum("amount")):
                key = row["transaction_type"]
                if key in totals:
                    totals[key] = row["total"] or Decimal("0.00")
            return totals

        # Opening = account position before the period (full history before ledger_from).
        prior_totals = _signed_totals(
            base_ledger_qs.filter(date__date__lt=ledger_from) if ledger_from else base_ledger_qs.none()
        )
        opening_balance = prior_totals["credit"] - prior_totals["debit"]

        # Balance-after each row from full history so the column stays correct under type filters.
        chronological = list(base_ledger_qs.order_by("date", "id"))
        running = Decimal("0.00")
        balance_by_id = {}
        for tx in chronological:
            if tx.transaction_type == "credit":
                running += tx.amount
            else:
                running -= tx.amount
            balance_by_id[tx.pk] = running

        period_totals = _signed_totals(period_ledger_qs)
        total_credit = period_totals["credit"]
        total_debit = period_totals["debit"]
        closing_balance = opening_balance + total_credit - total_debit

        # Oldest → newest so Balance is checkable top-to-bottom (prev ± amount = this).
        # Keep the most recent 500 when the filtered set is larger.
        chrono_rows = list(ledger_qs.order_by("date", "id"))
        truncated = len(chrono_rows) > 500
        if truncated:
            chrono_rows = chrono_rows[-500:]
        ledger_rows = chrono_rows
        window_opening = opening_balance
        if ledger_rows:
            first_balance = balance_by_id.get(ledger_rows[0].pk, Decimal("0.00"))
            first_signed = (
                ledger_rows[0].amount
                if ledger_rows[0].transaction_type == "credit"
                else -ledger_rows[0].amount
            )
            window_opening = first_balance - first_signed
        for tx in ledger_rows:
            tx.running_balance = balance_by_id.get(tx.pk, Decimal("0.00"))
            # Signed amount for export/sort: credit +, debit −
            tx.signed_amount = tx.amount if tx.transaction_type == "credit" else -tx.amount

        ctx["ledger_rows"] = ledger_rows
        ctx["ledger_totals"] = {
            "opening": window_opening,
            "credit": total_credit,
            "debit": total_debit,
            "closing": closing_balance,
            "has_opening": bool(ledger_rows) and (bool(ledger_from) or truncated),
        }
        ctx["ledger_filters"] = {
            "date_from": ledger_from.isoformat() if ledger_from else "",
            "date_to": ledger_to.isoformat() if ledger_to else "",
            "type": ledger_type,
            "period_active": bool(ledger_from or ledger_to or ledger_type),
            "count": ledger_qs.count(),
            "all_count": base_ledger_qs.count(),
            "credit_count": type_counts.get("credit", 0),
            "debit_count": type_counts.get("debit", 0),
            "period_count": period_ledger_qs.count(),
        }

        ctx["payment_rows"] = (
            BuyerPayment.objects.filter(buyer=self.object)
            .prefetch_related("allocations__distribution")
            .select_related("created_by")
            .order_by("-date", "-id")[:50]
        )
        payment_form = kwargs.get("payment_form")
        if payment_form is None:
            payment_form = BuyerPaymentReceiveForm()
        ctx["payment_form"] = payment_form
        ctx["can_receive_payment"] = self.request.user.has_perm("dispatch.add_buyerpayment") or (
            self.request.user.has_perm("dispatch.change_milkdistribution")
        )
        return ctx


class BuyerPaymentReceiveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "dispatch.change_milkdistribution"

    def post(self, request, pk):
        from django.core.exceptions import ValidationError
        from dispatch.buyer_billing import record_buyer_payment

        buyer = get_object_or_404(
            filter_m2m_by_user_branches(Buyer.objects.all(), request.user),
            pk=pk,
        )
        form = BuyerPaymentReceiveForm(request.POST)
        selected = request.POST.getlist("distribution_ids")
        use_explicit = (request.POST.get("allocation_mode") or "").strip() == "explicit"
        allocation_amounts = {}
        if use_explicit:
            for dist_id in selected:
                raw = (request.POST.get(f"alloc_{dist_id}") or "").replace(",", "").strip()
                if not raw:
                    continue
                try:
                    allocation_amounts[int(dist_id)] = Decimal(raw)
                except (InvalidOperation, ValueError):
                    messages.error(request, "Invalid allocation amount.")
                    return redirect("buyer-account", pk=buyer.pk)

        if not form.is_valid():
            messages.error(request, "Please enter a valid payment date and amount.")
            account_view = BuyerAccountView()
            account_view.request = request
            account_view.object = buyer
            account_view.kwargs = {"pk": buyer.pk}
            context = account_view.get_context_data(payment_form=form, object=buyer)
            return render(request, "masters/buyer_account.html", context, status=400)

        if not selected:
            messages.error(request, "Select at least one dispatch to apply this payment.")
            return redirect("buyer-account", pk=buyer.pk)

        try:
            payment = record_buyer_payment(
                buyer=buyer,
                amount=form.cleaned_data["amount"],
                date=form.cleaned_data["date"],
                reference=form.cleaned_data.get("reference") or "",
                note=form.cleaned_data.get("note") or "",
                created_by=request.user,
                distribution_ids=selected,
                allocation_amounts=allocation_amounts if use_explicit else None,
            )
        except ValidationError as exc:
            if hasattr(exc, "message_dict"):
                msg = "; ".join(
                    f"{k}: {', '.join(v)}" if isinstance(v, (list, tuple)) else f"{k}: {v}"
                    for k, v in exc.message_dict.items()
                )
            else:
                msg = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            messages.error(request, msg)
            return redirect("buyer-account", pk=buyer.pk)

        messages.success(
            request,
            f"Payment of {format_money(payment.amount)} recorded "
            f"across {payment.allocations.count()} dispatch(es).",
        )
        return redirect("buyer-account", pk=buyer.pk)


class BuyerCreateView(LoginRequiredMixin, PermissionRequiredMixin, ModelFormPageTitleMixin, CreateView):
    permission_required = 'masters.add_buyer'
    model = Buyer
    form_class = BuyerForm
    template_name = 'masters/form.html'
    success_url = reverse_lazy('buyer-list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

class BuyerUpdateView(LoginRequiredMixin, PermissionRequiredMixin, ModelFormPageTitleMixin, UpdateView):
    permission_required = 'masters.change_buyer'
    model = Buyer
    form_class = BuyerForm
    template_name = 'masters/form.html'
    success_url = reverse_lazy('buyer-list')

    def get_queryset(self):
        return filter_m2m_by_user_branches(Buyer.objects.all(), self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs


class BuyerDeleteView(LoginRequiredMixin, PermissionRequiredMixin, RedirectGetDeleteMixin, DeleteView):
    model = Buyer
    success_url = reverse_lazy("buyer-list")
    permission_required = "masters.delete_buyer"

    def get_queryset(self):
        return filter_m2m_by_user_branches(Buyer.objects.all(), self.request.user)

class FarmerListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = Farmer
    template_name = 'masters/farmer_list.html'
    permission_required = "masters.view_farmer"

    def get_queryset(self):
        today = date.today()
        qs = filter_by_user_branches(
            super().get_queryset().select_related("route", "collection_point", "branch"),
            self.request.user,
            "branch_id",
        )
        return annotate_farmer_list_queryset(qs, today=today)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = date.today()
        farmers = list(ctx.get("object_list", []))
        status_counts = summarize_farmer_list_rows(farmers, today=today)
        avg_daily_milk = bulk_farmer_avg_daily_milk_kg([f.pk for f in farmers])
        for farmer in farmers:
            farmer.avg_daily_milk_kg = avg_daily_milk.get(farmer.pk)
        ctx["farmer_summary"] = {
            "count": len(farmers),
            **status_counts,
        }
        branch_ids = list(get_allowed_branches_qs(self.request.user).values_list("id", flat=True))
        ctx["farmer_branch_options"] = get_allowed_branches_qs(self.request.user).order_by("code")
        routes_qs = filter_by_user_branches(
            Route.objects.select_related("branch").order_by("name"),
            self.request.user,
            "branch_id",
        )
        ctx["farmer_filter_routes"] = [
            {"id": route.pk, "branch_id": route.branch_id, "label": route.name}
            for route in routes_qs
        ]
        allowed_points_qs = CollectionPoint.objects.select_related("route", "route__branch").order_by(
            "route__branch__code", "route__code", "number"
        )
        if not self.request.user.is_superuser:
            allowed_points_qs = (
                allowed_points_qs.filter(route__branch_id__in=branch_ids) if branch_ids else allowed_points_qs.none()
            )
        ctx["farmer_filter_points"] = [
            {
                "id": p.pk,
                "branch_id": p.route.branch_id if p.route_id else None,
                "route_id": p.route_id,
                "label": f"{p.number} - {p.name}",
                "route_display": p.route.name if p.route_id else "",
            }
            for p in allowed_points_qs
        ]
        can_view_account = self.request.user.has_perm("reports.view_farmerpaymentsheet")
        ctx["can_view_farmer_account"] = can_view_account
        if can_view_account and farmers:
            account_map = build_farmer_account_summaries(
                [f.pk for f in farmers],
                branch_ids,
                today=today,
            )
            for farmer in farmers:
                farmer.account_summary = account_map.get(farmer.pk)
        else:
            account_map = {}
            for farmer in farmers:
                farmer.account_summary = None
        ctx["farmer_account_by_id"] = account_map
        if self.request.user.has_perm("masters.add_farmer") or self.request.user.has_perm("masters.change_farmer"):
            ctx["farmer_create_form"] = FarmerForm(user=self.request.user)
            ctx["farmer_point_rows"] = [
                {
                    "id": p["id"],
                    "branch_id": p["branch_id"],
                    "route_id": p["route_id"],
                    "label": p["label"],
                    "route_display": p["route_display"],
                }
                for p in ctx["farmer_filter_points"]
            ]
        if self.request.user.has_perm("masters.change_farmer"):
            ctx["farmer_resign_form"] = FarmerResignationForm()
            ctx["farmer_edit_rows"] = [
                {
                    "id": f.pk,
                    "registration_number": f.registration_number or "",
                    "full_name": f.full_name or "",
                    "initial": f.initial or "",
                    "common_name": f.common_name or "",
                    "branch_id": f.branch_id or "",
                    "collection_point_id": f.collection_point_id or "",
                    "nic": f.nic or "",
                    "date_of_birth": f.date_of_birth.isoformat() if f.date_of_birth else "",
                    "mobile": f.mobile or "",
                    "whatsapp": f.whatsapp or "",
                    "email": f.email or "",
                    "address": f.address or "",
                }
                for f in self.get_queryset()
            ]
        return ctx


class FarmerBulkRateUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.bulk_update_farmerrate"
    template_name = "masters/farmer_bulk_rate_update.html"

    @staticmethod
    def _parse_int(raw):
        if raw is None:
            return None
        text = str(raw).strip()
        if not text.isdigit():
            return None
        return int(text)

    def _selected_filters(self, request, source="GET"):
        bag = request.GET if source == "GET" else request.POST
        return {
            "branch_id": self._parse_int(bag.get("branch")),
            "route_id": self._parse_int(bag.get("route")),
            "collection_point_id": self._parse_int(bag.get("collection_point")),
        }

    def _get_farmers(self, request):
        qs = filter_by_user_branches(
            Farmer.objects.select_related("branch", "route", "collection_point").order_by("common_name", "full_name"),
            request.user,
            "branch_id",
        )
        return list(qs)

    def _filter_options(self, request):
        branches_qs = get_allowed_branches_qs(request.user).order_by("name")
        branch_ids = list(branches_qs.values_list("id", flat=True))
        routes_qs = Route.objects.filter(branch_id__in=branch_ids).order_by("name")
        points_qs = (
            CollectionPoint.objects.select_related("route")
            .filter(route_id__in=routes_qs.values_list("id", flat=True))
            .order_by("route__name", "number", "name")
        )
        return {
            "branches": branches_qs,
            "bulk_rate_filter_routes": [
                {"id": route.pk, "branch_id": route.branch_id, "label": route.name}
                for route in routes_qs
            ],
            "bulk_rate_filter_points": [
                {
                    "id": point.pk,
                    "branch_id": point.route.branch_id if point.route_id else None,
                    "route_id": point.route_id,
                    "label": f"{point.number} — {point.name}",
                }
                for point in points_qs
            ],
        }

    @staticmethod
    def _build_summary(farmers):
        count = len(farmers)
        supply_vals = [f.avg_daily_milk_kg for f in farmers if f.avg_daily_milk_kg is not None]
        avg_supply = sum(supply_vals) / len(supply_vals) if supply_vals else None
        paid_yes = sum(1 for f in farmers if (f.apply_rate_paid or "") == "yes")
        return {
            "count": count,
            "avg_supply": avg_supply,
            "paid_yes": paid_yes,
        }

    def get(self, request):
        farmers = self._get_farmers(request)
        avg_daily_milk = bulk_farmer_avg_daily_milk_kg([f.pk for f in farmers])
        for farmer in farmers:
            farmer.avg_daily_milk_kg = avg_daily_milk.get(farmer.pk)
        ctx = {
            "farmers": farmers,
            "summary": self._build_summary(farmers),
        }
        ctx.update(self._filter_options(request))
        return render(request, self.template_name, ctx)


class FarmerBulkRateCellSaveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.bulk_update_farmerrate"

    def post(self, request):
        farmer_id = self._parse_int(request.POST.get("farmer_id"))
        if not farmer_id:
            return JsonResponse({"ok": False, "error": "Farmer is required."}, status=400)

        rate_raw = (request.POST.get("rate") or "").strip()
        apply_rate_paid = (request.POST.get("apply_rate_paid") or "yes").strip().lower()

        farmer_qs = filter_by_user_branches(Farmer.objects.filter(pk=farmer_id), request.user, "branch_id")
        farmer = get_object_or_404(farmer_qs, pk=farmer_id)

        try:
            rate_val = Decimal(rate_raw)
        except (InvalidOperation, TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "Enter a valid rate."}, status=400)
        if rate_val <= 0:
            return JsonResponse({"ok": False, "error": "Rate must be greater than zero."}, status=400)
        if apply_rate_paid not in ("yes", "no"):
            return JsonResponse({"ok": False, "error": "Choose Yes or No for Apply Rate Paid."}, status=400)

        changed = farmer.rate != rate_val or (farmer.apply_rate_paid or "") != apply_rate_paid
        if changed:
            farmer.rate = rate_val
            farmer.apply_rate_paid = apply_rate_paid
            farmer.save(update_fields=["rate", "apply_rate_paid"])

        return JsonResponse(
            {
                "ok": True,
                "farmer_id": farmer.pk,
                "rate": str(farmer.rate),
                "rate_display": format_money(farmer.rate),
                "apply_rate_paid": farmer.apply_rate_paid,
                "saved": changed,
            }
        )

    @staticmethod
    def _parse_int(raw):
        if raw is None:
            return None
        text = str(raw).strip()
        if not text.isdigit():
            return None
        return int(text)


class FarmerQuickCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.add_farmer"

    def post(self, request):
        form = FarmerForm(data=request.POST, user=request.user)
        if not form.is_valid():
            errs = {name: [str(e) for e in items] for name, items in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errs}, status=400)
        farmer = form.save()
        return JsonResponse({"ok": True, "farmer": {"id": farmer.pk}})


class FarmerQuickUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.change_farmer"

    def post(self, request, pk):
        farmer_qs = filter_by_user_branches(Farmer.objects.filter(pk=pk), request.user, "branch_id")
        farmer = get_object_or_404(farmer_qs)
        form = FarmerForm(data=request.POST, instance=farmer, user=request.user)
        if not form.is_valid():
            errs = {name: [str(e) for e in items] for name, items in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errs}, status=400)
        form.save()
        return JsonResponse({"ok": True})


class FarmerResignView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.change_farmer"

    def post(self, request, pk):
        farmer_qs = filter_by_user_branches(Farmer.objects.filter(pk=pk), request.user, "branch_id")
        farmer = get_object_or_404(farmer_qs)
        if farmer.status == Farmer.Status.RESIGNED:
            return JsonResponse({"ok": False, "error": "This farmer is already marked as resigned."}, status=400)
        form = FarmerResignationForm(data=request.POST)
        if not form.is_valid():
            errs = {name: [str(e) for e in items] for name, items in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errs}, status=400)
        farmer.status = Farmer.Status.RESIGNED
        farmer.resigned_at = form.cleaned_data["resigned_at"]
        farmer.resignation_note = form.cleaned_data.get("resignation_note") or ""
        farmer.resigned_by = request.user
        farmer.save(update_fields=["status", "resigned_at", "resignation_note", "resigned_by", "updated_at"])
        return JsonResponse({"ok": True})


class FarmerQuickDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.delete_farmer"

    def post(self, request, pk):
        farmer_qs = filter_by_user_branches(Farmer.objects.filter(pk=pk), request.user, "branch_id")
        farmer = get_object_or_404(farmer_qs)
        can_delete = request.user.is_superuser
        try:
            farmer.delete()
        except ProtectedError as exc:
            payload = format_farmer_delete_blocked_payload(farmer, exc, can_delete=can_delete)
            return JsonResponse({"ok": False, **payload}, status=409)
        except Exception:
            payload = format_farmer_delete_blocked_payload(farmer, can_delete=can_delete)
            return JsonResponse({"ok": False, **payload}, status=409)
        return JsonResponse({"ok": True, "redirect": reverse("farmer-list")})


class FarmerBlockerDeleteView(LoginRequiredMixin, View):
    """Superuser-only: delete one related record that blocks farmer deletion."""

    def post(self, request, pk):
        if not request.user.is_superuser:
            return JsonResponse(
                {"ok": False, "error": "Only superusers can delete related records here."},
                status=403,
            )
        farmer_qs = filter_by_user_branches(Farmer.objects.filter(pk=pk), request.user, "branch_id")
        farmer = get_object_or_404(farmer_qs)
        kind = (request.POST.get("kind") or "").strip()
        record_id_raw = (request.POST.get("id") or "").strip()
        if not record_id_raw.isdigit():
            return JsonResponse({"ok": False, "error": "Invalid related record."}, status=400)
        ok, message = delete_farmer_blocker_record(farmer, kind, int(record_id_raw))
        if not ok:
            return JsonResponse({"ok": False, "error": message}, status=409)
        payload = format_farmer_delete_blocked_payload(farmer, can_delete=True)
        return JsonResponse(
            {
                "ok": True,
                "message": message,
                "cleared": not payload["blockers"],
                **payload,
            }
        )


class FarmerImportExcelView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.add_farmer"

    @staticmethod
    def _clean_int(value):
        if value is None or str(value).strip() == "":
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _clean_text(value):
        if cell_missing(value):
            return ""
        return str(value).strip()

    @staticmethod
    def _clean_decimal(value):
        if cell_missing(value):
            return None
        text = str(value).strip()
        if text == "":
            return None
        try:
            return Decimal(text)
        except (InvalidOperation, TypeError, ValueError):
            return None

    def _pick_row_value(self, row, *keys):
        for key in keys:
            if key in row and not cell_missing(row.get(key)):
                return row.get(key)
        return None

    def _resolve_collection_point_id(self, row):
        raw_number = self._clean_text(
            self._pick_row_value(row, "collection_point_number", "point_number")
        )
        if raw_number:
            by_number = CollectionPoint.objects.filter(number__iexact=raw_number).only("id")
            matches = by_number.count()
            if matches == 1:
                return by_number.first().id, ""
            if matches > 1:
                return (
                    None,
                    (
                        f"collection point number '{raw_number}' matches multiple points. "
                        "Use collection_point like 'number - name'."
                    ),
                )
            return None, f"collection point number '{raw_number}' not found."

        raw_id = self._pick_row_value(row, "collection_point_id", "point_id")
        point_id = self._clean_int(raw_id)
        if point_id:
            cp = CollectionPoint.objects.filter(pk=point_id).only("id").first()
            if cp:
                return cp.id, ""
            return None, f"collection point id '{point_id}' not found."

        raw_label = self._clean_text(
            self._pick_row_value(row, "collection_point", "point", "collection_point_label")
        )
        if not raw_label:
            return None, "collection point is missing."
        if "-" in raw_label:
            left, right = [p.strip() for p in raw_label.split("-", 1)]
            cp = CollectionPoint.objects.filter(number__iexact=left, name__iexact=right).only("id").first()
            if cp:
                return cp.id, ""
        cp = CollectionPoint.objects.filter(number__iexact=raw_label).only("id").first()
        if cp:
            return cp.id, ""
        cp = CollectionPoint.objects.filter(name__iexact=raw_label).only("id").first()
        if cp:
            return cp.id, ""
        return None, f"collection point '{raw_label}' not found."

    def post(self, request):
        if not request.user.is_superuser:
            messages.error(request, "Only superuser can import farmers from Excel.")
            return redirect("farmer-list")

        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, "Please select an Excel or CSV file.")
            return redirect("farmer-list")

        try:
            pd = get_pandas()
            if str(upload.name or "").lower().endswith(".csv"):
                df = pd.read_csv(upload)
            else:
                df = pd.read_excel(upload)
        except Exception:
            messages.error(request, "Could not read the file. Use a valid Excel/CSV file.")
            return redirect("farmer-list")

        created_count = 0
        updated_count = 0
        skipped_count = 0
        skipped_reasons = []

        for idx, row in df.iterrows():
            try:
                full_name = self._clean_text(row.get("full_name") or row.get("name"))
                common_name = self._clean_text(row.get("common_name"))
                collection_point_id, collection_point_err = self._resolve_collection_point_id(row)
                nic = self._clean_text(row.get("nic"))
                mobile = self._clean_text(row.get("mobile"))
                whatsapp = self._clean_text(row.get("whatsapp"))
                email = self._clean_text(row.get("email"))
                address = self._clean_text(row.get("address"))
                rate = self._clean_decimal(self._pick_row_value(row, "rate"))
                apply_rate_paid = self._clean_text(
                    self._pick_row_value(row, "apply_rate_paid")
                ).lower()

                if not full_name:
                    skipped_count += 1
                    if len(skipped_reasons) < 5:
                        skipped_reasons.append(f"Row {idx + 2}: full_name (or name) is empty.")
                    continue
                if not collection_point_id:
                    skipped_count += 1
                    if len(skipped_reasons) < 5:
                        skipped_reasons.append(f"Row {idx + 2}: {collection_point_err}")
                    continue
                point = (
                    CollectionPoint.objects.select_related("route", "route__branch")
                    .filter(pk=collection_point_id)
                    .first()
                )
                if not point:
                    skipped_count += 1
                    if len(skipped_reasons) < 5:
                        skipped_reasons.append(
                            f"Row {idx + 2}: collection point id '{collection_point_id}' not found."
                        )
                    continue
                if not point.route_id:
                    skipped_count += 1
                    if len(skipped_reasons) < 5:
                        skipped_reasons.append(
                            f"Row {idx + 2}: selected collection point has no route."
                        )
                    continue
                if not point.route or not point.route.branch_id:
                    skipped_count += 1
                    if len(skipped_reasons) < 5:
                        skipped_reasons.append(
                            f"Row {idx + 2}: selected collection point route has no branch."
                        )
                    continue
                if not common_name:
                    common_name = full_name
                if rate is None or rate <= 0:
                    skipped_count += 1
                    if len(skipped_reasons) < 5:
                        skipped_reasons.append(f"Row {idx + 2}: rate must be greater than zero.")
                    continue
                if apply_rate_paid not in ("yes", "no"):
                    skipped_count += 1
                    if len(skipped_reasons) < 5:
                        skipped_reasons.append(
                            f"Row {idx + 2}: apply_rate_paid must be 'yes' or 'no'."
                        )
                    continue

                farmer = None
                farmer = Farmer.objects.filter(
                    full_name=full_name,
                    collection_point_id=collection_point_id,
                ).first()

                if farmer is None:
                    Farmer.objects.create(
                        full_name=full_name,
                        common_name=common_name,
                        collection_point_id=collection_point_id,
                        route_id=point.route_id,
                        branch_id=point.route.branch_id,
                        rate=rate,
                        apply_rate_paid=apply_rate_paid,
                        nic=nic,
                        mobile=mobile,
                        whatsapp=whatsapp,
                        email=email,
                        address=address,
                    )
                    created_count += 1
                else:
                    farmer.full_name = full_name
                    farmer.common_name = common_name
                    farmer.collection_point_id = collection_point_id
                    farmer.route_id = point.route_id
                    farmer.branch_id = point.route.branch_id
                    farmer.rate = rate
                    farmer.apply_rate_paid = apply_rate_paid
                    farmer.nic = nic
                    farmer.mobile = mobile
                    farmer.whatsapp = whatsapp
                    farmer.email = email
                    farmer.address = address
                    farmer.save()
                    updated_count += 1
            except Exception as exc:
                skipped_count += 1
                if len(skipped_reasons) < 5:
                    detail = self._clean_text(str(exc)) or "invalid data."
                    skipped_reasons.append(f"Row {idx + 2}: {detail}")

        skip_note = f" Skipped: {skipped_count}."
        if skipped_reasons:
            skip_note += " Examples: " + " | ".join(skipped_reasons)
        messages.success(
            request,
            (
                f"Farmer import complete. Created: {created_count}, Updated: {updated_count}, "
                f"{skip_note} New farmers received auto-generated registration numbers."
            ),
        )
        return redirect("farmer-list")


class FarmerImportTemplateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.add_farmer"

    def get(self, request):
        if not request.user.is_superuser:
            messages.error(request, "Only superuser can download the farmer import template.")
            return redirect("farmer-list")

        columns = [
            "full_name",
            "common_name",
            "collection_point_number",
            "rate",
            "apply_rate_paid",
            "nic",
            "mobile",
            "whatsapp",
            "email",
            "address",
        ]
        sample_row = [
            "nimal perera",
            "nimal",
            "500",
            "175",
            "yes",
            "123456789V",
            "0712345678",
            "0771234567",
            "nimal@example.com",
            "Sample address",
        ]
        fmt = str(request.GET.get("format") or "csv").strip().lower()
        if fmt == "xlsx":
            response = HttpResponse(
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            response["Content-Disposition"] = 'attachment; filename="farmers_import_template.xlsx"'
            get_pandas().DataFrame([sample_row], columns=columns).to_excel(
                response, index=False, sheet_name="FarmersImport"
            )
            return response

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="farmers_import_template.csv"'
        writer = csv.writer(response)
        writer.writerow(columns)
        writer.writerow(sample_row)
        return response


class CollectionPointBankAccountImportExcelView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Superuser-only import of collection point bank accounts (template or CEFT-style file)."""

    permission_required = "masters.change_collectionpoint"

    @staticmethod
    def _clean_text(value):
        from reports.bank_ceft import cell_text

        return cell_text(value)

    @staticmethod
    def _truthy(value):
        text = str(value or "").strip().lower()
        return text in {"1", "true", "yes", "y", "primary"}

    def _resolve_company_branch(self, branch_value):
        text = (branch_value or "").strip()
        if not text:
            return None, ""
        from branches.models import Branch

        qs = Branch.objects.filter(Q(name__iexact=text) | Q(code__iexact=text))
        count = qs.count()
        if count == 1:
            return qs.first(), ""
        if count > 1:
            return None, f"branch '{text}' matches multiple branches."
        return None, f"branch '{text}' not found."

    def _resolve_point(self, point_number, point_label, company_branch=None):
        number = (point_number or "").strip()
        if not number and point_label:
            from reports.bank_ceft import extract_point_number_from_text

            number = extract_point_number_from_text(point_label)
        if not number:
            return None, "collection point number is missing."
        qs = CollectionPoint.objects.select_related("route", "route__branch").filter(
            number__iexact=number
        )
        if company_branch is not None:
            qs = qs.filter(route__branch=company_branch)
        count = qs.count()
        if count == 1:
            return qs.first(), ""
        if count > 1:
            if company_branch is None:
                return (
                    None,
                    f"point number '{number}' matches multiple collection points; "
                    "add a Branch column (dairy branch name/code).",
                )
            return (
                None,
                f"point number '{number}' matches multiple collection points in branch "
                f"'{company_branch.name}'.",
            )
        by_name = CollectionPoint.objects.select_related("route", "route__branch").filter(
            name__iexact=number
        )
        if company_branch is not None:
            by_name = by_name.filter(route__branch=company_branch)
        if by_name.count() == 1:
            return by_name.first(), ""
        if company_branch is not None:
            return None, f"collection point '{number}' not found in branch '{company_branch.name}'."
        return None, f"collection point '{number}' not found."

    def _bank_name_for_code(self, bank_code, bank_name):
        name = (bank_name or "").strip()
        code = (bank_code or "").strip()
        if name:
            return name
        if not code:
            return ""
        from .models import Bank

        bank = Bank.objects.filter(code__iexact=code, is_deleted=False).first()
        if bank:
            return bank.name
        return f"Bank {code}"

    def post(self, request):
        if not request.user.is_superuser:
            messages.error(request, "Only superuser can import collection point bank accounts.")
            return redirect("point-list")

        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, "Please select an Excel or CSV file.")
            return redirect("point-list")

        default_branch = None
        default_branch_raw = (request.POST.get("default_branch") or "").strip()
        if default_branch_raw:
            default_branch, default_branch_err = self._resolve_company_branch(default_branch_raw)
            if not default_branch:
                messages.error(request, f"Default branch: {default_branch_err}")
                return redirect("point-list")

        try:
            pd = get_pandas()
            name = str(upload.name or "").lower()
            if name.endswith(".csv"):
                df = pd.read_csv(upload, dtype=str, keep_default_na=False)
            elif name.endswith(".xls"):
                df = pd.read_excel(upload, dtype=str, keep_default_na=False, engine="xlrd")
            else:
                df = pd.read_excel(upload, dtype=str, keep_default_na=False)
        except Exception:
            messages.error(request, "Could not read the file. Use a valid Excel/CSV file.")
            return redirect("point-list")

        if df.empty:
            messages.error(request, "The file has no data rows.")
            return redirect("point-list")

        from reports.bank_ceft import detect_ceft_columns

        columns = [str(c) for c in df.columns.tolist()]
        mapping = detect_ceft_columns(columns)
        required_any = {"account_name", "account_number", "bank_code", "branch_code"}
        if not required_any.issubset(mapping.keys()) and not (
            "account_name" in mapping and "account_number" in mapping
        ):
            messages.error(
                request,
                "Unrecognized columns. Use the download template, or a CEFT bank file "
                "with Beneficiary Name / Bank Code / Branch Code / Account No.",
            )
            return redirect("point-list")

        created_count = 0
        updated_count = 0
        skipped_count = 0
        skipped_reasons = []

        for idx, row in df.iterrows():
            values = list(row.tolist())

            def col(key):
                if key not in mapping:
                    return ""
                return self._clean_text(values[mapping[key]])

            account_name = col("account_name")
            account_number = col("account_number")
            bank_code = "".join(ch for ch in col("bank_code") if ch.isalnum())
            branch_code = "".join(ch for ch in col("branch_code") if ch.isalnum())
            bank_branch = col("bank_branch") or branch_code
            bank_name = self._bank_name_for_code(bank_code, col("bank_name"))
            point_number = col("point_number")
            point_label = col("point_label")
            is_primary = self._truthy(col("is_primary")) if "is_primary" in mapping else True

            if not account_name and not account_number:
                continue
            if account_name.lower().startswith("beneficiary") or account_name.lower() == "p":
                if not account_number:
                    continue

            company_branch = default_branch
            row_branch = col("company_branch")
            if row_branch:
                company_branch, branch_err = self._resolve_company_branch(row_branch)
                if not company_branch:
                    skipped_count += 1
                    if len(skipped_reasons) < 8:
                        skipped_reasons.append(f"Row {idx + 2}: {branch_err}")
                    continue

            point, point_err = self._resolve_point(point_number, point_label, company_branch)
            if not point:
                skipped_count += 1
                if len(skipped_reasons) < 8:
                    skipped_reasons.append(f"Row {idx + 2}: {point_err}")
                continue
            if not account_name or not account_number:
                skipped_count += 1
                if len(skipped_reasons) < 8:
                    skipped_reasons.append(f"Row {idx + 2}: account name/number missing.")
                continue
            if not bank_code or not branch_code:
                skipped_count += 1
                if len(skipped_reasons) < 8:
                    skipped_reasons.append(
                        f"Row {idx + 2}: bank code and branch code are required."
                    )
                continue
            if not bank_name:
                bank_name = f"Bank {bank_code}"
            if not bank_branch:
                bank_branch = branch_code

            existing = (
                CollectionPointBankAccount.objects.filter(
                    collection_point=point,
                    account_number__iexact=account_number,
                )
                .order_by("-is_primary", "id")
                .first()
            )
            if existing:
                existing.account_name = account_name
                existing.bank_name = bank_name
                existing.bank_code = bank_code
                existing.bank_branch = bank_branch
                existing.branch_code = branch_code
                if is_primary:
                    existing.is_primary = True
                existing.save()
                updated_count += 1
            else:
                account = CollectionPointBankAccount(
                    collection_point=point,
                    account_name=account_name,
                    account_number=account_number,
                    bank_name=bank_name,
                    bank_code=bank_code,
                    bank_branch=bank_branch,
                    branch_code=branch_code,
                    is_primary=is_primary or not point.bank_accounts.exists(),
                )
                account.save()
                created_count += 1

        messages.success(
            request,
            f"Bank accounts import finished. Created {created_count}, updated {updated_count}, skipped {skipped_count}.",
        )
        if skipped_reasons:
            messages.warning(request, "Examples: " + " | ".join(skipped_reasons))
        return redirect("point-list")


class CollectionPointBankAccountImportTemplateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.change_collectionpoint"

    def get(self, request):
        if not request.user.is_superuser:
            messages.error(request, "Only superuser can download the bank account import template.")
            return redirect("point-list")

        columns = [
            "Branch",
            "point_number",
            "account_name",
            "account_number",
            "bank_name",
            "bank_code",
            "bank_branch",
            "branch_code",
            "is_primary",
        ]
        sample_row = [
            "Nawagaththegama",
            "401",
            "J A R S K JAYAKODI",
            "177010007036",
            "Hatton National Bank",
            "7083",
            "Nawagaththegama",
            "230",
            "yes",
        ]
        fmt = str(request.GET.get("format") or "xlsx").strip().lower()
        if fmt == "csv":
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = (
                'attachment; filename="collection_point_bank_accounts_import_template.csv"'
            )
            writer = csv.writer(response)
            writer.writerow(columns)
            writer.writerow(sample_row)
            return response

        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = (
            'attachment; filename="collection_point_bank_accounts_import_template.xlsx"'
        )
        get_pandas().DataFrame([sample_row], columns=columns).to_excel(
            response, index=False, sheet_name="PointBankAccounts"
        )
        return response


class FarmerBankAccountImportExcelView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Superuser-only import of farmer bank accounts."""

    permission_required = "masters.change_farmer"

    @staticmethod
    def _clean_text(value):
        from reports.bank_ceft import cell_text

        return cell_text(value)

    @staticmethod
    def _truthy(value):
        text = str(value or "").strip().lower()
        return text in {"1", "true", "yes", "y", "primary"}

    def _resolve_company_branch(self, branch_value):
        text = (branch_value or "").strip()
        if not text:
            return None, ""
        from branches.models import Branch

        qs = Branch.objects.filter(Q(name__iexact=text) | Q(code__iexact=text))
        count = qs.count()
        if count == 1:
            return qs.first(), ""
        if count > 1:
            return None, f"branch '{text}' matches multiple branches."
        return None, f"branch '{text}' not found."

    def _resolve_farmer(self, registration_number, farmer_label, farmer_id=None, company_branch=None):
        if farmer_id:
            try:
                pk = int(float(str(farmer_id).strip()))
            except (TypeError, ValueError):
                pk = None
            if pk:
                farmer = Farmer.objects.filter(pk=pk).first()
                if farmer:
                    if company_branch is not None and farmer.branch_id != company_branch.pk:
                        return (
                            None,
                            f"farmer id '{pk}' is not in branch '{company_branch.name}'.",
                        )
                    return farmer, ""
                return None, f"farmer id '{pk}' not found."

        reg = (registration_number or "").strip()
        if not reg and farmer_label:
            from reports.bank_ceft import extract_point_number_from_text

            reg = extract_point_number_from_text(farmer_label)
        if not reg:
            return None, "farmer registration number is missing."

        qs = Farmer.objects.filter(registration_number__iexact=reg)
        if company_branch is not None:
            qs = qs.filter(branch=company_branch)
        if qs.count() == 1:
            return qs.first(), ""
        if qs.count() > 1:
            if company_branch is None:
                return (
                    None,
                    f"registration '{reg}' matches multiple farmers; add a Branch column.",
                )
            return (
                None,
                f"registration '{reg}' matches multiple farmers in branch '{company_branch.name}'.",
            )

        by_common = Farmer.objects.filter(common_name__iexact=reg)
        by_full = Farmer.objects.filter(full_name__iexact=reg)
        if company_branch is not None:
            by_common = by_common.filter(branch=company_branch)
            by_full = by_full.filter(branch=company_branch)
        if by_common.count() == 1:
            return by_common.first(), ""
        if by_full.count() == 1:
            return by_full.first(), ""
        if company_branch is not None:
            return None, f"farmer '{reg}' not found in branch '{company_branch.name}'."
        return None, f"farmer '{reg}' not found."

    def _bank_name_for_code(self, bank_code, bank_name):
        name = (bank_name or "").strip()
        code = (bank_code or "").strip()
        if name:
            return name
        if not code:
            return ""
        from .models import Bank

        bank = Bank.objects.filter(code__iexact=code, is_deleted=False).first()
        if bank:
            return bank.name
        return f"Bank {code}"

    def post(self, request):
        if not request.user.is_superuser:
            messages.error(request, "Only superuser can import farmer bank accounts.")
            return redirect("farmer-list")

        upload = request.FILES.get("file")
        if not upload:
            messages.error(request, "Please select an Excel or CSV file.")
            return redirect("farmer-list")

        default_branch = None
        default_branch_raw = (request.POST.get("default_branch") or "").strip()
        if default_branch_raw:
            default_branch, default_branch_err = self._resolve_company_branch(default_branch_raw)
            if not default_branch:
                messages.error(request, f"Default branch: {default_branch_err}")
                return redirect("farmer-list")

        try:
            pd = get_pandas()
            name = str(upload.name or "").lower()
            if name.endswith(".csv"):
                df = pd.read_csv(upload, dtype=str, keep_default_na=False)
            elif name.endswith(".xls"):
                df = pd.read_excel(upload, dtype=str, keep_default_na=False, engine="xlrd")
            else:
                df = pd.read_excel(upload, dtype=str, keep_default_na=False)
        except Exception:
            messages.error(request, "Could not read the file. Use a valid Excel/CSV file.")
            return redirect("farmer-list")

        if df.empty:
            messages.error(request, "The file has no data rows.")
            return redirect("farmer-list")

        from reports.bank_ceft import detect_ceft_columns

        columns = [str(c) for c in df.columns.tolist()]
        mapping = detect_ceft_columns(columns)
        normalized = {re.sub(r"[^a-z0-9]+", "", c.lower()): i for i, c in enumerate(columns)}
        for key, aliases in {
            "registration_number": (
                "registrationnumber",
                "registration_number",
                "reg_no",
                "farmer_registration",
            ),
            "farmer_id": ("farmerid", "farmer_id", "id"),
            "farmer_label": ("farmer", "farmername", "common_name", "full_name"),
        }.items():
            if key in mapping:
                continue
            for alias in aliases:
                if alias in normalized:
                    mapping[key] = normalized[alias]
                    break

        if "account_name" not in mapping or "account_number" not in mapping:
            messages.error(
                request,
                "Unrecognized columns. Use the download template, or a CEFT bank file "
                "with Beneficiary Name / Bank Code / Branch Code / Account No.",
            )
            return redirect("farmer-list")

        created_count = 0
        updated_count = 0
        skipped_count = 0
        skipped_reasons = []

        for idx, row in df.iterrows():
            values = list(row.tolist())

            def col(key):
                if key not in mapping:
                    return ""
                return self._clean_text(values[mapping[key]])

            account_name = col("account_name")
            account_number = col("account_number")
            bank_code = "".join(ch for ch in col("bank_code") if ch.isalnum())
            branch_code = "".join(ch for ch in col("branch_code") if ch.isalnum())
            bank_branch = col("bank_branch") or branch_code
            bank_name = self._bank_name_for_code(bank_code, col("bank_name"))
            registration_number = col("registration_number") or col("point_number")
            farmer_label = col("farmer_label") or col("point_label")
            farmer_id = col("farmer_id")
            is_primary = self._truthy(col("is_primary")) if "is_primary" in mapping else True

            if not account_name and not account_number:
                continue

            company_branch = default_branch
            row_branch = col("company_branch")
            if row_branch:
                company_branch, branch_err = self._resolve_company_branch(row_branch)
                if not company_branch:
                    skipped_count += 1
                    if len(skipped_reasons) < 8:
                        skipped_reasons.append(f"Row {idx + 2}: {branch_err}")
                    continue

            farmer, farmer_err = self._resolve_farmer(
                registration_number, farmer_label, farmer_id, company_branch
            )
            if not farmer:
                skipped_count += 1
                if len(skipped_reasons) < 8:
                    skipped_reasons.append(f"Row {idx + 2}: {farmer_err}")
                continue
            if not account_name or not account_number:
                skipped_count += 1
                if len(skipped_reasons) < 8:
                    skipped_reasons.append(f"Row {idx + 2}: account name/number missing.")
                continue
            if not bank_code or not branch_code:
                skipped_count += 1
                if len(skipped_reasons) < 8:
                    skipped_reasons.append(
                        f"Row {idx + 2}: bank code and branch code are required."
                    )
                continue
            if not bank_name:
                bank_name = f"Bank {bank_code}"
            if not bank_branch:
                bank_branch = branch_code

            existing = (
                FarmerBankAccount.objects.filter(
                    farmer=farmer,
                    account_number__iexact=account_number,
                )
                .order_by("-is_primary", "id")
                .first()
            )
            if existing:
                existing.account_name = account_name
                existing.bank_name = bank_name
                existing.bank_code = bank_code
                existing.bank_branch = bank_branch
                existing.branch_code = branch_code
                if is_primary:
                    existing.is_primary = True
                existing.save()
                updated_count += 1
            else:
                account = FarmerBankAccount(
                    farmer=farmer,
                    account_name=account_name,
                    account_number=account_number,
                    bank_name=bank_name,
                    bank_code=bank_code,
                    bank_branch=bank_branch,
                    branch_code=branch_code,
                    is_primary=is_primary or not farmer.bank_accounts.exists(),
                )
                account.save()
                created_count += 1

        messages.success(
            request,
            f"Farmer bank accounts import finished. Created {created_count}, updated {updated_count}, skipped {skipped_count}.",
        )
        if skipped_reasons:
            messages.warning(request, "Examples: " + " | ".join(skipped_reasons))
        return redirect("farmer-list")


class FarmerBankAccountImportTemplateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.change_farmer"

    def get(self, request):
        if not request.user.is_superuser:
            messages.error(
                request, "Only superuser can download the farmer bank account import template."
            )
            return redirect("farmer-list")

        columns = [
            "Branch",
            "registration_number",
            "account_name",
            "account_number",
            "bank_name",
            "bank_code",
            "bank_branch",
            "branch_code",
            "is_primary",
        ]
        sample_row = [
            "Anamaduwa",
            "FAB00001",
            "NIMAL PERERA",
            "1234567890",
            "Bank of Ceylon",
            "7010",
            "Colombo",
            "048",
            "yes",
        ]
        fmt = str(request.GET.get("format") or "xlsx").strip().lower()
        if fmt == "csv":
            response = HttpResponse(content_type="text/csv")
            response["Content-Disposition"] = (
                'attachment; filename="farmer_bank_accounts_import_template.csv"'
            )
            writer = csv.writer(response)
            writer.writerow(columns)
            writer.writerow(sample_row)
            return response

        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = (
            'attachment; filename="farmer_bank_accounts_import_template.xlsx"'
        )
        get_pandas().DataFrame([sample_row], columns=columns).to_excel(
            response, index=False, sheet_name="FarmerBankAccounts"
        )
        return response


class FarmerFormContextMixin:
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        points_qs = CollectionPoint.objects.select_related("route", "route__branch").order_by("route__code", "number")
        if not self.request.user.is_superuser:
            branch_ids = list(get_allowed_branches_qs(self.request.user).values_list("id", flat=True))
            points_qs = points_qs.filter(route__branch_id__in=branch_ids) if branch_ids else points_qs.none()
        ctx["farmer_points_route_json"] = [
            {
                "id": p.pk,
                "branch_id": p.route.branch_id if p.route_id else None,
                "label": f"{p.number} - {p.name}",
                "route_display": f"{p.route.name}",
            }
            for p in points_qs
        ]
        return ctx


class FarmerCreateView(
    LoginRequiredMixin,
    PermissionRequiredMixin,
    ModelFormPageTitleMixin,
    FarmerFormContextMixin,
    CreateView,
):
    permission_required = 'masters.add_farmer'
    model = Farmer
    form_class = FarmerForm
    template_name = 'masters/farmer_form.html'
    success_url = reverse_lazy('farmer-list')

    def _resolved_return_point_id(self):
        raw = self.request.POST.get("return_point") or self.request.GET.get("return_point")
        if not raw or not str(raw).isdigit():
            return None
        point_pk = int(raw)
        exists = filter_by_user_branches(
            CollectionPoint.objects.filter(pk=point_pk),
            self.request.user,
            "route__branch_id",
        ).exists()
        return point_pk if exists else None

    def get_initial(self):
        initial = super().get_initial()
        cp_raw = self.request.GET.get("collection_point")
        if cp_raw and str(cp_raw).isdigit() and not self.request.POST:
            cp_pk = int(cp_raw)
            cp = filter_by_user_branches(
                CollectionPoint.objects.filter(pk=cp_pk),
                self.request.user,
                "route__branch_id",
            ).first()
            if cp:
                initial["collection_point"] = cp.pk
                initial["route"] = cp.route_id
                initial["branch"] = cp.route.branch_id
        return initial

    def get_success_url(self):
        point_pk = self._resolved_return_point_id()
        if point_pk:
            return reverse("point-detail", kwargs={"pk": point_pk})
        return str(self.success_url)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs


class FarmerUpdateView(
    LoginRequiredMixin,
    PermissionRequiredMixin,
    ModelFormPageTitleMixin,
    FarmerFormContextMixin,
    UpdateView,
):
    permission_required = 'masters.change_farmer'
    model = Farmer
    form_class = FarmerForm
    template_name = 'masters/farmer_form.html'
    success_url = reverse_lazy('farmer-list')

    def get_success_url(self):
        raw = self.request.POST.get("return_point") or self.request.GET.get("return_point")
        if raw and str(raw).isdigit():
            point_pk = int(raw)
            exists = filter_by_user_branches(
                CollectionPoint.objects.filter(pk=point_pk),
                self.request.user,
                "route__branch_id",
            ).exists()
            if exists:
                return reverse("point-detail", kwargs={"pk": point_pk})
        return str(self.success_url)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs


class FarmerDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = Farmer
    template_name = "masters/farmer_detail.html"
    context_object_name = "farmer"
    permission_required = "masters.view_farmer"

    def get_queryset(self):
        return filter_by_user_branches(
            Farmer.objects.select_related(
                "branch", "route", "collection_point", "collection_point__route"
            ).prefetch_related("bank_accounts"),
            self.request.user,
            "branch_id",
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        start, end, full = parse_statement_period(self.request)
        period_active = bool(start and end)
        ctx["statement_from"] = start.isoformat() if start else ""
        ctx["statement_to"] = end.isoformat() if end else ""
        ctx["statement_period_active"] = period_active
        ctx.update(build_farmer_milk_collection_context(self.object, start if period_active else None, end if period_active else None))
        ctx["farmer_rate_history"] = self.object.rate_history.all()[:20]
        from django.utils import timezone as dj_timezone

        ctx["rate_form"] = kwargs.get(
            "rate_form",
            FarmerRateUpdateForm(
                initial={
                    "rate": self.object.rate,
                    "effective_from": dj_timezone.localdate(),
                    "apply_rate_paid": Farmer.ApplyRatePaid.YES,
                }
            ),
        )
        ctx["bank_account_form"] = kwargs.get(
            "bank_account_form",
            FarmerBankAccountForm(),
        )
        ctx["can_view_milk_payment_status"] = self.request.user.has_perm(
            "collections.view_milkcollectionpaymentstatus"
        )
        ctx["can_view_farmer_advances"] = self.request.user.has_perm(
            "masters.view_farmeradvancepayment"
        )
        ctx["can_view_farmer_goods"] = self.request.user.has_perm(
            "suppliers.view_farmergoodsissue"
        )
        ctx["can_change_farmer_goods"] = self.request.user.has_perm(
            "suppliers.change_farmergoodsissue"
        )
        ctx["can_delete_farmer_goods"] = self.request.user.has_perm(
            "suppliers.delete_farmergoodsissue"
        )
        ctx["can_view_farmer_loans"] = self.request.user.has_perm(
            "farmer_loans.view_farmerloan"
        )
        ctx["can_view_farmer_payments"] = self.request.user.has_perm(
            "reports.view_paymentsettlement"
        )
        if (
            ctx["can_view_farmer_advances"]
            or ctx["can_view_farmer_goods"]
            or ctx["can_view_farmer_loans"]
            or ctx["can_view_farmer_payments"]
        ):
            period_start = start if period_active else None
            period_end = end if period_active else None
            ctx.update(build_farmer_financial_context(self.object, period_start, period_end))
            from urllib.parse import urlencode

            goods_report_params = [("farmer", self.object.pk)]
            if self.object.branch_id:
                goods_report_params.append(("branch", self.object.branch_id))
            route_id = self.object.route_id
            if not route_id and self.object.collection_point_id:
                route_id = self.object.collection_point.route_id
            if route_id:
                goods_report_params.append(("route", route_id))
            if period_active:
                goods_report_params.extend(
                    [("start", start.isoformat()), ("end", end.isoformat())]
                )
            ctx["farmer_goods_summary_report_query"] = urlencode(goods_report_params)
        return ctx

    def post(self, request, *args, **kwargs):
        """Superuser bulk delete for milk collections listed on this farmer detail page."""
        self.object = self.get_object()
        if not request.user.is_superuser:
            messages.error(request, "Only superusers can bulk delete milk collections.")
            return self._redirect_to_detail(request)

        action = (request.POST.get("bulk_action") or "").strip()
        if action not in {"delete_selected", "delete_all"}:
            messages.error(request, "Invalid bulk action.")
            return self._redirect_to_detail(request)

        from milk_collections.models import CollectionSource, MilkCollection

        start, end, _full = parse_statement_period(request)
        # Prefer period posted with the bulk form (matches current filters on the page).
        from_raw = (request.POST.get("from") or "").strip()
        to_raw = (request.POST.get("to") or "").strip()
        if from_raw and to_raw:
            parsed_start = parse_date(from_raw)
            parsed_end = parse_date(to_raw)
            if parsed_start and parsed_end:
                if parsed_start > parsed_end:
                    parsed_start, parsed_end = parsed_end, parsed_start
                start, end = parsed_start, parsed_end

        base_qs = MilkCollection.objects.filter(
            farmer=self.object,
            source=CollectionSource.FARMER,
        )
        if start and end:
            base_qs = base_qs.filter(date__gte=start, date__lte=end)

        if action == "delete_all":
            target_qs = base_qs
        else:
            selected_ids = [int(x) for x in request.POST.getlist("selected_ids") if str(x).isdigit()]
            target_qs = base_qs.filter(pk__in=selected_ids) if selected_ids else base_qs.none()

        deleted_count = target_qs.count()
        if deleted_count == 0:
            messages.warning(request, "No matching milk collections selected.")
            return self._redirect_to_detail(request, start=start, end=end)

        target_qs.delete()
        messages.success(request, f"Deleted {deleted_count} milk collection record(s).")
        return self._redirect_to_detail(request, start=start, end=end)

    def _redirect_to_detail(self, request, start=None, end=None):
        url = reverse("farmer-detail", kwargs={"pk": self.object.pk})
        if start and end:
            return redirect(f"{url}?from={start.isoformat()}&to={end.isoformat()}")
        redirect_query = (request.POST.get("redirect_query") or "").strip()
        if redirect_query:
            return redirect(f"{url}?{redirect_query}")
        return redirect(url)


class FarmerStatementPrintView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "masters/farmer_statement_print.html"
    permission_required = "masters.view_farmer"

    def get_queryset(self):
        return filter_by_user_branches(
            Farmer.objects.select_related("branch", "route", "collection_point"),
            self.request.user,
            "branch_id",
        )

    def get_farmer(self):
        if not hasattr(self, "_farmer"):
            self._farmer = get_object_or_404(self.get_queryset(), pk=self.kwargs["pk"])
        return self._farmer

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        farmer = self.get_farmer()
        start, end, full = parse_statement_period(self.request)
        if not full and not (start and end):
            full = True
        ctx.update(build_farmer_statement_context(farmer, start, end, full=full))
        ctx["can_view_milk_payment_status"] = self.request.user.has_perm(
            "collections.view_milkcollectionpaymentstatus"
        )
        ctx["can_view_farmer_advances"] = self.request.user.has_perm(
            "masters.view_farmeradvancepayment"
        )
        ctx["can_view_farmer_goods"] = self.request.user.has_perm(
            "suppliers.view_farmergoodsissue"
        )
        ctx["can_view_farmer_loans"] = self.request.user.has_perm(
            "farmer_loans.view_farmerloan"
        )
        ctx["can_view_farmer_payments"] = self.request.user.has_perm(
            "reports.view_paymentsettlement"
        )
        ctx["autoprint"] = self.request.GET.get("autoprint") == "1"
        return ctx


class FarmerRateUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.change_farmerrate"

    def post(self, request, pk):
        farmer_qs = filter_by_user_branches(
            Farmer.objects.select_related("branch", "route", "collection_point"),
            request.user,
            "branch_id",
        )
        farmer = get_object_or_404(farmer_qs, pk=pk)
        form = FarmerRateUpdateForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Please enter a valid rate update.")
            detail_view = FarmerDetailView()
            detail_view.request = request
            detail_view.object = farmer
            context = detail_view.get_context_data(rate_form=form)
            return render(request, "masters/farmer_detail.html", context, status=400)
        farmer.rate = form.cleaned_data["rate"]
        farmer.apply_rate_paid = Farmer.ApplyRatePaid.YES
        farmer._rate_effective_from = form.cleaned_data["effective_from"]
        farmer.save()
        messages.success(
            request,
            f"Farmer rate updated from {form.cleaned_data['effective_from']}. Latest rate is now active.",
        )
        return redirect("farmer-detail", pk=farmer.pk)


class FarmerRateHistoryDeleteView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Superuser-only delete of a farmer rate history row."""

    def test_func(self):
        return bool(self.request.user and self.request.user.is_superuser)

    def post(self, request, farmer_pk, rate_pk):
        farmer = get_object_or_404(
            filter_by_user_branches(Farmer.objects.all(), request.user, "branch_id"),
            pk=farmer_pk,
        )
        rate_row = get_object_or_404(FarmerRateHistory, pk=rate_pk, farmer=farmer)
        rate_row.delete()

        latest = (
            FarmerRateHistory.objects.filter(farmer=farmer)
            .order_by("-effective_at", "-id")
            .first()
        )
        # Use update() so Farmer.save() does not insert a replacement history row.
        if latest:
            Farmer.objects.filter(pk=farmer.pk).update(
                rate=latest.rate,
                apply_rate_paid=latest.apply_rate_paid,
            )
        else:
            Farmer.objects.filter(pk=farmer.pk).update(
                rate=Decimal("0.00"),
                apply_rate_paid=Farmer.ApplyRatePaid.YES,
            )

        messages.success(request, "Farmer rate history row deleted.")
        return redirect("farmer-detail", pk=farmer.pk)


class FarmerDeleteView(LoginRequiredMixin, PermissionRequiredMixin, RedirectGetDeleteMixin, DeleteView):
    model = Farmer
    success_url = reverse_lazy("farmer-list")
    permission_required = "masters.delete_farmer"

    def get_queryset(self):
        return filter_by_user_branches(Farmer.objects.all(), self.request.user, "branch_id")

    def get_success_url(self):
        raw = self.request.POST.get("return_point") or self.request.GET.get("return_point")
        if raw and str(raw).isdigit():
            point_pk = int(raw)
            exists = filter_by_user_branches(
                CollectionPoint.objects.filter(pk=point_pk),
                self.request.user,
                "route__branch_id",
            ).exists()
            if exists:
                return reverse("point-detail", kwargs={"pk": point_pk})
        return str(self.success_url)

    def _is_ajax(self):
        return self.request.headers.get("X-Requested-With") == "XMLHttpRequest"

    def form_valid(self, form):
        self.object = self.get_object()
        success_url = self.get_success_url()
        can_delete = self.request.user.is_superuser
        try:
            self.object.delete()
        except ProtectedError as exc:
            payload = format_farmer_delete_blocked_payload(self.object, exc, can_delete=can_delete)
            if self._is_ajax():
                return JsonResponse({"ok": False, **payload}, status=409)
            messages.error(self.request, payload["error"])
            return redirect("farmer-detail", pk=self.object.pk)
        except Exception:
            payload = format_farmer_delete_blocked_payload(self.object, can_delete=can_delete)
            if self._is_ajax():
                return JsonResponse({"ok": False, **payload}, status=409)
            messages.error(self.request, payload["error"])
            return redirect("farmer-detail", pk=self.object.pk)
        if self._is_ajax():
            return JsonResponse({"ok": True, "redirect": success_url})
        return redirect(success_url)
