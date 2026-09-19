from canmee_dairies.mixins import ModelFormPageTitleMixin, RedirectGetDeleteMixin
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.db.models import Count, DecimalField, Max, OuterRef, Prefetch, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView
from datetime import date
from decimal import Decimal

from hrm.models import Employee
from masters.forms import RouteForm
from masters.models import Farmer, Route
from milk_collections.models import CollectionSource, MilkCollection
from milk_collections.totals import kg_liters_totals
from dispatch.models import MilkDistribution
from suppliers.models import RawMilkSupplierCollection

from .forms import BranchForm, BranchOpeningStockForm, BranchStockAdjustmentForm
from .models import Branch, BranchMilkStock, BranchMilkStockAdjustment
from .services import branch_stock_adjustment_totals, branch_stock_snapshot
from canmee_dairies.constants import MILK_LITER_FACTOR
from .utils import (
    branch_quantity_to_kg,
    filter_by_user_branches,
    get_allowed_branches_qs,
    kg_to_branch_quantity,
)

User = get_user_model()


def _can_manage_opening_stock(user):
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__iexact="Admin").exists()


class BranchListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = Branch
    template_name = "branches/branch_list.html"
    permission_required = "branches.view_branch"

    def get_queryset(self):
        today = date.today()
        base = get_allowed_branches_qs(self.request.user)
        today_milk_sq = (
            MilkCollection.objects.route_collections()
            .filter(branch_id=OuterRef("pk"), date=today)
            .values("branch_id")
            .annotate(total=Sum("kg"))
            .values("total")[:1]
        )
        today_raw_sq = (
            RawMilkSupplierCollection.objects.filter(branch_id=OuterRef("pk"), date=today)
            .exclude(status=RawMilkSupplierCollection.CollectionStatus.CANCELLED)
            .values("branch_id")
            .annotate(total=Sum("kg"))
            .values("total")[:1]
        )
        today_dispatch_sq = (
            MilkDistribution.objects.filter(branch_id=OuterRef("pk"), date=today)
            .exclude(status=MilkDistribution.DistributionStatus.CANCELLED)
            .values("branch_id")
            .annotate(total=Sum("kg"))
            .values("total")[:1]
        )
        return (
            base
            .prefetch_related("users", "employees")
            .select_related("milk_stock", "branch_manager")
            .annotate(
                today_collection_kg=Coalesce(
                    Subquery(today_milk_sq, output_field=DecimalField(max_digits=14, decimal_places=2)),
                    Value(Decimal("0.00"), output_field=DecimalField(max_digits=14, decimal_places=2)),
                ),
                today_raw_collection_kg=Coalesce(
                    Subquery(today_raw_sq, output_field=DecimalField(max_digits=14, decimal_places=2)),
                    Value(Decimal("0.00"), output_field=DecimalField(max_digits=14, decimal_places=2)),
                ),
                today_dispatch_kg=Coalesce(
                    Subquery(today_dispatch_sq, output_field=DecimalField(max_digits=14, decimal_places=2)),
                    Value(Decimal("0.00"), output_field=DecimalField(max_digits=14, decimal_places=2)),
                ),
            )
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        total_stock = Decimal("0.00")
        today_milk = Decimal("0.00")
        today_raw = Decimal("0.00")
        today_dispatch = Decimal("0.00")
        missing_opening = 0
        active_today = 0
        for branch in ctx.get("object_list", []):
            route_milk_kg = branch.today_collection_kg or Decimal("0.00")
            raw_kg = branch.today_raw_collection_kg or Decimal("0.00")
            dispatch_kg = branch.today_dispatch_kg or Decimal("0.00")
            branch.today_route_milk_kg = route_milk_kg
            branch.today_collection_kg = route_milk_kg + raw_kg
            stock_snap = branch_stock_snapshot(branch)
            branch.opening_stock_kg = stock_snap["opening_kg"]
            branch.current_stock_kg = stock_snap["current_kg"]
            branch.opening_stock_display = kg_to_branch_quantity(stock_snap["opening_kg"], branch)
            branch.has_opening_stock = (branch.opening_stock_kg or Decimal("0")) > Decimal("0")
            branch.stock_pct = None
            if branch.maximum_milk_storage and branch.maximum_milk_storage > Decimal("0"):
                pct = (branch.current_stock_kg or Decimal("0")) / branch.maximum_milk_storage * Decimal("100")
                branch.stock_pct = float(min(Decimal("100"), pct))
            branch.is_active_today = (
                route_milk_kg > Decimal("0") or raw_kg > Decimal("0") or dispatch_kg > Decimal("0")
            )
            total_stock += branch.current_stock_kg or Decimal("0")
            today_milk += route_milk_kg
            today_raw += raw_kg
            today_dispatch += dispatch_kg
            if not branch.has_opening_stock:
                missing_opening += 1
            if branch.is_active_today:
                active_today += 1
        branches = list(ctx.get("object_list", []))
        collected_kg = today_milk + today_raw
        ctx["branch_summary"] = {
            "count": len(branches),
            "total_stock_kg": total_stock,
            "total_stock_liters": (total_stock * MILK_LITER_FACTOR).quantize(Decimal("0.01")),
            "today_milk_kg": today_milk,
            "today_raw_kg": today_raw,
            "today_collected_kg": collected_kg,
            "today_collected_liters": (collected_kg * MILK_LITER_FACTOR).quantize(Decimal("0.01")),
            "today_dispatch_kg": today_dispatch,
            "today_dispatch_liters": (today_dispatch * MILK_LITER_FACTOR).quantize(Decimal("0.01")),
            "missing_opening": missing_opening,
            "active_today": active_today,
        }
        ctx["can_manage_opening_stock"] = (
            self.request.user.has_perm("branches.change_branch")
            and _can_manage_opening_stock(self.request.user)
        )
        if ctx["can_manage_opening_stock"]:
            ctx["opening_stock_form"] = BranchOpeningStockForm()
            ctx["branch_opening_rows"] = [
                {
                    "id": b.pk,
                    "name": b.name,
                    "collection_unit": b.collection_unit,
                    "collection_unit_label": b.get_collection_unit_display(),
                    "opening_stock_display": str(
                        kg_to_branch_quantity(
                            b.milk_stock.opening_kg if getattr(b, "milk_stock", None) else Decimal("0.00"),
                            b,
                        )
                    ),
                }
                for b in ctx.get("object_list", [])
            ]
        if self.request.user.has_perm("branches.add_branch"):
            ctx["branch_create_form"] = BranchForm()
        if self.request.user.has_perm("branches.change_branch"):
            ctx["branch_edit_form"] = BranchForm()
            ctx["branch_edit_rows"] = [
                {
                    "id": b.pk,
                    "name": b.name,
                    "maximum_milk_storage": (
                        str(b.maximum_milk_storage) if b.maximum_milk_storage is not None else ""
                    ),
                    "branch_manager_id": b.branch_manager_id,
                    "manager_contact": b.manager_contact or "",
                    "branch_contact": b.branch_contact or "",
                    "location": b.location or "",
                    "collection_unit": b.collection_unit,
                    "user_ids": list(b.users.values_list("id", flat=True)),
                    "employee_ids": list(b.employees.values_list("id", flat=True)),
                }
                for b in get_allowed_branches_qs(self.request.user)
                .prefetch_related("users", "employees")
                .select_related("branch_manager")
                .order_by("name")
            ]
        return ctx


class BranchDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = Branch
    template_name = "branches/branch_detail.html"
    context_object_name = "branch"
    permission_required = "branches.view_branch"

    def get_queryset(self):
        users_qs = User.objects.order_by("username")
        employees_qs = Employee.objects.order_by("common_name", "first_name", "last_name")
        routes_qs = Route.objects.select_related("branch").order_by("code")
        qs = (
            Branch.objects.all()
            .select_related("milk_stock", "branch_manager")
            .prefetch_related(
                Prefetch("users", queryset=users_qs),
                Prefetch("employees", queryset=employees_qs),
                Prefetch("routes", queryset=routes_qs),
            )
        )
        return filter_by_user_branches(qs, self.request.user, "id")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = date.today()
        route_summary = (
            MilkCollection.objects.route_collections()
            .filter(branch_id=self.object.pk)
            .aggregate(
                total_entries=Count("id"),
                total_kg=Sum("kg"),
                total_liters=Sum("liters"),
                latest_date=Max("date"),
            )
        )
        farmer_entries = (
            MilkCollection.objects.filter(
                branch_id=self.object.pk,
                source=CollectionSource.FARMER,
            ).count()
        )
        raw_summary = RawMilkSupplierCollection.objects.filter(branch_id=self.object.pk).exclude(
            status=RawMilkSupplierCollection.CollectionStatus.CANCELLED
        ).aggregate(
            total_entries=Count("id"),
            total_kg=Sum("kg"),
            total_liters=Sum("liters"),
            latest_date=Max("date"),
        )
        stock_snap = branch_stock_snapshot(self.object)
        adjustment_totals = branch_stock_adjustment_totals(self.object)
        opening_kg = stock_snap["opening_kg"]
        opening_liters = (opening_kg * MILK_LITER_FACTOR).quantize(Decimal("0.01"))
        route_kg = route_summary["total_kg"] or Decimal("0.00")
        route_liters = route_summary["total_liters"] or Decimal("0.00")
        raw_kg = raw_summary["total_kg"] or Decimal("0.00")
        raw_liters = raw_summary["total_liters"] or Decimal("0.00")
        net_adjustment_kg = adjustment_totals["net_adjustment_kg"]
        net_adjustment_liters = adjustment_totals["net_adjustment_liters"]
        latest_dates = [
            d for d in (route_summary["latest_date"], raw_summary["latest_date"]) if d
        ]
        ctx["branch_collection"] = {
            "total_entries": (route_summary["total_entries"] or 0) + (raw_summary["total_entries"] or 0),
            "point_entries": route_summary["total_entries"] or 0,
            "farmer_entries": farmer_entries,
            "total_kg": route_kg + raw_kg + opening_kg + net_adjustment_kg,
            "total_liters": route_liters + raw_liters + opening_liters + net_adjustment_liters,
            "latest_date": max(latest_dates) if latest_dates else None,
        }
        ctx["branch_stock_adjustments"] = adjustment_totals
        ctx["branch_raw_collection"] = {
            "total_entries": raw_summary["total_entries"] or 0,
            "total_kg": raw_kg,
            "total_liters": raw_liters,
            "latest_date": raw_summary["latest_date"],
        }
        milk_tab = self.request.GET.get("milk_tab", "route")
        if milk_tab not in {"route", "point", "farmer"}:
            milk_tab = "route"
        ctx["milk_tab"] = milk_tab
        branch_milk_qs = (
            MilkCollection.objects.filter(branch_id=self.object.pk)
            .select_related("route", "collection_point", "farmer", "branch", "created_by")
            .order_by("-date", "route__code", "collection_point__number", "farmer__full_name")
        )
        if milk_tab == "route":
            raw = (
                branch_milk_qs.filter(source=CollectionSource.POINT)
                .values("date", "route_id", "route__code", "route__name")
                .annotate(total_kg=Sum("kg"), total_liters=Sum("liters"))
                .order_by("-date", "route__code")
            )
            branch_milk_route_rows = [
                {
                    "date": row["date"],
                    "route_id": row["route_id"],
                    "route_name": row["route__name"],
                    "route_label": f"{row['route__code']} — {row['route__name']}",
                    "total_kg": row["total_kg"],
                    "total_liters": row["total_liters"],
                }
                for row in raw
            ]
            ctx["branch_milk_route_rows"] = branch_milk_route_rows
            route_table_totals = kg_liters_totals(branch_milk_qs.filter(source=CollectionSource.POINT))
            route_table_totals["count"] = len(branch_milk_route_rows)
            ctx["branch_milk_route_table_totals"] = route_table_totals
            ctx["branch_milk_collections"] = branch_milk_qs.none()
        elif milk_tab == "point":
            ctx["branch_milk_route_rows"] = []
            ctx["branch_milk_route_table_totals"] = None
            ctx["branch_milk_collections"] = branch_milk_qs.filter(source=CollectionSource.POINT)
        else:
            ctx["branch_milk_route_rows"] = []
            ctx["branch_milk_route_table_totals"] = None
            ctx["branch_milk_collections"] = branch_milk_qs.filter(source=CollectionSource.FARMER)
        branch_raw_milk_qs = (
            RawMilkSupplierCollection.objects.filter(branch_id=self.object.pk)
            .select_related("supplier", "created_by")
            .order_by("-date", "-id")
        )
        ctx["branch_raw_milk_collections"] = branch_raw_milk_qs
        ctx["branch_raw_milk_table_totals"] = kg_liters_totals(branch_raw_milk_qs)
        today_regular_collection = (
            MilkCollection.objects.filter(
                branch_id=self.object.pk,
                date=today,
                source=CollectionSource.POINT,
            )
            .aggregate(total=Sum("kg"))
            .get("total")
            or Decimal("0.00")
        )
        today_raw_collection = (
            RawMilkSupplierCollection.objects.filter(branch_id=self.object.pk, date=today)
            .exclude(status=RawMilkSupplierCollection.CollectionStatus.CANCELLED)
            .aggregate(total=Sum("kg"))
            .get("total")
            or Decimal("0.00")
        )
        today_dispatch = (
            MilkDistribution.objects.filter(branch_id=self.object.pk, date=today)
            .exclude(status=MilkDistribution.DistributionStatus.CANCELLED)
            .aggregate(total=Sum("kg"))
            .get("total")
            or Decimal("0.00")
        )
        ctx["today_collection_kg"] = today_regular_collection + today_raw_collection
        ctx["today_dispatch_kg"] = today_dispatch
        ctx["current_stock_kg"] = stock_snap["current_kg"]
        ctx["opening_stock_kg"] = stock_snap["opening_kg"]
        ctx["opening_stock_display"] = kg_to_branch_quantity(ctx["opening_stock_kg"], self.object)
        ctx["opening_stock_unit_label"] = self.object.get_collection_unit_display()
        ctx["opening_stock_form"] = BranchOpeningStockForm(
            initial={"opening_quantity": ctx["opening_stock_display"]}
        )
        ctx["stock_adjustment_rows"] = (
            BranchMilkStockAdjustment.objects.filter(branch_id=self.object.pk)
            .select_related("created_by")
            .order_by("-date", "-id")
        )
        ctx["stock_adjustment_form"] = BranchStockAdjustmentForm(branch=self.object)
        ctx["can_manage_opening_stock"] = (
            self.request.user.has_perm("branches.change_branch")
            and _can_manage_opening_stock(self.request.user)
        )
        routes = list(self.object.routes.all())
        ctx["branch_smart_stats"] = {
            "routes": len(routes),
            "users": self.object.users.count(),
            "employees": self.object.employees.count(),
            "farmers": Farmer.objects.filter(branch_id=self.object.pk).count(),
            "milk_entries": route_summary["total_entries"] or 0,
            "current_stock_kg": stock_snap["current_kg"],
        }
        return ctx


class BranchOpeningStockUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "branches.change_branch"

    def post(self, request, pk):
        is_ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        if not _can_manage_opening_stock(request.user):
            if is_ajax:
                return JsonResponse(
                    {"ok": False, "error": "Only superuser or Admin role users can set opening milk stock."},
                    status=403,
                )
            messages.error(request, "Only superuser or Admin role users can set opening milk stock.")
            return redirect("branch-detail", pk=pk)

        branch_qs = filter_by_user_branches(Branch.objects.filter(pk=pk), request.user, "id")
        branch = get_object_or_404(branch_qs)
        form = BranchOpeningStockForm(request.POST)
        if not form.is_valid():
            if is_ajax:
                errs = {name: [str(e) for e in items] for name, items in form.errors.items()}
                return JsonResponse({"ok": False, "errors": errs}, status=400)
            first_error = next(iter(form.errors.values()))[0] if form.errors else "Invalid value."
            messages.error(request, f"Could not update opening stock: {first_error}")
            next_url = (request.POST.get("next_url") or "").strip()
            if next_url.startswith("/"):
                return redirect(next_url)
            return redirect("branch-detail", pk=branch.pk)

        opening_qty = form.cleaned_data["opening_quantity"]
        opening_kg = branch_quantity_to_kg(opening_qty, branch)
        stock, _ = BranchMilkStock.objects.get_or_create(branch=branch)
        stock.opening_kg = opening_kg
        stock.save(update_fields=["opening_kg", "updated_at"])
        from .services import recalculate_branch_stock

        recalculate_branch_stock(branch)
        stock_snap = branch_stock_snapshot(branch)
        if is_ajax:
            return JsonResponse(
                {
                    "ok": True,
                    "opening_stock_display": str(opening_qty),
                    "opening_stock_kg": str(stock_snap["opening_kg"]),
                    "current_stock_kg": str(stock_snap["current_kg"]),
                    "has_opening_stock": stock_snap["opening_kg"] > Decimal("0"),
                }
            )
        messages.success(
            request,
            f"Opening milk stock updated ({opening_qty} {branch.get_collection_unit_display().lower()}).",
        )
        next_url = (request.POST.get("next_url") or "").strip()
        if next_url.startswith("/"):
            return redirect(next_url)
        return redirect("branch-detail", pk=branch.pk)


class BranchStockAdjustmentCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "branches.change_branch"

    def post(self, request, pk):
        if not _can_manage_opening_stock(request.user):
            messages.error(request, "Only superuser or Admin role users can record stock adjustments.")
            return redirect("branch-detail", pk=pk)

        branch_qs = filter_by_user_branches(Branch.objects.filter(pk=pk), request.user, "id")
        branch = get_object_or_404(branch_qs)
        form = BranchStockAdjustmentForm(request.POST, branch=branch)
        if not form.is_valid():
            first_error = next(iter(form.errors.values()))[0] if form.errors else "Invalid value."
            messages.error(request, f"Could not save stock adjustment: {first_error}")
            return redirect("branch-detail", pk=branch.pk)

        adjustment = form.save(commit=True, created_by=request.user, branch=branch)
        from .services import recalculate_branch_stock

        recalculate_branch_stock(branch)
        messages.success(
            request,
            f"{adjustment.get_adjustment_type_display()} recorded ({kg_to_branch_quantity(adjustment.kg, branch)} "
            f"{branch.get_collection_unit_display().lower()} on {adjustment.date}).",
        )
        return redirect("branch-detail", pk=branch.pk)


class BranchStockAdjustmentUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "branches.change_branch"

    def post(self, request, pk, adjustment_id):
        if not _can_manage_opening_stock(request.user):
            messages.error(request, "Only superuser or Admin role users can edit stock adjustments.")
            return redirect("branch-detail", pk=pk)

        branch_qs = filter_by_user_branches(Branch.objects.filter(pk=pk), request.user, "id")
        branch = get_object_or_404(branch_qs)
        adjustment = get_object_or_404(
            BranchMilkStockAdjustment.objects.filter(branch=branch, pk=adjustment_id)
        )
        form = BranchStockAdjustmentForm(request.POST, instance=adjustment, branch=branch)
        if not form.is_valid():
            first_error = next(iter(form.errors.values()))[0] if form.errors else "Invalid value."
            messages.error(request, f"Could not update stock adjustment: {first_error}")
            return redirect("branch-detail", pk=branch.pk)

        adjustment = form.save(commit=True, branch=branch)
        from .services import recalculate_branch_stock

        recalculate_branch_stock(branch)
        messages.success(
            request,
            f"{adjustment.get_adjustment_type_display()} updated ({kg_to_branch_quantity(adjustment.kg, branch)} "
            f"{branch.get_collection_unit_display().lower()} on {adjustment.date}).",
        )
        return redirect("branch-detail", pk=branch.pk)


class BranchStockAdjustmentDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "branches.change_branch"

    def post(self, request, pk, adjustment_id):
        if not _can_manage_opening_stock(request.user):
            messages.error(request, "Only superuser or Admin role users can delete stock adjustments.")
            return redirect("branch-detail", pk=pk)

        branch_qs = filter_by_user_branches(Branch.objects.filter(pk=pk), request.user, "id")
        branch = get_object_or_404(branch_qs)
        adjustment = get_object_or_404(
            BranchMilkStockAdjustment.objects.filter(branch=branch, pk=adjustment_id)
        )
        adjustment.delete()
        from .services import recalculate_branch_stock

        recalculate_branch_stock(branch)
        messages.success(request, "Stock adjustment deleted.")
        return redirect("branch-detail", pk=branch.pk)


class BranchMilkCollectionQuickDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "collections.delete_milkcollection"

    def post(self, request, pk, collection_id):
        from milk_collections.models import MilkCollection

        branch_qs = filter_by_user_branches(Branch.objects.filter(pk=pk), request.user, "id")
        branch = get_object_or_404(branch_qs)
        row = get_object_or_404(MilkCollection.objects.filter(pk=collection_id, branch_id=branch.pk))
        try:
            row.delete()
        except Exception:
            return JsonResponse(
                {"ok": False, "error": "Could not delete this milk collection record."},
                status=409,
            )
        return JsonResponse({"ok": True})


class BranchRouteQuickCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.add_route"

    def post(self, request, pk):
        branch = get_object_or_404(filter_by_user_branches(Branch.objects.filter(pk=pk), request.user, "id"))
        data = request.POST.copy()
        data["branch"] = str(branch.pk)
        form = RouteForm(data=data, user=request.user)
        if not form.is_valid():
            errs = {name: [str(e) for e in items] for name, items in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errs}, status=400)
        route = form.save()
        return JsonResponse(
            {
                "ok": True,
                "route": {
                    "id": route.pk,
                    "code": route.code,
                    "name": route.name,
                    "detail_url": reverse("route-detail", kwargs={"pk": route.pk}),
                },
            }
        )


class BranchRouteQuickUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.change_route"

    def post(self, request, pk, route_id):
        branch = get_object_or_404(filter_by_user_branches(Branch.objects.filter(pk=pk), request.user, "id"))
        route = get_object_or_404(Route.objects.filter(pk=route_id, branch_id=branch.pk))
        data = request.POST.copy()
        data["branch"] = str(branch.pk)
        form = RouteForm(data=data, instance=route, user=request.user)
        if not form.is_valid():
            errs = {name: [str(e) for e in items] for name, items in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errs}, status=400)
        route = form.save()
        return JsonResponse(
            {
                "ok": True,
                "route": {"id": route.pk, "code": route.code, "name": route.name},
            }
        )


class BranchRouteQuickDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.delete_route"

    def post(self, request, pk, route_id):
        branch = get_object_or_404(filter_by_user_branches(Branch.objects.filter(pk=pk), request.user, "id"))
        route = get_object_or_404(Route.objects.filter(pk=route_id, branch_id=branch.pk))
        try:
            route.delete()
        except Exception:
            return JsonResponse(
                {
                    "ok": False,
                    "error": (
                        "Could not delete this route. It may still be used by collection points, "
                        "farmers, milk collections, or other records."
                    ),
                },
                status=409,
            )
        return JsonResponse({"ok": True})


class BranchQuickCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "branches.add_branch"

    def post(self, request):
        form = BranchForm(data=request.POST)
        if not form.is_valid():
            errs = {name: [str(e) for e in items] for name, items in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errs}, status=400)
        branch = form.save()
        return JsonResponse({"ok": True, "branch": {"id": branch.pk}})


class BranchQuickUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "branches.change_branch"

    def post(self, request, pk):
        branch = get_object_or_404(filter_by_user_branches(Branch.objects.filter(pk=pk), request.user, "id"))
        form = BranchForm(data=request.POST, instance=branch)
        if not form.is_valid():
            errs = {name: [str(e) for e in items] for name, items in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errs}, status=400)
        form.save()
        return JsonResponse({"ok": True})


class BranchCreateView(LoginRequiredMixin, PermissionRequiredMixin, ModelFormPageTitleMixin, CreateView):
    model = Branch
    form_class = BranchForm
    template_name = "masters/form.html"
    success_url = reverse_lazy("branch-list")
    permission_required = "branches.add_branch"


class BranchUpdateView(LoginRequiredMixin, PermissionRequiredMixin, ModelFormPageTitleMixin, UpdateView):
    model = Branch
    form_class = BranchForm
    template_name = "masters/form.html"
    success_url = reverse_lazy("branch-list")
    permission_required = "branches.change_branch"

    def get_queryset(self):
        return filter_by_user_branches(Branch.objects.all(), self.request.user, "id")


class BranchDeleteView(LoginRequiredMixin, PermissionRequiredMixin, RedirectGetDeleteMixin, DeleteView):
    model = Branch
    success_url = reverse_lazy("branch-list")
    permission_required = "branches.delete_branch"

    def get_queryset(self):
        return filter_by_user_branches(Branch.objects.all(), self.request.user, "id")
