from decimal import Decimal, InvalidOperation
from collections import defaultdict
from datetime import datetime, time
import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin, UserPassesTestMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, FormView, ListView, TemplateView, UpdateView
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.permissions import DjangoModelPermissions, IsAuthenticated
from rest_framework import status

from canmee_dairies.constants import MILK_LITER_FACTOR
from canmee_dairies.formatting import format_money
from canmee_dairies.mixins import AnyPermissionRequiredMixin, RedirectGetDeleteMixin

from .forms import (
    ConsumptionSettlementForm,
    FarmerGoodsIssueHeaderForm,
    FarmerGoodsModuleSettingsForm,
    get_farmer_goods_issue_line_formset,
    GRNForm,
    GRNItemFormSet,
    InventoryUsageForm,
    RawMilkSupplierCollectionPaymentForm,
    RawMilkSupplierCollectionForm,
    RawMilkSupplierCollectionResultForm,
    RawMilkSupplierCollectionStatusForm,
    RawMilkSupplierPaymentReceiveForm,
    SupplierForm,
    SupplierPaymentForm,
    SupplierRateUpdateForm,
)
from .models import (
    ConsumptionSettlement,
    DriverFarmerGoodsRequest,
    FarmerGoodsIssue,
    FarmerGoodsModuleSettings,
    GRN,
    GRNItem,
    RawMilkSupplierCollection,
    RawMilkSupplierPayment,
    Supplier,
    SupplierRateHistory,
    SupplierTransaction,
)
from .supplier_rates import annotate_rate_history_windows
from .serializers import (
    ConfirmGRNSerializer,
    ConsumptionSettlementSerializer,
    FarmerGoodsIssueSerializer,
    GRNSerializer,
    SupplierPaymentSerializer,
    SupplierSerializer,
    SupplierTransactionSerializer,
)
from branches.models import Branch
from branches.utils import (
    filter_m2m_by_user_branches,
    get_allowed_branches_qs,
    get_default_branch_id,
    get_user_branch_ids,
    master_branch_option_rows,
)
from reports.manual_settlement import (
    record_goods_receipt_payment,
    set_goods_batch_settled,
    settlement_datetime_from_date,
    settlement_from_request,
)
from masters.models import CollectionPoint, Farmer, Route

from .services import (
    accept_farmer_goods_issue_batch,
    approx_price_map_for_issues,
    assert_farmer_goods_batch_accessible_to_user,
    batch_amount_totals_for_issues,
    bulk_accept_farmer_goods_issue_batches,
    check_farmer_goods_issue_line,
    confirm_grn,
    count_pending_collection_point_batches_for_user,
    delete_farmer_goods_issue_batch,
    delete_farmer_goods_issue_line,
    consume_goods,
    farmer_goods_accept_notifications_enabled,
    farmer_goods_branch_check_rows,
    fifo_price_map_for_issues,
    finalize_grn_totals,
    auto_accept_all_pending_farmer_goods_issues,
    cancel_driver_farmer_goods_request,
    fulfill_driver_farmer_goods_request,
    goods_issue_batch_settlement_summary,
    issue_goods_batch,
    pending_collection_point_batches_for_user,
    driver_farmer_goods_requests_for_user,
    pending_driver_farmer_goods_requests_for_user,
    latest_farmer_goods_unit_price,
    priced_farmer_goods_issue_lines,
    add_farmer_goods_issue_line,
    update_farmer_goods_issue_header,
    record_inventory_usage,
    record_supplier_payment,
    reject_farmer_goods_issue_batch,
    update_farmer_goods_issue_batch,
    update_farmer_goods_issue_line,
    user_can_view_driver_farmer_goods_requests,
    _global_farmer_goods_available_qty,
)
from branches.services import recalculate_branch_stock

from stock_management.models import BranchStock, Product, ProductCategory


_GRN_SUPPLIER_CATEGORY_CHOICES = tuple(
    choice for choice in Supplier.Category.choices if choice[0] != Supplier.Category.RAW_MILK_SUPPLIER
)


def _goods_supplier_branch_options(user):
    return master_branch_option_rows(
        filter_m2m_by_user_branches(
            Supplier.objects.prefetch_related("branches")
            .filter(status=Supplier.Status.ACTIVE)
            .exclude(category=Supplier.Category.RAW_MILK_SUPPLIER),
            user,
        )
    )


def _grn_discounts_match_scope(form, formset):
    scope = form.cleaned_data["discount_scope"]
    doc_pct = form.cleaned_data.get("document_discount_percent") or Decimal("0")
    doc_amt = form.cleaned_data.get("document_discount_amount") or Decimal("0")
    if scope == GRN.DiscountScope.LINE:
        if doc_pct > 0 or doc_amt > 0:
            form.add_error(
                None,
                "Clear total discount fields when using per-product discounts, or change “Discount applies to” to on GRN total.",
            )
            return False
    elif scope == GRN.DiscountScope.DOCUMENT:
        for f in formset.forms:
            if not f.cleaned_data or f.cleaned_data.get("DELETE"):
                continue
            if not f.cleaned_data.get("product"):
                continue
            lp = f.cleaned_data.get("line_discount_percent") or Decimal("0")
            la = f.cleaned_data.get("line_discount_amount") or Decimal("0")
            if lp > Decimal("0") or la > Decimal("0"):
                form.add_error(
                    None,
                    "Per-product line discounts are set, but scope is “on GRN total”. "
                    "Clear line discount columns or switch scope to per product.",
                )
                return False
    return True


class SupplierListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = Supplier
    template_name = "suppliers/supplier_list.html"
    permission_required = "suppliers.view_supplier"

    def get_queryset(self):
        qs = filter_m2m_by_user_branches(
            Supplier.objects.prefetch_related("branches"),
            self.request.user,
        )
        category = self.request.GET.get("category")
        if category:
            qs = qs.filter(category=category)
        return qs.order_by("name")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["categories"] = Supplier.Category.choices
        ctx["selected_category"] = self.request.GET.get("category", "")
        base_qs = filter_m2m_by_user_branches(
            Supplier.objects.all(),
            self.request.user,
        )
        category_counts = {
            row["category"]: row["c"]
            for row in base_qs.values("category").annotate(c=Count("id"))
        }
        ctx["supplier_summary"] = {
            "count": base_qs.count(),
            "active": base_qs.filter(status=Supplier.Status.ACTIVE).count(),
            "raw_milk": category_counts.get(Supplier.Category.RAW_MILK_SUPPLIER, 0),
        }
        ctx["category_counts"] = category_counts
        ctx["category_chips"] = [
            {
                "code": code,
                "label": label,
                "count": category_counts.get(code, 0),
            }
            for code, label in Supplier.Category.choices
        ]
        if self.request.user.has_perm("suppliers.add_supplier"):
            supplier_create_form = SupplierForm(user=self.request.user)
            supplier_create_form.fields["branches"].widget.attrs["id"] = "supplier-add-branches"
            ctx["supplier_create_form"] = supplier_create_form
        if self.request.user.has_perm("suppliers.change_supplier"):
            supplier_edit_form = SupplierForm(user=self.request.user)
            supplier_edit_form.fields["branches"].widget.attrs["id"] = "supplier-edit-branches"
            ctx["supplier_edit_form"] = supplier_edit_form
            ctx["supplier_edit_rows"] = [
                {
                    "id": s.pk,
                    "name": s.name or "",
                    "category": s.category or "",
                    "contact_number": s.contact_number or "",
                    "address": s.address or "",
                    "email": s.email or "",
                    "status": s.status or "",
                    "branch_ids": list(s.branches.values_list("id", flat=True)),
                }
                for s in self.object_list
            ]
        return ctx


class SupplierQuickCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "suppliers.add_supplier"

    def post(self, request):
        form = SupplierForm(data=request.POST, user=request.user)
        if not form.is_valid():
            errs = {name: [str(e) for e in items] for name, items in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errs}, status=400)
        supplier = form.save()
        return JsonResponse({"ok": True, "supplier": {"id": supplier.pk, "name": supplier.name}})


class SupplierQuickUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "suppliers.change_supplier"

    def post(self, request, pk):
        supplier = get_object_or_404(
            filter_m2m_by_user_branches(Supplier.objects.all(), request.user),
            pk=pk,
        )
        form = SupplierForm(data=request.POST, instance=supplier, user=request.user)
        if not form.is_valid():
            errs = {name: [str(e) for e in items] for name, items in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errs}, status=400)
        form.save()
        return JsonResponse({"ok": True})


class SupplierCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Supplier
    form_class = SupplierForm
    template_name = "suppliers/supplier_form.html"
    success_url = reverse_lazy("supplier-list")
    permission_required = "suppliers.add_supplier"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs


class SupplierUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Supplier
    form_class = SupplierForm
    template_name = "suppliers/supplier_form.html"
    success_url = reverse_lazy("supplier-list")
    permission_required = "suppliers.change_supplier"

    def get_queryset(self):
        return filter_m2m_by_user_branches(Supplier.objects.all(), self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs


class SupplierDeleteView(LoginRequiredMixin, PermissionRequiredMixin, RedirectGetDeleteMixin, DeleteView):
    model = Supplier
    success_url = reverse_lazy("supplier-list")
    permission_required = "suppliers.delete_supplier"

    def get_queryset(self):
        return filter_m2m_by_user_branches(Supplier.objects.all(), self.request.user)


def _raw_milk_supplier_qs(user):
    return filter_m2m_by_user_branches(
        Supplier.objects.filter(category=Supplier.Category.RAW_MILK_SUPPLIER).prefetch_related(
            "branches"
        ),
        user,
    )


class RawMilkSupplierDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = Supplier
    template_name = "suppliers/raw_milk_supplier_detail.html"
    context_object_name = "supplier"
    permission_required = "suppliers.view_supplier"

    def get_queryset(self):
        return _raw_milk_supplier_qs(self.request.user).prefetch_related("branches")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from .raw_milk_billing import raw_milk_supplier_account_summary

        rate_form = kwargs.get("rate_form")
        if rate_form is None:
            rate_form = SupplierRateUpdateForm(
                initial={
                    "rate": self.object.rate,
                    "rate_unit": self.object.rate_unit,
                }
            )
        ctx["rate_form"] = rate_form
        history = list(
            SupplierRateHistory.objects.filter(supplier=self.object).order_by(
                "-effective_from", "-id"
            )[:50]
        )
        ctx["supplier_rate_history"] = annotate_rate_history_windows(history)
        ctx["account_summary"] = raw_milk_supplier_account_summary(self.object)

        base_qs = (
            RawMilkSupplierCollection.objects.filter(supplier=self.object)
            .select_related("branch", "supplier", "created_by")
        )
        ids = get_user_branch_ids(self.request.user)
        if ids is not None:
            base_qs = base_qs.filter(branch_id__in=ids) if ids else base_qs.none()

        date_from_raw = (self.request.GET.get("date_from") or "").strip()
        date_to_raw = (self.request.GET.get("date_to") or "").strip()
        date_from = parse_date(date_from_raw) if date_from_raw else None
        date_to = parse_date(date_to_raw) if date_to_raw else None
        selected_branch = (self.request.GET.get("branch") or "").strip()
        selected_status = (self.request.GET.get("status") or "").strip()
        selected_payment = (self.request.GET.get("payment_status") or "").strip()

        valid_status = {c[0] for c in RawMilkSupplierCollection.CollectionStatus.choices}
        valid_payment = {
            c[0] for c in RawMilkSupplierCollection.CollectionPaymentStatus.choices
        }

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

        collection_qs = period_qs
        if selected_status in valid_status:
            collection_qs = collection_qs.filter(status=selected_status)
        if selected_payment in valid_payment:
            collection_qs = collection_qs.filter(payment_status=selected_payment)

        summary = collection_qs.aggregate(
            total_entries=Count("id"),
            total_kg=Sum("kg"),
            total_liters=Sum("liters"),
            latest_date=Max("date"),
            total_amount=Sum("total_amount"),
            total_paid=Sum("paid_amount"),
        )
        ctx["supplier_collections"] = {
            "total_entries": summary["total_entries"] or 0,
            "total_kg": summary["total_kg"] or Decimal("0.00"),
            "total_liters": summary["total_liters"] or Decimal("0.00"),
            "latest_date": summary["latest_date"],
        }
        ctx["supplier_collection_rows"] = collection_qs.order_by("-date", "-id")
        ctx["supplier_collection_totals"] = {
            "total_kg": summary["total_kg"] or Decimal("0.00"),
            "total_liters": summary["total_liters"] or Decimal("0.00"),
            "total_amount": summary["total_amount"] or Decimal("0.00"),
            "total_paid": summary["total_paid"] or Decimal("0.00"),
            "count": summary["total_entries"] or 0,
        }

        period_active = bool(
            date_from or date_to or selected_branch or selected_status or selected_payment
        )
        ctx["collection_filters"] = {
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
        }
        ctx["status_choices"] = RawMilkSupplierCollection.CollectionStatus.choices
        ctx["payment_status_choices"] = RawMilkSupplierCollection.CollectionPaymentStatus.choices
        ctx["supplier_collection_branches"] = get_allowed_branches_qs(self.request.user).order_by(
            "name"
        )
        return ctx


def _apply_supplier_rate(supplier, *, rate, rate_unit, effective_from=None):
    """Update supplier rate/history and rebill unpaid collections. Returns (changed, effective_from)."""
    from .raw_milk_billing import sync_raw_milk_collection_billing

    if effective_from is None:
        effective_from = timezone.localdate()
    changed = supplier.rate != rate or (supplier.rate_unit or "") != (rate_unit or "")
    if changed:
        supplier.rate = rate
        supplier.rate_unit = rate_unit
        supplier._rate_effective_from = effective_from
        supplier.save()
        unpaid = RawMilkSupplierCollection.objects.filter(supplier=supplier, paid_amount=0).exclude(
            status=RawMilkSupplierCollection.CollectionStatus.CANCELLED
        )
        for coll in unpaid.iterator():
            sync_raw_milk_collection_billing(coll)
    return changed, effective_from


class RawMilkSupplierRateUpdateView(LoginRequiredMixin, AnyPermissionRequiredMixin, View):
    permission_required = ("suppliers.change_supplierrate", "suppliers.change_supplier")

    def post(self, request, pk):
        supplier = get_object_or_404(_raw_milk_supplier_qs(request.user), pk=pk)
        form = SupplierRateUpdateForm(request.POST)
        if not form.is_valid():
            messages.error(request, "Please enter a valid supplier rate.")
            detail_view = RawMilkSupplierDetailView()
            detail_view.request = request
            detail_view.object = supplier
            detail_view.kwargs = {"pk": supplier.pk}
            context = detail_view.get_context_data(rate_form=form, object=supplier)
            return render(request, "suppliers/raw_milk_supplier_detail.html", context, status=400)

        effective_from = form.cleaned_data["effective_from"]
        _apply_supplier_rate(
            supplier,
            rate=form.cleaned_data["rate"],
            rate_unit=form.cleaned_data["rate_unit"],
            effective_from=effective_from,
        )
        messages.success(
            request,
            f"Supplier rate updated to {format_money(supplier.rate)} "
            f"{supplier.get_rate_unit_display().lower()} from {effective_from}.",
        )
        return redirect("raw-milk-supplier-detail", pk=supplier.pk)


class RawMilkSupplierRateCellSaveView(LoginRequiredMixin, AnyPermissionRequiredMixin, View):
    """Inline rate save from the suppliers list (JSON)."""

    permission_required = ("suppliers.change_supplierrate", "suppliers.change_supplier")

    def post(self, request):
        supplier_id = (request.POST.get("supplier_id") or "").strip()
        if not supplier_id.isdigit():
            return JsonResponse({"ok": False, "error": "Supplier is required."}, status=400)

        supplier = get_object_or_404(_raw_milk_supplier_qs(request.user), pk=int(supplier_id))
        rate_raw = (request.POST.get("rate") or "").replace(",", "").strip()
        rate_unit = (request.POST.get("rate_unit") or "").strip().lower()
        if rate_unit not in {Supplier.RateUnit.LITER, Supplier.RateUnit.KG}:
            return JsonResponse({"ok": False, "error": "Choose liter or kg."}, status=400)
        try:
            rate_val = Decimal(rate_raw)
        except (InvalidOperation, TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "Enter a valid rate."}, status=400)
        if rate_val < 0:
            return JsonResponse({"ok": False, "error": "Rate cannot be negative."}, status=400)

        changed, effective_from = _apply_supplier_rate(
            supplier, rate=rate_val, rate_unit=rate_unit
        )
        unit_short = supplier.get_rate_unit_display().replace("Per ", "").lower()
        return JsonResponse(
            {
                "ok": True,
                "supplier_id": supplier.pk,
                "rate": str(supplier.rate),
                "rate_display": format_money(supplier.rate),
                "rate_unit": supplier.rate_unit,
                "rate_unit_label": unit_short,
                "effective_from": effective_from.isoformat(),
                "saved": changed,
            }
        )


class SupplierRateHistoryDeleteView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Superuser-only delete of a supplier rate history row."""

    def test_func(self):
        return bool(self.request.user and self.request.user.is_superuser)

    def post(self, request, supplier_pk, rate_pk):
        supplier = get_object_or_404(_raw_milk_supplier_qs(request.user), pk=supplier_pk)
        rate_row = get_object_or_404(SupplierRateHistory, pk=rate_pk, supplier=supplier)
        rate_row.delete()

        latest = (
            SupplierRateHistory.objects.filter(supplier=supplier)
            .order_by("-effective_from", "-id")
            .first()
        )
        if latest:
            Supplier.objects.filter(pk=supplier.pk).update(
                rate=latest.rate, rate_unit=latest.rate_unit
            )
            supplier.refresh_from_db(fields=["rate", "rate_unit"])
        else:
            Supplier.objects.filter(pk=supplier.pk).update(
                rate=Decimal("0.00"), rate_unit=Supplier.RateUnit.LITER
            )
            supplier.refresh_from_db(fields=["rate", "rate_unit"])

        from .raw_milk_billing import sync_raw_milk_collection_billing

        unpaid = RawMilkSupplierCollection.objects.filter(
            supplier=supplier, paid_amount=0
        ).exclude(status=RawMilkSupplierCollection.CollectionStatus.CANCELLED)
        for coll in unpaid.iterator():
            sync_raw_milk_collection_billing(coll)

        messages.success(request, "Supplier rate history row deleted.")
        return redirect("raw-milk-supplier-detail", pk=supplier.pk)


class RawMilkSupplierAccountView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = Supplier
    template_name = "suppliers/raw_milk_supplier_account.html"
    context_object_name = "supplier"
    permission_required = "suppliers.view_supplier"

    def get_queryset(self):
        return _raw_milk_supplier_qs(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        from .raw_milk_billing import outstanding_collections_qs, raw_milk_supplier_account_summary

        ctx["account_summary"] = raw_milk_supplier_account_summary(self.object)

        date_from_raw = (self.request.GET.get("date_from") or "").strip()
        date_to_raw = (self.request.GET.get("date_to") or "").strip()
        date_from = parse_date(date_from_raw) if date_from_raw else None
        date_to = parse_date(date_to_raw) if date_to_raw else None
        selected_branch = (self.request.GET.get("branch") or "").strip()

        base_outstanding_qs = outstanding_collections_qs(self.object)
        outstanding_qs = base_outstanding_qs
        if date_from:
            outstanding_qs = outstanding_qs.filter(date__gte=date_from)
        if date_to:
            outstanding_qs = outstanding_qs.filter(date__lte=date_to)
        if selected_branch.isdigit():
            outstanding_qs = outstanding_qs.filter(branch_id=int(selected_branch))

        outstanding = list(outstanding_qs)
        ctx["outstanding_collections"] = outstanding
        period_active = bool(date_from or date_to or selected_branch)
        ctx["account_filters"] = {
            "date_from": date_from.isoformat() if date_from else "",
            "date_to": date_to.isoformat() if date_to else "",
            "branch": selected_branch if selected_branch.isdigit() else "",
            "period_active": period_active,
            "count": len(outstanding),
            "all_count": base_outstanding_qs.count(),
        }
        ctx["account_branch_options"] = get_allowed_branches_qs(self.request.user).order_by("name")

        ledger_from_raw = (self.request.GET.get("ledger_from") or "").strip()
        ledger_to_raw = (self.request.GET.get("ledger_to") or "").strip()
        ledger_from = parse_date(ledger_from_raw) if ledger_from_raw else None
        ledger_to = parse_date(ledger_to_raw) if ledger_to_raw else None
        ledger_type = (self.request.GET.get("ledger_type") or "").strip().lower()
        if ledger_type not in {"credit", "debit"}:
            ledger_type = ""

        base_ledger_qs = SupplierTransaction.objects.filter(supplier=self.object)
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

        prior_totals = _signed_totals(
            base_ledger_qs.filter(date__date__lt=ledger_from) if ledger_from else base_ledger_qs.none()
        )
        opening_balance = prior_totals["credit"] - prior_totals["debit"]

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
            RawMilkSupplierPayment.objects.filter(supplier=self.object)
            .prefetch_related("allocations__collection")
            .select_related("created_by")
            .order_by("-date", "-id")[:50]
        )
        payment_form = kwargs.get("payment_form")
        if payment_form is None:
            payment_form = RawMilkSupplierPaymentReceiveForm()
        ctx["payment_form"] = payment_form
        ctx["can_receive_payment"] = self.request.user.has_perm(
            "suppliers.add_rawmilksupplierpayment"
        ) or self.request.user.has_perm("suppliers.change_rawmilksuppliercollection")
        return ctx


class RawMilkSupplierPaymentReceiveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "suppliers.change_rawmilksuppliercollection"

    def post(self, request, pk):
        from .raw_milk_billing import record_raw_milk_payment

        supplier = get_object_or_404(_raw_milk_supplier_qs(request.user), pk=pk)
        form = RawMilkSupplierPaymentReceiveForm(request.POST)
        selected = request.POST.getlist("collection_ids")
        use_explicit = (request.POST.get("allocation_mode") or "").strip() == "explicit"
        allocation_amounts = {}
        if use_explicit:
            for coll_id in selected:
                raw = (request.POST.get(f"alloc_{coll_id}") or "").replace(",", "").strip()
                if not raw:
                    continue
                try:
                    allocation_amounts[int(coll_id)] = Decimal(raw)
                except (InvalidOperation, ValueError):
                    messages.error(request, "Invalid allocation amount.")
                    return redirect("raw-milk-supplier-account", pk=supplier.pk)

        if not form.is_valid():
            messages.error(request, "Please enter a valid payment date and amount.")
            account_view = RawMilkSupplierAccountView()
            account_view.request = request
            account_view.object = supplier
            account_view.kwargs = {"pk": supplier.pk}
            context = account_view.get_context_data(payment_form=form, object=supplier)
            return render(request, "suppliers/raw_milk_supplier_account.html", context, status=400)

        if not selected:
            messages.error(request, "Select at least one bulk collection to apply this payment.")
            return redirect("raw-milk-supplier-account", pk=supplier.pk)

        try:
            payment = record_raw_milk_payment(
                supplier=supplier,
                amount=form.cleaned_data["amount"],
                date=form.cleaned_data["date"],
                reference=form.cleaned_data.get("reference") or "",
                note=form.cleaned_data.get("note") or "",
                created_by=request.user,
                collection_ids=selected,
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
            return redirect("raw-milk-supplier-account", pk=supplier.pk)

        messages.success(
            request,
            f"Payment of {format_money(payment.amount)} recorded "
            f"across {payment.allocations.count()} collection(s).",
        )
        return redirect("raw-milk-supplier-account", pk=supplier.pk)


class SupplierLedgerView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = SupplierTransaction
    template_name = "suppliers/supplier_ledger.html"
    permission_required = "suppliers.view_suppliertransaction"

    def get_queryset(self):
        qs = SupplierTransaction.objects.select_related("supplier").order_by("-date")
        supplier_id = self.request.GET.get("supplier")
        if supplier_id:
            qs = qs.filter(supplier_id=supplier_id)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["suppliers"] = filter_m2m_by_user_branches(
            Supplier.objects.all(),
            self.request.user,
        ).order_by("name")
        ctx["selected_supplier"] = self.request.GET.get("supplier", "")
        return ctx


def raw_milk_collection_list_totals(queryset):
    from milk_collections.totals import kg_liters_totals

    return kg_liters_totals(queryset)


def _raw_milk_is_ajax(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _raw_milk_form_errors_json(form):
    errors = {}
    for field, field_errors in form.errors.items():
        errors[field] = [str(error) for error in field_errors]
    for error in form.non_field_errors():
        errors.setdefault("__all__", []).append(str(error))
    return errors


def _raw_milk_redirect_next(request, *, fallback):
    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect(fallback)


def _raw_milk_status_json(instance):
    return {
        "ok": True,
        "status": instance.status,
        "status_display": instance.get_status_display(),
    }


def _raw_milk_payment_json(instance):
    return {
        "ok": True,
        "payment_status": instance.payment_status,
        "payment_status_display": instance.get_payment_status_display(),
    }


def _raw_milk_collection_queryset_for_user(user):
    qs = RawMilkSupplierCollection.objects.select_related("branch")
    ids = get_user_branch_ids(user)
    if ids is not None:
        if not ids:
            return qs.none()
        qs = qs.filter(branch_id__in=ids)
    return qs


class RawMilkSupplierCollectionListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = RawMilkSupplierCollection
    paginate_by = 100
    template_name = "suppliers/raw_milk_collection_list.html"
    permission_required = "suppliers.view_rawmilkbulkcollection"

    def get_queryset(self):
        qs = (
            super()
            .get_queryset()
            .select_related("branch", "supplier", "created_by")
            .order_by("-date", "-id")
        )
        ids = get_user_branch_ids(self.request.user)
        if ids is not None:
            if not ids:
                return qs.none()
            qs = qs.filter(branch_id__in=ids)
        branch_ids = self.request.GET.getlist("branches")
        if branch_ids:
            safe_ids = [int(b) for b in branch_ids if str(b).isdigit()]
            qs = qs.filter(branch_id__in=safe_ids)
        supplier_id = self.request.GET.get("supplier")
        if supplier_id and str(supplier_id).isdigit():
            qs = qs.filter(supplier_id=int(supplier_id))
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
        status = (self.request.GET.get("status") or "").strip()
        if status:
            valid_status = {choice[0] for choice in RawMilkSupplierCollection.CollectionStatus.choices}
            if status in valid_status:
                qs = qs.filter(status=status)
        payment_status = (self.request.GET.get("payment_status") or "").strip()
        if payment_status:
            valid_payment = {
                choice[0] for choice in RawMilkSupplierCollection.CollectionPaymentStatus.choices
            }
            if payment_status in valid_payment:
                qs = qs.filter(payment_status=payment_status)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["branches"] = get_allowed_branches_qs(self.request.user).order_by("name")
        ctx["selected_branches"] = self.request.GET.getlist("branches")
        ctx["raw_milk_suppliers"] = Supplier.objects.filter(
            category=Supplier.Category.RAW_MILK_SUPPLIER
        ).order_by("name")
        ctx["selected_supplier"] = self.request.GET.get("supplier", "")
        ctx["date_from"] = self.request.GET.get("date_from", "")
        ctx["date_to"] = self.request.GET.get("date_to", "")
        ctx["selected_status"] = self.request.GET.get("status", "")
        ctx["selected_payment_status"] = self.request.GET.get("payment_status", "")
        q = self.request.GET.copy()
        q.pop("page", None)
        ctx["filter_query"] = q.urlencode()
        ctx["raw_milk_totals"] = raw_milk_collection_list_totals(self.get_queryset())
        ctx["status_choices"] = RawMilkSupplierCollection.CollectionStatus.choices
        ctx["payment_status_choices"] = RawMilkSupplierCollection.CollectionPaymentStatus.choices
        return ctx


class RawMilkSupplierCollectionCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = RawMilkSupplierCollection
    form_class = RawMilkSupplierCollectionForm
    template_name = "suppliers/raw_milk_collection_form.html"
    success_url = reverse_lazy("raw-milk-collection-list")
    permission_required = "suppliers.add_rawmilksuppliercollection"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        supplier_id = (self.request.GET.get("supplier") or "").strip()
        if supplier_id.isdigit():
            initial["supplier"] = int(supplier_id)
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        supplier_qs = filter_m2m_by_user_branches(
            Supplier.objects.prefetch_related("branches").filter(
                status=Supplier.Status.ACTIVE,
                category=Supplier.Category.RAW_MILK_SUPPLIER,
            ),
            self.request.user,
        )
        ctx["supplier_branch_options"] = master_branch_option_rows(supplier_qs)
        return ctx

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, "Raw milk collection saved.")
        response = super().form_valid(form)
        recalculate_branch_stock(self.object.branch)
        return response


class RawMilkSupplierCollectionUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = RawMilkSupplierCollection
    form_class = RawMilkSupplierCollectionForm
    template_name = "suppliers/raw_milk_collection_form.html"
    success_url = reverse_lazy("raw-milk-collection-list")
    permission_required = "suppliers.change_rawmilksuppliercollection"

    def get_queryset(self):
        return _raw_milk_collection_queryset_for_user(self.request.user).select_related(
            "branch", "supplier"
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def _supplier_branch_options(self):
        supplier_qs = filter_m2m_by_user_branches(
            Supplier.objects.prefetch_related("branches").filter(
                status=Supplier.Status.ACTIVE,
                category=Supplier.Category.RAW_MILK_SUPPLIER,
            ),
            self.request.user,
        )
        return master_branch_option_rows(supplier_qs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["supplier_branch_options"] = self._supplier_branch_options()
        return ctx

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if _raw_milk_is_ajax(request):
            form = self.get_form()
            options = self._supplier_branch_options()
            return render(
                request,
                "suppliers/_raw_milk_collection_edit_modal_form.html",
                {
                    "form": form,
                    "object": self.object,
                    "supplier_branch_options_json": json.dumps(options),
                },
            )
        return super().get(request, *args, **kwargs)

    def form_valid(self, form):
        self.object = form.save()
        recalculate_branch_stock(self.object.branch)
        if _raw_milk_is_ajax(self.request):
            return JsonResponse(
                {
                    "ok": True,
                    "message": "Raw milk collection updated.",
                    "redirect": self.request.POST.get("next") or "",
                }
            )
        messages.success(self.request, "Raw milk collection updated.")
        return _raw_milk_redirect_next(self.request, fallback=self.success_url)

    def form_invalid(self, form):
        if _raw_milk_is_ajax(self.request):
            return JsonResponse(
                {"ok": False, "errors": _raw_milk_form_errors_json(form)},
                status=400,
            )
        return super().form_invalid(form)


class RawMilkSupplierCollectionStatusUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    permission_required = "suppliers.change_rawmilksuppliercollection"
    model = RawMilkSupplierCollection
    form_class = RawMilkSupplierCollectionStatusForm
    http_method_names = ["post"]

    def get_queryset(self):
        return _raw_milk_collection_queryset_for_user(self.request.user)

    def form_valid(self, form):
        instance = form.save()
        if _raw_milk_is_ajax(self.request):
            return JsonResponse(_raw_milk_status_json(instance))
        return _raw_milk_redirect_next(self.request, fallback=reverse_lazy("raw-milk-collection-list"))

    def form_invalid(self, form):
        if _raw_milk_is_ajax(self.request):
            return JsonResponse({"ok": False, "errors": _raw_milk_form_errors_json(form)}, status=400)
        for error in form.non_field_errors():
            messages.error(self.request, error)
        for errors in form.errors.values():
            for error in errors:
                messages.error(self.request, error)
        return _raw_milk_redirect_next(self.request, fallback=reverse_lazy("raw-milk-collection-list"))


class RawMilkSupplierCollectionResultUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    permission_required = "suppliers.change_rawmilksuppliercollection"
    model = RawMilkSupplierCollection
    form_class = RawMilkSupplierCollectionResultForm
    template_name = "suppliers/raw_milk_collection_result_form.html"
    success_url = reverse_lazy("raw-milk-collection-list")

    def get_queryset(self):
        return _raw_milk_collection_queryset_for_user(self.request.user).select_related("branch")

    def form_valid(self, form):
        self.object = form.save()
        recalculate_branch_stock(self.object.branch)
        if _raw_milk_is_ajax(self.request):
            payload = _raw_milk_status_json(self.object)
            if self.object.supplier_result_quantity is not None:
                payload["supplier_result_quantity"] = str(self.object.supplier_result_quantity)
            return JsonResponse(payload)
        return _raw_milk_redirect_next(self.request, fallback=self.success_url)

    def form_invalid(self, form):
        if _raw_milk_is_ajax(self.request):
            return JsonResponse(
                {"ok": False, "errors": _raw_milk_form_errors_json(form)},
                status=400,
            )
        return super().form_invalid(form)


class RawMilkSupplierCollectionPaymentUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Record a payment against one bulk collection (same pattern as buyer dispatch payment)."""

    permission_required = "suppliers.change_rawmilksuppliercollection"
    template_name = "suppliers/raw_milk_collection_payment_form.html"
    success_url = reverse_lazy("raw-milk-collection-list")

    def get_object(self):
        qs = _raw_milk_collection_queryset_for_user(self.request.user).select_related(
            "supplier", "branch"
        )
        return get_object_or_404(qs, pk=self.kwargs["pk"])

    def get(self, request, pk):
        obj = self.get_object()
        next_url = (request.GET.get("next") or "").strip()
        return render(
            request,
            self.template_name,
            {
                "collection": obj,
                "object": obj,
                "default_date": timezone.localdate().isoformat(),
                "next": next_url if next_url.startswith("/") else "",
                "form_errors": [],
            },
        )

    def post(self, request, pk):
        from .raw_milk_billing import record_raw_milk_payment

        obj = self.get_object()
        form = RawMilkSupplierPaymentReceiveForm(request.POST)
        ajax = _raw_milk_is_ajax(request)

        def error_payload(errors):
            if ajax:
                return JsonResponse({"ok": False, "errors": errors}, status=400)
            flat = []
            for msgs in errors.values():
                flat.extend(msgs)
            next_url = (request.POST.get("next") or "").strip()
            return render(
                request,
                self.template_name,
                {
                    "collection": obj,
                    "object": obj,
                    "default_date": request.POST.get("date") or timezone.localdate().isoformat(),
                    "next": next_url if next_url.startswith("/") else "",
                    "form_errors": flat,
                },
                status=400,
            )

        if not form.is_valid():
            return error_payload(_raw_milk_form_errors_json(form))

        try:
            payment = record_raw_milk_payment(
                supplier=obj.supplier,
                amount=form.cleaned_data["amount"],
                date=form.cleaned_data["date"],
                reference=form.cleaned_data.get("reference") or "",
                note=form.cleaned_data.get("note") or "",
                created_by=request.user,
                collection_ids=[obj.pk],
            )
        except ValidationError as exc:
            errors = {}
            if hasattr(exc, "message_dict") and exc.message_dict:
                for key, msgs in exc.message_dict.items():
                    if isinstance(msgs, (list, tuple)):
                        errors[key] = [str(m) for m in msgs]
                    else:
                        errors[key] = [str(msgs)]
            else:
                messages_list = list(getattr(exc, "messages", [str(exc)]))
                errors["__all__"] = [str(m) for m in messages_list]
            return error_payload(errors)

        obj.refresh_from_db()
        status = obj.payment_status_from_amounts()
        msg = (
            f"Payment of {format_money(payment.amount)} recorded for collection {obj.dispatch_no}."
        )
        if ajax:
            return JsonResponse(
                {
                    "ok": True,
                    "payment_status": status,
                    "payment_status_display": obj.get_payment_status_display(),
                    "outstanding": str(obj.outstanding_amount),
                    "paid_amount": str(obj.paid_amount or Decimal("0")),
                    "total_amount": str(obj.total_amount or Decimal("0")),
                    "message": msg,
                }
            )
        messages.success(request, msg)
        return _raw_milk_redirect_next(request, fallback=self.success_url)


class RawMilkSupplierCollectionDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "suppliers.delete_rawmilksuppliercollection"

    def post(self, request, *args, **kwargs):
        from .raw_milk_billing import reverse_raw_milk_billing_on_delete

        qs = RawMilkSupplierCollection.objects.select_related("branch", "supplier")
        ids = get_user_branch_ids(request.user)
        if ids is not None:
            qs = qs.filter(branch_id__in=ids)
        obj = get_object_or_404(qs, pk=kwargs["pk"])
        branch = obj.branch
        reverse_raw_milk_billing_on_delete(obj)
        obj.delete()
        recalculate_branch_stock(branch)
        messages.success(request, "Raw milk bulk collection deleted.")
        return redirect("raw-milk-collection-list")


class RawMilkSupplierCollectionInlineQuantityView(LoginRequiredMixin, View):
    """Superuser-only inline kg/liters edit from the raw milk collection list."""

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

        if field == "kg":
            new_liters = (value * MILK_LITER_FACTOR).quantize(Decimal("0.01"))
        else:
            new_liters = value.quantize(Decimal("0.01"))

        qs = RawMilkSupplierCollection.objects.select_related("branch", "supplier")
        ids = get_user_branch_ids(request.user)
        if ids is not None:
            if not ids:
                return JsonResponse({"ok": False, "error": "Record not found."}, status=404)
            qs = qs.filter(branch_id__in=ids)
        try:
            with transaction.atomic():
                collection = qs.select_for_update().get(pk=pk)
                collection.liters = new_liters
                collection.save()
        except RawMilkSupplierCollection.DoesNotExist:
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
                "kg": str(collection.kg),
                "liters": str(collection.liters),
            }
        )


class GRNListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = GRN
    template_name = "suppliers/grn_list.html"
    permission_required = "suppliers.view_grn"

    def get_queryset(self):
        qs = (
            super()
            .get_queryset()
            .select_related("supplier", "branch")
            .order_by("-date", "-id")
        )
        ids = get_user_branch_ids(self.request.user)
        if ids is not None:
            if not ids:
                return qs.none()
            qs = qs.filter(Q(branch_id__in=ids) | Q(branch_id__isnull=True))

        branch_id = (self.request.GET.get("branch") or "").strip()
        if branch_id.isdigit():
            qs = qs.filter(branch_id=int(branch_id))

        supplier_id = (self.request.GET.get("supplier") or "").strip()
        if supplier_id.isdigit():
            qs = qs.filter(supplier_id=int(supplier_id))

        grn_type = (self.request.GET.get("grn_type") or "").strip()
        valid_types = {choice[0] for choice in GRN.GRNType.choices}
        if grn_type in valid_types:
            qs = qs.filter(grn_type=grn_type)

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

        product_id = (self.request.GET.get("product") or "").strip()
        if product_id.isdigit():
            qs = qs.filter(items__product_id=int(product_id)).distinct()

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["branches"] = get_allowed_branches_qs(self.request.user).order_by("name")
        ctx["selected_branch"] = (self.request.GET.get("branch") or "").strip()
        ctx["grn_suppliers"] = (
            Supplier.objects.exclude(category=Supplier.Category.RAW_MILK_SUPPLIER)
            .order_by("name")
        )
        ctx["selected_supplier"] = (self.request.GET.get("supplier") or "").strip()
        ctx["grn_type_choices"] = GRN.GRNType.choices
        ctx["selected_grn_type"] = (self.request.GET.get("grn_type") or "").strip()
        ctx["date_from"] = self.request.GET.get("date_from", "")
        ctx["date_to"] = self.request.GET.get("date_to", "")
        # Only products that appear on GRNs — keeps the filter dropdown light.
        ctx["grn_products"] = (
            Product.objects.filter(grn_items__isnull=False).distinct().order_by("name")
        )
        selected_product = (self.request.GET.get("product") or "").strip()
        ctx["selected_product"] = selected_product
        ctx["selected_product_obj"] = None
        ctx["show_product_price_cols"] = False
        object_list = list(ctx["object_list"])
        draft_count = 0
        confirmed_count = 0
        farmer_goods_count = 0
        corporate_count = 0
        stock_correction_count = 0
        total_amount = Decimal("0")
        for grn in object_list:
            amount = grn.total_amount or Decimal("0")
            total_amount += amount
            if grn.status == GRN.Status.DRAFT:
                draft_count += 1
            elif grn.status == GRN.Status.CONFIRMED:
                confirmed_count += 1
            if grn.grn_type == GRN.GRNType.FARMER_GOODS:
                farmer_goods_count += 1
            elif grn.grn_type == GRN.GRNType.CORPORATE:
                corporate_count += 1
            elif grn.grn_type == GRN.GRNType.STOCK_CORRECTION:
                stock_correction_count += 1
        ctx["grn_summary"] = {
            "count": len(object_list),
            "draft_count": draft_count,
            "confirmed_count": confirmed_count,
            "farmer_goods_count": farmer_goods_count,
            "corporate_count": corporate_count,
            "stock_correction_count": stock_correction_count,
            "total_amount": total_amount,
        }
        if selected_product.isdigit():
            product = Product.objects.filter(pk=int(selected_product)).first()
            ctx["selected_product_obj"] = product
            ctx["show_product_price_cols"] = product is not None
            line_map = defaultdict(list)
            if product is not None and object_list:
                for item in GRNItem.objects.filter(
                    grn_id__in=[g.id for g in object_list],
                    product_id=product.id,
                ).order_by("id"):
                    line_map[item.grn_id].append(item)
            for grn in object_list:
                lines = line_map.get(grn.id, [])
                qty = sum((line.quantity or Decimal("0")) for line in lines)
                rate = Decimal("0")
                issue_price = Decimal("0")
                for line in lines:
                    if (line.unit_price or Decimal("0")) > 0 and rate <= 0:
                        rate = line.unit_price
                    if (line.issuing_price or Decimal("0")) > 0 and issue_price <= 0:
                        issue_price = line.issuing_price
                if rate <= 0 and lines:
                    rate = lines[0].unit_price or Decimal("0")
                if issue_price <= 0 and lines:
                    issue_price = lines[0].issuing_price or Decimal("0")
                grn.filtered_product_qty = qty
                grn.filtered_product_rate = rate
                grn.filtered_product_issue_price = issue_price
        return ctx


class GRNCreateView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    template_name = "suppliers/grn_form.html"
    form_class = GRNForm
    success_url = reverse_lazy("grn-list")
    permission_required = "suppliers.add_grn"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        item_formset = kwargs.pop("item_formset", None)
        ctx = super().get_context_data(**kwargs)
        if item_formset is not None:
            ctx["item_formset"] = item_formset
        else:
            ctx.setdefault(
                "item_formset",
                GRNItemFormSet(prefix="items", queryset=GRNItem.objects.none()),
            )
        ctx["supplier_category_choices"] = _GRN_SUPPLIER_CATEGORY_CHOICES
        ctx["product_category_choices"] = [
            (c.code, c.name) for c in ProductCategory.objects.order_by("name")
        ]
        ctx["grn_edit"] = False
        ctx["grn_obj"] = None
        ctx["supplier_branch_options"] = _goods_supplier_branch_options(self.request.user)
        return ctx

    def post(self, request, *args, **kwargs):
        form = GRNForm(request.POST, user=request.user)
        formset = GRNItemFormSet(request.POST, prefix="items", queryset=GRNItem.objects.none())
        if form.is_valid() and formset.is_valid():
            if not _grn_discounts_match_scope(form, formset):
                return self.render_to_response(self.get_context_data(form=form, item_formset=formset))
            return self.form_valid(form, formset)
        return self.render_to_response(self.get_context_data(form=form, item_formset=formset))

    @transaction.atomic
    def form_valid(self, form, formset):
        from suppliers.grn_audit import grn_audit_snapshot, log_grn_action
        from suppliers.models import GRNActionLog

        grn = form.save(commit=False)
        grn.created_by = self.request.user
        grn.save()
        form.save_m2m()
        for item in formset.save(commit=False):
            item.grn = grn
            item.save()
        for obj in formset.deleted_objects:
            obj.delete()
        finalize_grn_totals(grn)
        log_grn_action(
            grn,
            user=self.request.user,
            action=GRNActionLog.Action.CREATED,
            details={"snapshot": grn_audit_snapshot(grn)},
        )
        messages.success(self.request, "GRN created in draft status.")
        return redirect(self.success_url)


class GRNUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    """Edit draft GRNs only (same form as create)."""

    template_name = "suppliers/grn_form.html"
    permission_required = "suppliers.change_grn"

    def get_grn(self, request, pk):
        grn = get_object_or_404(GRN.objects.select_related("branch"), pk=pk)
        if grn.status != GRN.Status.DRAFT:
            raise Http404("Only draft GRNs can be edited.")
        from branches.utils import get_user_branch_ids

        bids = get_user_branch_ids(request.user)
        if bids is not None and grn.branch_id and grn.branch_id not in bids:
            raise Http404()
        return grn

    def get(self, request, pk):
        grn = self.get_grn(request, pk)
        form = GRNForm(instance=grn, user=request.user)
        formset = GRNItemFormSet(prefix="items", queryset=GRNItem.objects.filter(grn=grn))
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "item_formset": formset,
                "supplier_category_choices": _GRN_SUPPLIER_CATEGORY_CHOICES,
                "product_category_choices": [
                    (c.code, c.name) for c in ProductCategory.objects.order_by("name")
                ],
                "grn_edit": True,
                "grn_obj": grn,
                "supplier_branch_options": _goods_supplier_branch_options(request.user),
            },
        )

    def post(self, request, pk):
        grn = self.get_grn(request, pk)
        form = GRNForm(request.POST, instance=grn, user=request.user)
        formset = GRNItemFormSet(
            request.POST,
            prefix="items",
            queryset=GRNItem.objects.filter(grn=grn),
        )
        if form.is_valid() and formset.is_valid():
            if not _grn_discounts_match_scope(form, formset):
                return render(
                    request,
                    self.template_name,
                    {
                        "form": form,
                        "item_formset": formset,
                        "supplier_category_choices": _GRN_SUPPLIER_CATEGORY_CHOICES,
                        "product_category_choices": [
                    (c.code, c.name) for c in ProductCategory.objects.order_by("name")
                ],
                        "grn_edit": True,
                        "grn_obj": grn,
                        "supplier_branch_options": _goods_supplier_branch_options(request.user),
                    },
                )
            return self._save(request, form, formset)
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "item_formset": formset,
                "supplier_category_choices": _GRN_SUPPLIER_CATEGORY_CHOICES,
                "product_category_choices": [
                    (c.code, c.name) for c in ProductCategory.objects.order_by("name")
                ],
                "grn_edit": True,
                "grn_obj": grn,
                "supplier_branch_options": _goods_supplier_branch_options(request.user),
            },
        )

    @transaction.atomic
    def _save(self, request, form, formset):
        from suppliers.grn_audit import grn_audit_diff, grn_audit_snapshot, log_grn_action
        from suppliers.models import GRNActionLog

        before = grn_audit_snapshot(form.instance)
        grn = form.save()
        for item in formset.save(commit=False):
            item.grn = grn
            item.save()
        for obj in formset.deleted_objects:
            obj.delete()
        finalize_grn_totals(grn)
        after = grn_audit_snapshot(grn)
        changes = grn_audit_diff(before, after)
        log_grn_action(
            grn,
            user=request.user,
            action=GRNActionLog.Action.UPDATED,
            details={"changes": changes, "snapshot": after},
        )
        messages.success(request, "GRN saved.")
        return redirect("grn-list")


class GRNPrintView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = GRN
    template_name = "suppliers/grn_print.html"
    context_object_name = "grn"
    permission_required = "suppliers.view_grn"

    def get_queryset(self):
        return GRN.objects.select_related("supplier", "branch").prefetch_related("items__product")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        grn = ctx["grn"]
        items = list(grn.items.all())
        lines_subtotal = sum((i.total_price for i in items), Decimal("0"))
        ctx["lines_subtotal"] = lines_subtotal
        doc_disc = Decimal("0")
        if grn.discount_scope == GRN.DiscountScope.DOCUMENT:
            if grn.document_discount_percent > 0:
                doc_disc = lines_subtotal * (grn.document_discount_percent / Decimal("100"))
            elif grn.document_discount_amount > 0:
                doc_disc = min(lines_subtotal, grn.document_discount_amount)
        ctx["grn_document_discount_value"] = doc_disc
        return ctx


class GRNDetailModalAPI(LoginRequiredMixin, PermissionRequiredMixin, View):
    """JSON GRN header + line items for the list View modal."""

    permission_required = "suppliers.view_grn"

    def get(self, request, pk):
        qs = GRN.objects.select_related("supplier", "branch").prefetch_related("items__product")
        branch_ids = get_user_branch_ids(request.user)
        if branch_ids is not None:
            qs = qs.filter(Q(branch_id__in=branch_ids) | Q(branch_id__isnull=True))
        grn = get_object_or_404(qs, pk=pk)
        items = list(grn.items.all())
        lines_subtotal = sum((i.total_price or Decimal("0") for i in items), Decimal("0"))
        doc_disc = Decimal("0")
        if grn.discount_scope == GRN.DiscountScope.DOCUMENT:
            if grn.document_discount_percent > 0:
                doc_disc = lines_subtotal * (grn.document_discount_percent / Decimal("100"))
            elif grn.document_discount_amount > 0:
                doc_disc = min(lines_subtotal, grn.document_discount_amount)
        payable = (lines_subtotal - doc_disc).quantize(Decimal("0.01"))
        return JsonResponse(
            {
                "grn": {
                    "id": grn.id,
                    "number": grn.grn_number,
                    "status": grn.get_status_display(),
                    "date": grn.date.isoformat() if grn.date else "",
                    "supplier": grn.supplier.name if grn.supplier_id else "—",
                    "branch": grn.branch.name if grn.branch_id else "—",
                    "type": grn.get_grn_type_display(),
                    "discount_scope": grn.get_discount_scope_display(),
                    "total_amount": format_money(grn.total_amount or Decimal("0")),
                    "lines_subtotal": format_money(lines_subtotal),
                    "document_discount": format_money(doc_disc),
                    "payable_total": format_money(payable),
                    "print_url": reverse("grn-print", args=[grn.id]),
                    "edit_url": reverse("grn-edit", args=[grn.id]) if grn.status == GRN.Status.DRAFT else "",
                },
                "items": [
                    {
                        "product": item.product.name if item.product_id else "—",
                        "unit": item.product.unit if item.product_id else "",
                        "quantity": str(item.quantity or Decimal("0")),
                        "free_quantity": str(item.free_quantity or Decimal("0")),
                        "rate": format_money(item.unit_price or Decimal("0")),
                        "issue_price": format_money(item.issuing_price or Decimal("0")),
                        "line_discount_percent": str(item.line_discount_percent or Decimal("0")),
                        "line_discount_amount": format_money(item.line_discount_amount or Decimal("0")),
                        "line_total": format_money(item.total_price or Decimal("0")),
                    }
                    for item in items
                ],
            }
        )


class GRNAuditModalAPI(LoginRequiredMixin, UserPassesTestMixin, View):
    """Superuser-only GRN audit trail (created by, confirmed by, action log)."""

    def test_func(self):
        return self.request.user.is_superuser

    def handle_no_permission(self):
        return JsonResponse({"detail": "Only superusers can view GRN audit logs."}, status=403)

    def get(self, request, pk):
        from suppliers.models import GRNActionLog

        qs = GRN.objects.select_related(
            "supplier", "branch", "created_by", "confirmed_by"
        )
        branch_ids = get_user_branch_ids(request.user)
        if branch_ids is not None:
            qs = qs.filter(Q(branch_id__in=branch_ids) | Q(branch_id__isnull=True))
        grn = get_object_or_404(qs, pk=pk)

        def _user_label(user):
            if not user:
                return "—"
            name = (user.get_full_name() or "").strip()
            return name or user.get_username()

        logs = (
            GRNActionLog.objects.filter(Q(grn=grn) | Q(grn_number=grn.grn_number))
            .select_related("performed_by")
            .order_by("-performed_at", "-id")[:200]
        )
        events = [
            {
                "at": timezone.localtime(log.performed_at).strftime("%Y-%m-%d %H:%M:%S"),
                "action": log.get_action_display(),
                "action_key": log.action,
                "user": _user_label(log.performed_by),
                "notes": log.notes or "",
                "details": log.details or {},
            }
            for log in logs
        ]
        return JsonResponse(
            {
                "grn": {
                    "id": grn.id,
                    "number": grn.grn_number,
                    "status": grn.get_status_display(),
                    "supplier": grn.supplier.name if grn.supplier_id else "—",
                    "branch": grn.branch.name if grn.branch_id else "—",
                    "type": grn.get_grn_type_display(),
                    "business_date": grn.date.isoformat() if grn.date else "",
                    "created_at": (
                        timezone.localtime(grn.created_at).strftime("%Y-%m-%d %H:%M:%S")
                        if grn.created_at
                        else "—"
                    ),
                    "created_by": _user_label(grn.created_by),
                    "confirmed_at": (
                        timezone.localtime(grn.confirmed_at).strftime("%Y-%m-%d %H:%M:%S")
                        if grn.confirmed_at
                        else "—"
                    ),
                    "confirmed_by": _user_label(grn.confirmed_by),
                    "updated_at": (
                        timezone.localtime(grn.updated_at).strftime("%Y-%m-%d %H:%M:%S")
                        if grn.updated_at
                        else "—"
                    ),
                },
                "events": events,
            }
        )


def farmer_goods_issue_form_context(request, form, line_formset, **extra):
    allowed = get_allowed_branches_qs(request.user)
    page_heading_branch = allowed.first().name if allowed.count() == 1 else "Issue Farmer Goods"
    points_qs = CollectionPoint.objects.select_related("route", "route__branch")
    farmers_qs = Farmer.objects.select_related("route")
    if request.user.is_superuser:
        pass
    else:
        branch_ids = list(allowed.values_list("id", flat=True))
        if branch_ids:
            points_qs = points_qs.filter(route__branch_id__in=branch_ids)
            farmers_qs = farmers_qs.filter(branch_id__in=branch_ids)
        else:
            points_qs = points_qs.none()
            farmers_qs = farmers_qs.none()
    points = [
        {
            "id": p.id,
            "label": str(p),
            "branch_id": (p.route.branch_id if p.route_id else None),
            "route_name": (p.route.name if p.route_id else ""),
            "route_code": (p.route.code if p.route_id else ""),
        }
        for p in points_qs.order_by("route__code", "number")
    ]
    farmers = [
        {
            "id": f.id,
            "label": str(f),
            "branch_id": f.branch_id,
            "route_name": (f.route.name if getattr(f, "route_id", None) else ""),
            "route_code": (f.route.code if getattr(f, "route_id", None) else ""),
            "reg": (f.registration_number or ""),
        }
        for f in farmers_qs.order_by("common_name", "full_name")
    ]
    issue_branches_json = [{"id": b.id, "label": str(b)} for b in allowed.order_by("name")]

    # Products available for each “from branch” (based on branch stock).
    # Used to keep the product dropdown in-sync with the selected branch.
    branch_ids = list(allowed.values_list("id", flat=True))
    products_by_branch = {}
    if branch_ids:
        check_by_branch = {
            str(bid): {row["product_id"]: row for row in farmer_goods_branch_check_rows(bid)}
            for bid in branch_ids
        }
        stocks = (
            BranchStock.objects.filter(branch_id__in=branch_ids, quantity__gt=0)
            .select_related("product")
            .order_by("branch_id", "product__name")
        )
        for s in stocks:
            pid = s.product_id
            bid = str(s.branch_id)
            if pid is None:
                continue
            if bid not in products_by_branch:
                products_by_branch[bid] = []
            # Avoid duplicates if the data contains multiple rows.
            if not any(p["id"] == pid for p in products_by_branch[bid]):
                info = check_by_branch.get(bid, {}).get(pid) or {}
                available = info.get("effective_available")
                if available is None:
                    available = s.quantity or Decimal("0")
                unit_price = info.get("unit_price") or Decimal("0")
                products_by_branch[bid].append(
                    {
                        "id": pid,
                        "label": str(s.product),
                        "available": str(available.quantize(Decimal("0.01"))),
                        "unit_price": str(unit_price.quantize(Decimal("0.01"))),
                        "unit": (getattr(s.product, "unit", None) or info.get("unit") or ""),
                    }
                )
    ctx = {
        "form": form,
        "line_formset": line_formset,
        "issue_points_json": points,
        "issue_farmers_json": farmers,
        "issue_branches_json": issue_branches_json,
        "issue_products_by_branch_json": products_by_branch,
        "page_heading_branch": page_heading_branch,
        "issue_single_branch": allowed.count() == 1,
        "issue_branch_select_count": allowed.count(),
    }
    ctx.update(extra)
    return ctx


def farmer_goods_destination_label(head):
    destination_name = str(head.issue_to_id)
    if head.issue_to_type == FarmerGoodsIssue.IssueToType.BRANCH:
        destination_name = (
            Branch.objects.filter(pk=head.issue_to_id).values_list("name", flat=True).first() or destination_name
        )
    elif head.issue_to_type == FarmerGoodsIssue.IssueToType.COLLECTION_POINT:
        destination_name = (
            CollectionPoint.objects.filter(pk=head.issue_to_id).values_list("name", flat=True).first()
            or destination_name
        )
    elif head.issue_to_type == FarmerGoodsIssue.IssueToType.FARMER:
        destination_name = (
            Farmer.objects.filter(pk=head.issue_to_id).values_list("common_name", flat=True).first()
            or destination_name
        )
    return destination_name


def _branch_id_from_route(route_id):
    try:
        rid = int(route_id)
    except (TypeError, ValueError):
        return None
    return Route.objects.filter(pk=rid).values_list("branch_id", flat=True).first()


def _branch_route_from_collection_point(point_id):
    point = CollectionPoint.objects.select_related("route").filter(pk=point_id).first()
    if not point or not point.route_id:
        return None, None
    return point.route.branch_id, point.route_id


def _branch_route_from_farmer(farmer_id):
    farmer = Farmer.objects.select_related("route").filter(pk=farmer_id).first()
    if not farmer:
        return None, None
    return farmer.branch_id, farmer.route_id


def _resolve_filter_branch_route(
    selected_branch,
    selected_route,
    selected_point,
    selected_farmer,
    issue_to_type,
):
    branch = (selected_branch or "").strip()
    route = (selected_route or "").strip()
    if not route.isdigit():
        if issue_to_type == FarmerGoodsIssue.IssueToType.COLLECTION_POINT and selected_point.isdigit():
            bid, rid = _branch_route_from_collection_point(int(selected_point))
            if rid:
                route = str(rid)
            if not branch.isdigit() and bid:
                branch = str(bid)
        elif issue_to_type == FarmerGoodsIssue.IssueToType.FARMER and selected_farmer.isdigit():
            bid, rid = _branch_route_from_farmer(int(selected_farmer))
            if rid:
                route = str(rid)
            if not branch.isdigit() and bid:
                branch = str(bid)
    if not branch.isdigit() and route.isdigit():
        bid = _branch_id_from_route(route)
        if bid:
            branch = str(bid)
    return branch, route


def _effective_branch_id_from_request(request):
    branch_raw = (request.GET.get("branch") or "").strip()
    if branch_raw.isdigit():
        return int(branch_raw)
    route_raw = (request.GET.get("route") or "").strip()
    if route_raw.isdigit():
        return _branch_id_from_route(route_raw)
    point_raw = (request.GET.get("collection_point") or request.GET.get("point") or "").strip()
    if point_raw.isdigit():
        branch_id, _ = _branch_route_from_collection_point(int(point_raw))
        if branch_id:
            return branch_id
    farmer_raw = (request.GET.get("farmer") or "").strip()
    if farmer_raw.isdigit():
        branch_id, _ = _branch_route_from_farmer(int(farmer_raw))
        if branch_id:
            return branch_id
    return None


def _effective_route_id_from_request(request):
    route_raw = (request.GET.get("route") or "").strip()
    if route_raw.isdigit():
        return int(route_raw)
    point_raw = (request.GET.get("collection_point") or request.GET.get("point") or "").strip()
    if point_raw.isdigit():
        _, route_id = _branch_route_from_collection_point(int(point_raw))
        if route_id:
            return route_id
    farmer_raw = (request.GET.get("farmer") or "").strip()
    if farmer_raw.isdigit():
        _, route_id = _branch_route_from_farmer(int(farmer_raw))
        if route_id:
            return route_id
    return None


def _apply_issue_date_range(qs, date_from_raw, date_to_raw):
    """Filter issue datetimes by local calendar day (timezone-safe)."""
    date_from = parse_date((date_from_raw or "").strip()) if (date_from_raw or "").strip() else None
    date_to = parse_date((date_to_raw or "").strip()) if (date_to_raw or "").strip() else None
    if date_from and not date_to:
        date_to = date_from
    elif date_to and not date_from:
        date_from = date_to
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from
    tz = timezone.get_current_timezone()
    if date_from:
        start = timezone.make_aware(datetime.combine(date_from, time.min), tz)
        qs = qs.filter(date__gte=start)
    if date_to:
        end = timezone.make_aware(datetime.combine(date_to, time.max), tz)
        qs = qs.filter(date__lte=end)
    return qs


def _route_point_farmer_filter_querysets(
    request,
    *,
    selected_branch="",
    selected_route="",
    include_farmers=False,
):
    allowed_qs = get_allowed_branches_qs(request.user)
    allowed_ids = list(allowed_qs.values_list("id", flat=True))

    route_qs = Route.objects.all().order_by("name")
    point_qs = CollectionPoint.objects.select_related("route", "route__branch").order_by(
        "route__name", "number", "name"
    )
    farmer_qs = (
        Farmer.objects.select_related("route").order_by("common_name", "full_name")
        if include_farmers
        else None
    )

    if not request.user.is_superuser:
        route_qs = route_qs.filter(branch_id__in=allowed_ids)
        point_qs = point_qs.filter(route__branch_id__in=allowed_ids)
        if farmer_qs is not None:
            farmer_qs = farmer_qs.filter(branch_id__in=allowed_ids)

    if selected_branch.isdigit():
        branch_id = int(selected_branch)
        route_qs = route_qs.filter(branch_id=branch_id)
        point_qs = point_qs.filter(route__branch_id=branch_id)
        if farmer_qs is not None:
            farmer_qs = farmer_qs.filter(branch_id=branch_id)

    # Route → point/farmer narrowing is handled client-side so route changes work without reload.
    return route_qs, point_qs, farmer_qs


def _farmer_goods_issue_list_base_qs(user):
    qs = FarmerGoodsIssue.objects.filter(batch_ref__gt="")
    branch_ids = get_user_branch_ids(user)
    if branch_ids is not None:
        qs = qs.filter(from_branch_id__in=branch_ids)
    return qs


def _apply_farmer_goods_issue_list_filters(qs, request):
    branch_id = _effective_branch_id_from_request(request)
    if branch_id:
        qs = qs.filter(from_branch_id=branch_id)

    date_from = request.GET.get("date_from")
    date_to = request.GET.get("date_to")
    qs = _apply_issue_date_range(qs, date_from, date_to)

    route_id = (request.GET.get("route") or "").strip()
    point_id = (request.GET.get("collection_point") or request.GET.get("point") or "").strip()
    if point_id.isdigit():
        point_id = int(point_id)
        farmer_ids = list(
            Farmer.objects.filter(collection_point_id=point_id).values_list("pk", flat=True)
        )
        dest_q = Q(
            issue_to_type=FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
            issue_to_id=point_id,
        )
        if farmer_ids:
            dest_q |= Q(
                issue_to_type=FarmerGoodsIssue.IssueToType.FARMER,
                issue_to_id__in=farmer_ids,
            )
        qs = qs.filter(dest_q)
    elif route_id.isdigit():
        route_id = int(route_id)
        cp_ids = list(
            CollectionPoint.objects.filter(route_id=route_id).values_list("pk", flat=True)
        )
        farmer_ids = list(
            Farmer.objects.filter(route_id=route_id).values_list("pk", flat=True)
        )
        dest_q = Q()
        if cp_ids:
            dest_q |= Q(
                issue_to_type=FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
                issue_to_id__in=cp_ids,
            )
        if farmer_ids:
            dest_q |= Q(
                issue_to_type=FarmerGoodsIssue.IssueToType.FARMER,
                issue_to_id__in=farmer_ids,
            )
        qs = qs.filter(dest_q) if dest_q else qs.none()
    return qs


def _farmer_goods_issue_list_filter_context(request):
    allowed_qs = get_allowed_branches_qs(request.user)
    selected_branch = (request.GET.get("branch") or "").strip()
    selected_route = (request.GET.get("route") or "").strip()
    selected_point = (request.GET.get("collection_point") or request.GET.get("point") or "").strip()
    selected_farmer = (request.GET.get("farmer") or "").strip()
    issue_to_type = (request.GET.get("issue_to_type") or "").strip()
    if not issue_to_type:
        if selected_farmer.isdigit() and not selected_point.isdigit():
            issue_to_type = FarmerGoodsIssue.IssueToType.FARMER
        else:
            issue_to_type = FarmerGoodsIssue.IssueToType.COLLECTION_POINT
    selected_branch, selected_route = _resolve_filter_branch_route(
        selected_branch,
        selected_route,
        selected_point,
        selected_farmer,
        issue_to_type,
    )
    route_qs, point_qs, _ = _route_point_farmer_filter_querysets(
        request,
        selected_branch=selected_branch,
        selected_route=selected_route,
        include_farmers=False,
    )
    filter_q = request.GET.copy()
    filter_q.pop("type", None)
    filter_q.pop("page", None)
    return {
        "branches": allowed_qs.order_by("name"),
        "selected_branch": selected_branch,
        "filter_routes": route_qs,
        "selected_route": selected_route,
        "filter_points": point_qs,
        "selected_collection_point": selected_point,
        "date_from": request.GET.get("date_from", ""),
        "date_to": request.GET.get("date_to", ""),
        "issue_filter_query": filter_q.urlencode(),
    }


def _farmer_goods_issue_list_filtered_qs(request):
    qs = _apply_farmer_goods_issue_list_filters(
        _farmer_goods_issue_list_base_qs(request.user),
        request,
    )
    valid_types = {
        FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
        FarmerGoodsIssue.IssueToType.FARMER,
        FarmerGoodsIssue.IssueToType.BRANCH,
    }
    selected_type = (request.GET.get("type") or "").strip()
    if selected_type in valid_types:
        qs = qs.filter(issue_to_type=selected_type)
    return qs


def _farmer_goods_issue_list_redirect(request, redirect_query=""):
    redirect_query = (redirect_query or request.GET.urlencode() or "").strip()
    if redirect_query:
        return redirect(f"{reverse('farmer-goods-issue-list')}?{redirect_query}")
    return redirect("farmer-goods-issue-list")


def _build_farmer_goods_issue_batches(qs):
    refs_qs = (
        qs.values("batch_ref")
        .annotate(line_count=Count("id"), batch_date=Max("date"))
        .order_by("-batch_date")
    )
    batch_refs = [r["batch_ref"] for r in refs_qs]
    heads = {}
    all_batch_issues = []
    if batch_refs:
        all_batch_issues = list(
            FarmerGoodsIssue.objects.filter(batch_ref__in=batch_refs)
            .select_related("from_branch", "product")
            .order_by("batch_ref", "id")
        )
        for issue in all_batch_issues:
            if issue.batch_ref not in heads:
                heads[issue.batch_ref] = issue
    amount_by_ref = batch_amount_totals_for_issues(all_batch_issues)
    issues_by_ref = defaultdict(list)
    for issue in all_batch_issues:
        issues_by_ref[issue.batch_ref].append(issue)
    summary_by_ref = {r["batch_ref"]: r for r in refs_qs}
    batches = []
    type_counts = defaultdict(int)
    for ref in batch_refs:
        head = heads.get(ref)
        if not head:
            continue
        info = summary_by_ref[ref]
        batch_amount = amount_by_ref.get(ref, Decimal("0"))
        settlement = goods_issue_batch_settlement_summary(issues_by_ref.get(ref, []))
        issue_to_type = head.issue_to_type
        type_counts[issue_to_type] += 1
        batches.append(
            {
                "batch_ref": ref,
                "batch_date": info["batch_date"],
                "line_count": info["line_count"],
                "batch_amount": batch_amount,
                "from_branch_name": head.from_branch.name if head.from_branch_id else "—",
                "destination_name": farmer_goods_destination_label(head),
                "issue_to_type": issue_to_type,
                **settlement,
            }
        )
    return batches, type_counts


class GRNConfirmView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    form_class = GRNForm
    permission_required = "suppliers.change_grn"
    success_url = reverse_lazy("grn-list")
    template_name = "suppliers/grn_list.html"

    def post(self, request, *args, **kwargs):
        grn = get_object_or_404(GRN, pk=kwargs["pk"])
        try:
            confirm_grn(grn=grn, actor=request.user)
        except ValidationError as exc:
            messages.error(request, str(exc))
            return redirect(self.success_url)
        messages.success(request, f"{grn.grn_number} confirmed.")
        return redirect(self.success_url)


class GRNStatusUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    success_url = reverse_lazy("grn-list")
    permission_required = "suppliers.change_grn"

    def post(self, request, *args, **kwargs):
        from suppliers.grn_audit import grn_audit_snapshot, log_grn_action
        from suppliers.models import GRNActionLog

        grn = get_object_or_404(GRN, pk=kwargs["pk"])
        branch_ids = get_user_branch_ids(request.user)
        if branch_ids is not None:
            if not branch_ids:
                raise Http404
            if grn.branch_id and grn.branch_id not in branch_ids:
                raise Http404
        target = (request.POST.get("status") or "").strip().lower()
        allowed = {GRN.Status.DRAFT, GRN.Status.CONFIRMED}
        if target not in allowed:
            messages.error(request, "Invalid status.")
            return redirect(self.success_url)
        if target == grn.status:
            messages.info(request, f"{grn.grn_number} is already {grn.get_status_display()}.")
            return redirect(self.success_url)
        if target == GRN.Status.CONFIRMED:
            try:
                confirm_grn(grn=grn, actor=request.user)
            except ValidationError as exc:
                messages.error(request, str(exc))
                return redirect(self.success_url)
            messages.success(request, f"{grn.grn_number} marked as Confirmed.")
            return redirect(self.success_url)

        grn.status = GRN.Status.DRAFT
        grn.confirmed_by = None
        grn.confirmed_at = None
        grn.save(update_fields=["status", "confirmed_by", "confirmed_at"])
        log_grn_action(
            grn,
            user=request.user,
            action=GRNActionLog.Action.REVERTED_TO_DRAFT,
            details={"snapshot": grn_audit_snapshot(grn)},
        )
        messages.warning(request, f"{grn.grn_number} set to Draft.")
        return redirect(self.success_url)


class GRNDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    success_url = reverse_lazy("grn-list")
    permission_required = "suppliers.delete_grn"

    def post(self, request, *args, **kwargs):
        from suppliers.grn_audit import log_grn_deleted

        grn = get_object_or_404(GRN, pk=kwargs["pk"])
        branch_ids = get_user_branch_ids(request.user)
        if branch_ids is not None:
            if not branch_ids:
                raise Http404
            if grn.branch_id and grn.branch_id not in branch_ids:
                raise Http404
        if grn.status != GRN.Status.DRAFT:
            messages.error(request, "Only Draft GRNs can be deleted.")
            return redirect(self.success_url)
        ref = grn.grn_number
        log_grn_deleted(grn, user=request.user)
        grn.delete()
        messages.success(request, f"{ref} deleted.")
        return redirect(self.success_url)


class FarmerGoodsIssueView(LoginRequiredMixin, PermissionRequiredMixin, View):
    template_name = "suppliers/farmer_goods_issue_form.html"
    success_url = reverse_lazy("grn-list")
    permission_required = "suppliers.add_farmergoodsissue"

    def get(self, request):
        initial = {}
        default_bid = get_default_branch_id(request.user)
        if default_bid:
            initial["from_branch"] = default_bid
        form = FarmerGoodsIssueHeaderForm(user=request.user, initial=initial)
        FormSet = get_farmer_goods_issue_line_formset()
        formset = FormSet(form_kwargs={"from_branch_id": initial.get("from_branch")})
        return render(request, self.template_name, farmer_goods_issue_form_context(request, form, formset))

    def post(self, request):
        form = FarmerGoodsIssueHeaderForm(request.POST, user=request.user)
        FormSet = get_farmer_goods_issue_line_formset()
        formset = FormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            lines = []
            for f in formset.forms:
                cd = getattr(f, "cleaned_data", None) or {}
                if cd.get("product") and cd.get("quantity"):
                    lines.append((cd["product"], cd["quantity"]))
            if not lines:
                form.add_error(None, "Add at least one product line with quantity.")
                return render(
                    request,
                    self.template_name,
                    farmer_goods_issue_form_context(request, form, formset),
                )
            try:
                _, batch_ref = issue_goods_batch(
                    line_items=lines,
                    issue_to_type=form.cleaned_data["issue_to_type"],
                    issue_to_id=form.cleaned_data["issue_to_id"],
                    from_branch=form.cleaned_data["from_branch"],
                    issue_date=form.cleaned_data["date"],
                )
            except ValidationError as exc:
                form.add_error(None, str(exc))
                return render(
                    request,
                    self.template_name,
                    farmer_goods_issue_form_context(request, form, formset),
                )
            messages.success(
                request,
                "Goods issued successfully. Collection-point issues await acceptance before payment."
                if form.cleaned_data["issue_to_type"]
                == FarmerGoodsIssue.IssueToType.COLLECTION_POINT
                and farmer_goods_accept_notifications_enabled()
                else "Goods issued successfully.",
            )
            return redirect(reverse("farmer-goods-issue-print", kwargs={"batch_ref": batch_ref}))
        return render(request, self.template_name, farmer_goods_issue_form_context(request, form, formset))


def _apply_farmer_goods_check_issue_filters(qs, request):
    point_id = (request.GET.get("collection_point") or request.GET.get("point") or "").strip()
    farmer_id = (request.GET.get("farmer") or "").strip()
    # When a specific point/farmer is selected, filter by destination only — not from_branch.
    if not point_id.isdigit() and not farmer_id.isdigit():
        branch_id = _effective_branch_id_from_request(request)
        if branch_id:
            qs = qs.filter(from_branch_id=branch_id)

    issue_to_type = (request.GET.get("issue_to_type") or "").strip()
    if issue_to_type in (
        FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
        FarmerGoodsIssue.IssueToType.FARMER,
    ):
        qs = qs.filter(issue_to_type=issue_to_type)

    date_from = (request.GET.get("date_from") or "").strip()
    date_to = (request.GET.get("date_to") or "").strip()
    qs = _apply_issue_date_range(qs, date_from, date_to)

    route_id = (request.GET.get("route") or "").strip()
    if point_id.isdigit():
        qs = qs.filter(
            issue_to_type=FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
            issue_to_id=int(point_id),
        )
    elif farmer_id.isdigit():
        qs = qs.filter(
            issue_to_type=FarmerGoodsIssue.IssueToType.FARMER,
            issue_to_id=int(farmer_id),
        )
    elif route_id.isdigit():
        route_id = int(route_id)
        cp_ids = list(CollectionPoint.objects.filter(route_id=route_id).values_list("pk", flat=True))
        farmer_ids = list(Farmer.objects.filter(route_id=route_id).values_list("pk", flat=True))
        dest_q = Q()
        if cp_ids:
            dest_q |= Q(
                issue_to_type=FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
                issue_to_id__in=cp_ids,
            )
        if farmer_ids:
            dest_q |= Q(
                issue_to_type=FarmerGoodsIssue.IssueToType.FARMER,
                issue_to_id__in=farmer_ids,
            )
        qs = qs.filter(dest_q) if dest_q else qs.none()
    return qs


def _build_farmer_goods_issued_product_rows(qs):
    issues = list(qs.select_related("from_branch", "product").order_by("-date", "id"))
    priced_lines, grand_total = priced_farmer_goods_issue_lines(issues)
    rows = []
    for row in priced_lines:
        issue = row["issue"]
        rows.append(
            {
                "issue_id": issue.id,
                "batch_ref": issue.batch_ref,
                "date": issue.date,
                "product_name": issue.product.name,
                "unit": issue.product.unit or "",
                "quantity": row["quantity"],
                "unit_price": row["unit_price"],
                "line_amount": row["line_amount"],
                "is_settled": issue.is_settled,
            }
        )
    return rows, grand_total


def _farmer_goods_check_context(request):
    allowed_qs = get_allowed_branches_qs(request.user)
    issued_selected_branch = (request.GET.get("branch") or "").strip()
    lookup_selected_branch = issued_selected_branch
    if not lookup_selected_branch:
        default_bid = get_default_branch_id(request.user)
        if default_bid:
            lookup_selected_branch = str(default_bid)

    issue_to_type = (request.GET.get("issue_to_type") or FarmerGoodsIssue.IssueToType.COLLECTION_POINT).strip()
    if issue_to_type not in (
        FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
        FarmerGoodsIssue.IssueToType.FARMER,
    ):
        issue_to_type = FarmerGoodsIssue.IssueToType.COLLECTION_POINT

    selected_route = (request.GET.get("route") or "").strip()
    selected_point = (request.GET.get("collection_point") or "").strip()
    selected_farmer = (request.GET.get("farmer") or "").strip()
    date_from = request.GET.get("date_from", "")
    date_to = request.GET.get("date_to", "")

    issued_selected_branch, selected_route = _resolve_filter_branch_route(
        issued_selected_branch,
        selected_route,
        selected_point,
        selected_farmer,
        issue_to_type,
    )

    route_qs, point_qs, farmer_qs = _route_point_farmer_filter_querysets(
        request,
        selected_branch=issued_selected_branch,
        selected_route=selected_route,
        include_farmers=True,
    )
    inferred_branch_id = _effective_branch_id_from_request(request)

    issued_rows = []
    issued_total = Decimal("0")
    list_ready = False
    if issue_to_type == FarmerGoodsIssue.IssueToType.COLLECTION_POINT and selected_point.isdigit():
        list_ready = True
    elif issue_to_type == FarmerGoodsIssue.IssueToType.FARMER and selected_farmer.isdigit():
        list_ready = True

    if list_ready and request.GET.get("show"):
        base_qs = _farmer_goods_issue_list_base_qs(request.user)
        filtered_qs = _apply_farmer_goods_check_issue_filters(base_qs, request)
        issued_rows, issued_total = _build_farmer_goods_issued_product_rows(filtered_qs)

    filter_q = request.GET.copy()
    filter_q.pop("show", None)
    return {
        "branches": allowed_qs.order_by("name"),
        "lookup_selected_branch": lookup_selected_branch,
        "issued_selected_branch": issued_selected_branch,
        "inferred_branch_id": inferred_branch_id,
        "filter_routes": route_qs,
        "selected_route": selected_route,
        "filter_points": point_qs,
        "selected_collection_point": selected_point,
        "filter_farmers": farmer_qs,
        "selected_farmer": selected_farmer,
        "selected_issue_to_type": issue_to_type,
        "date_from": date_from,
        "date_to": date_to,
        "issued_rows": issued_rows,
        "issued_total": issued_total,
        "list_ready": list_ready,
        "issued_filter_query": filter_q.urlencode(),
        "can_issue_goods": request.user.has_perm("suppliers.add_farmergoodsissue"),
        "can_change_issue": request.user.has_perm("suppliers.change_farmergoodsissue"),
        "can_delete_issue": request.user.has_perm("suppliers.delete_farmergoodsissue"),
        "return_url": request.get_full_path(),
    }


class FarmerGoodsCheckView(LoginRequiredMixin, PermissionRequiredMixin, View):
    template_name = "suppliers/farmer_goods_check.html"
    permission_required = "suppliers.view_farmergoodsissue"

    def get(self, request):
        ctx = _farmer_goods_check_context(request)
        return render(request, self.template_name, ctx)


class FarmerGoodsProductSearchAPI(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "suppliers.view_farmergoodsissue"

    def get(self, request):
        query = (request.GET.get("q") or "").strip()
        branch_raw = (request.GET.get("branch") or "").strip()
        if len(query) < 1:
            return JsonResponse({"results": []})

        qs = Product.objects.filter(status=Product.Status.ACTIVE)
        qs = qs.filter(name__icontains=query).order_by("name")[:25]

        results = [
            {"id": p.id, "name": p.name, "unit": p.unit or ""}
            for p in qs
        ]
        return JsonResponse({"results": results, "branch": branch_raw})


class FarmerGoodsCheckAPI(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "suppliers.view_farmergoodsissue"

    def get(self, request):
        branch_raw = (request.GET.get("branch") or "").strip()
        product_raw = (request.GET.get("product") or "").strip()
        if not branch_raw.isdigit() or not product_raw.isdigit():
            return JsonResponse({"detail": "branch and product are required."}, status=400)

        allowed_ids = list(get_allowed_branches_qs(request.user).values_list("id", flat=True))
        branch_id = int(branch_raw)
        if not request.user.is_superuser and branch_id not in allowed_ids:
            return JsonResponse({"detail": "Branch not allowed."}, status=403)

        try:
            result = check_farmer_goods_issue_line(
                from_branch_id=branch_id,
                product_id=int(product_raw),
                quantity="1",
            )
        except ValidationError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)

        payload = {
            key: (str(value) if isinstance(value, Decimal) else value)
            for key, value in result.items()
        }
        return JsonResponse(payload)


class FarmerGoodsIssuePrintView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "suppliers/farmer_goods_issue_print.html"
    permission_required = "suppliers.view_farmergoodsissue"

    def get_template_names(self):
        if self.request.GET.get("panel") == "payment":
            return ["suppliers/farmer_goods_issue_payment_embed.html"]
        if self.request.GET.get("embed") == "1":
            return ["suppliers/farmer_goods_issue_embed.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        batch_ref = self.kwargs["batch_ref"]
        issues = list(
            FarmerGoodsIssue.objects.select_related("product", "from_branch")
            .filter(batch_ref=batch_ref)
            .order_by("id")
        )
        if not issues:
            raise Http404("Issue receipt not found.")
        head = issues[0]
        branch_ids = get_user_branch_ids(self.request.user)
        if branch_ids is not None:
            if not branch_ids:
                raise Http404
            if head.from_branch_id and head.from_branch_id not in branch_ids:
                raise Http404
        issue_lines, issue_total = priced_farmer_goods_issue_lines(issues)
        ctx["issues"] = issues
        ctx["issue_lines"] = issue_lines
        ctx["issue_total"] = issue_total
        ctx["batch_ref"] = batch_ref
        ctx["issue_head"] = head
        ctx["is_branch_transfer"] = head.issue_to_type == FarmerGoodsIssue.IssueToType.BRANCH
        ctx["destination_name"] = farmer_goods_destination_label(head)
        ctx["from_branch_name"] = head.from_branch.name if head.from_branch_id else "—"
        ctx["settlement_summary"] = goods_issue_batch_settlement_summary(issues)
        ctx["driver_status"] = head.driver_status
        ctx["awaiting_driver"] = head.driver_status == FarmerGoodsIssue.DriverStatus.PENDING
        ctx["driver_rejected"] = head.driver_status == FarmerGoodsIssue.DriverStatus.REJECTED
        payment_eligible = (
            head.issue_to_type != FarmerGoodsIssue.IssueToType.COLLECTION_POINT
            or head.driver_status == FarmerGoodsIssue.DriverStatus.ACCEPTED
        )
        ctx["payment_eligible"] = payment_eligible
        ctx["payment_pending_amount"] = (
            sum(row["line_amount"] for row in issue_lines if not row["issue"].is_settled)
            if payment_eligible
            else 0
        )
        ctx["payment_settled_amount"] = sum(
            row["line_amount"] for row in issue_lines if row["issue"].is_settled
        )
        ctx["embed"] = self.request.GET.get("embed") == "1"
        ctx["print_pos"] = self.request.GET.get("pos") == "1"
        ctx["autoprint"] = self.request.GET.get("autoprint") == "1"
        next_url = (self.request.GET.get("next") or "").strip()
        if next_url.startswith("/") and not next_url.startswith("//"):
            ctx["payment_next_url"] = next_url
        else:
            ctx["payment_next_url"] = self.request.get_full_path()

        can_change = self.request.user.has_perm("suppliers.change_farmergoodsissue")
        can_delete = self.request.user.has_perm("suppliers.delete_farmergoodsissue")
        ctx["can_change_farmer_goods"] = can_change
        ctx["can_delete_farmer_goods"] = can_delete
        settlement_status = (ctx["settlement_summary"] or {}).get("settlement_status")
        allow_line_actions = (
            ctx["embed"]
            and not ctx["is_branch_transfer"]
            and settlement_status not in ("settled", "rejected", "n/a")
            and (can_change or can_delete)
        )
        ctx["allow_goods_line_actions"] = allow_line_actions
        ctx["line_edit_products"] = []
        if allow_line_actions and can_change:
            available_ids = {
                issue.product_id for issue in issues if issue.product_id
            }
            if head.from_branch_id:
                available_ids.update(
                    BranchStock.objects.filter(
                        branch_id=head.from_branch_id,
                        quantity__gt=0,
                        product__status=Product.Status.ACTIVE,
                    ).values_list("product_id", flat=True)
                )
            products = list(
                Product.objects.filter(id__in=available_ids)
                .order_by("name")
                .values("id", "name", "unit")
            )
            for product in products:
                price = latest_farmer_goods_unit_price(
                    product["id"], head.from_branch_id
                ) or Decimal("0")
                product["unit_price"] = str(price.quantize(Decimal("0.01")))
            ctx["line_edit_products"] = products
        ctx["goods_header_branches"] = []
        ctx["goods_header_destinations"] = []
        ctx["allow_goods_header_edit"] = bool(allow_line_actions and can_change)
        ctx["goods_line_add_url"] = ""
        ctx["goods_header_update_url"] = ""
        if allow_line_actions and can_change:
            ctx["goods_line_add_url"] = reverse(
                "farmer-goods-issue-line-add", kwargs={"batch_ref": batch_ref}
            )
            ctx["goods_header_update_url"] = reverse(
                "farmer-goods-issue-header", kwargs={"batch_ref": batch_ref}
            )
            allowed_branches = get_allowed_branches_qs(self.request.user).order_by("name")
            ctx["goods_header_branches"] = [
                {"id": branch.id, "label": str(branch)} for branch in allowed_branches
            ]
            if head.issue_to_type == FarmerGoodsIssue.IssueToType.COLLECTION_POINT:
                points_qs = CollectionPoint.objects.select_related("route", "route__branch")
                if not self.request.user.is_superuser:
                    branch_ids = list(allowed_branches.values_list("id", flat=True))
                    points_qs = (
                        points_qs.filter(route__branch_id__in=branch_ids)
                        if branch_ids
                        else points_qs.none()
                    )
                ctx["goods_header_destinations"] = [
                    {
                        "id": point.id,
                        "label": f"{point.number} {point.name}".strip(),
                        "branch_id": point.route.branch_id if point.route_id else None,
                    }
                    for point in points_qs.order_by("route__code", "number")
                ]
            elif head.issue_to_type == FarmerGoodsIssue.IssueToType.FARMER:
                farmers_qs = Farmer.objects.select_related("route")
                if not self.request.user.is_superuser:
                    branch_ids = list(allowed_branches.values_list("id", flat=True))
                    farmers_qs = (
                        farmers_qs.filter(branch_id__in=branch_ids)
                        if branch_ids
                        else farmers_qs.none()
                    )
                ctx["goods_header_destinations"] = [
                    {
                        "id": farmer.id,
                        "label": farmer.common_name or farmer.full_name or str(farmer),
                        "branch_id": farmer.branch_id,
                    }
                    for farmer in farmers_qs.order_by("common_name", "full_name")
                ]
        ctx["goods_header_branches_json"] = json.dumps(ctx["goods_header_branches"])
        ctx["goods_header_destinations_json"] = json.dumps(ctx["goods_header_destinations"])
        return ctx


class FarmerGoodsIssueBatchSettleView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "reports.record_manual_settlement"

    def post(self, request, batch_ref):
        branch_ids = get_user_branch_ids(request.user)
        issues = list(
            FarmerGoodsIssue.objects.filter(batch_ref=batch_ref).select_related("from_branch")
        )
        if not issues:
            raise Http404("Issue receipt not found.")
        head = issues[0]
        if branch_ids is not None:
            if not branch_ids:
                raise Http404
            if head.from_branch_id and head.from_branch_id not in branch_ids:
                raise Http404
        action = (request.POST.get("action") or "").strip().lower()
        if not action:
            settled = (request.POST.get("settled") or "").strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
            }
            action = "full" if settled else "unsettle_all"
        line_ids = request.POST.getlist("line_ids")
        settled_at = None
        settlement_note = ""
        if action in {"full", "partial"}:
            try:
                settlement_date, settlement_note = settlement_from_request(request)
                settled_at = settlement_datetime_from_date(settlement_date)
            except ValidationError as exc:
                messages.error(request, exc.messages[0] if exc.messages else str(exc))
                next_url = (request.POST.get("next") or "").strip()
                if next_url.startswith("/") and not next_url.startswith("//"):
                    return redirect(next_url)
                return redirect("farmer-goods-issue-list")
        try:
            result = record_goods_receipt_payment(
                batch_ref,
                action=action,
                line_ids=line_ids,
                settled_at=settled_at,
                settlement_note=settlement_note,
            )
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
            next_url = (request.POST.get("next") or "").strip()
            if next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect("farmer-goods-issue-list")
        updated = result["updated"]
        if action == "full":
            if updated:
                messages.success(request, "Full payment recorded for this receipt.")
            else:
                messages.info(request, "All lines on this receipt were already paid.")
        elif action == "partial":
            if updated:
                messages.success(
                    request,
                    f"Partial payment recorded for {updated} line{'s' if updated != 1 else ''}.",
                )
            else:
                messages.info(request, "No lines were updated.")
        elif action == "unsettle_all":
            if updated:
                messages.success(request, "All lines marked as pending again.")
            else:
                messages.info(request, "No paid lines to undo.")
        elif action == "unsettle_lines":
            if updated:
                messages.success(
                    request,
                    f"Payment undone for {updated} line{'s' if updated != 1 else ''}.",
                )
            else:
                messages.info(request, "No paid lines were updated.")
        next_url = (request.POST.get("next") or "").strip()
        if next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
        return redirect("farmer-goods-issue-list")


class FarmerGoodsIssueListView(LoginRequiredMixin, PermissionRequiredMixin, View):
    template_name = "suppliers/farmer_goods_issue_list.html"
    permission_required = "suppliers.view_farmergoodsissue"

    def get(self, request):
        qs = _apply_farmer_goods_issue_list_filters(
            _farmer_goods_issue_list_base_qs(request.user),
            request,
        )
        batches, type_counts = _build_farmer_goods_issue_batches(qs)
        valid_types = {
            FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
            FarmerGoodsIssue.IssueToType.FARMER,
            FarmerGoodsIssue.IssueToType.BRANCH,
        }
        selected_type = (request.GET.get("type") or "").strip()
        if selected_type not in valid_types:
            selected_type = ""
        if selected_type:
            filtered_batches = [row for row in batches if row["issue_to_type"] == selected_type]
        else:
            filtered_batches = batches
        filtered_total = sum((row["batch_amount"] for row in filtered_batches), Decimal("0"))
        ctx = _farmer_goods_issue_list_filter_context(request)
        ctx.update(
            {
                "batches": filtered_batches,
                "selected_type": selected_type,
                "grand_total": filtered_total.quantize(Decimal("0.01")),
                "type_counts": {
                    "all": len(batches),
                    "collection_point": type_counts.get(
                        FarmerGoodsIssue.IssueToType.COLLECTION_POINT, 0
                    ),
                    "farmer": type_counts.get(FarmerGoodsIssue.IssueToType.FARMER, 0),
                    "branch": type_counts.get(FarmerGoodsIssue.IssueToType.BRANCH, 0),
                },
            }
        )
        return render(request, self.template_name, ctx)

    def post(self, request):
        if not request.user.is_superuser:
            messages.error(request, "Only superusers can bulk delete issue receipts.")
            return _farmer_goods_issue_list_redirect(
                request, (request.POST.get("redirect_query") or "").strip()
            )

        action = (request.POST.get("bulk_action") or "").strip()
        redirect_query = (request.POST.get("redirect_query") or "").strip()

        if action not in {"delete_selected", "delete_all"}:
            messages.error(request, "Invalid bulk action.")
            return _farmer_goods_issue_list_redirect(request, redirect_query)

        allowed_refs = set(
            _farmer_goods_issue_list_filtered_qs(request).values_list("batch_ref", flat=True)
        )
        if action == "delete_all":
            batch_refs = sorted(allowed_refs)
        else:
            batch_refs = [
                ref.strip()
                for ref in request.POST.getlist("selected_batch_refs")
                if ref.strip() in allowed_refs
            ]

        if not batch_refs:
            messages.warning(request, "No issue receipts selected.")
            return _farmer_goods_issue_list_redirect(request, redirect_query)

        deleted_batches = 0
        deleted_lines = 0
        errors = []
        for batch_ref in batch_refs:
            try:
                deleted_lines += delete_farmer_goods_issue_batch(batch_ref=batch_ref)
                deleted_batches += 1
            except ValidationError as exc:
                err_msg = exc.messages[0] if exc.messages else str(exc)
                short_ref = batch_ref[:8].upper() if batch_ref else "?"
                errors.append(f"{short_ref}…: {err_msg}")

        if deleted_batches:
            messages.success(
                request,
                f"Deleted {deleted_batches} issue receipt(s) ({deleted_lines} line(s)).",
            )
        for err in errors[:5]:
            messages.error(request, err)
        if len(errors) > 5:
            messages.error(request, f"{len(errors) - 5} more deletion(s) failed.")
        return _farmer_goods_issue_list_redirect(request, redirect_query)


class FarmerGoodsIssueUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    template_name = "suppliers/farmer_goods_issue_form.html"
    permission_required = "suppliers.change_farmergoodsissue"

    def _get_issues_for_batch(self, request, batch_ref):
        qs = (
            FarmerGoodsIssue.objects.filter(batch_ref=batch_ref)
            .select_related("from_branch", "product")
            .order_by("id")
        )
        if not qs.exists():
            raise Http404
        head = qs.first()
        branch_ids = get_user_branch_ids(request.user)
        if branch_ids is not None:
            if not head.from_branch_id or head.from_branch_id not in branch_ids:
                raise Http404
        return list(qs)

    def get(self, request, batch_ref):
        issues = self._get_issues_for_batch(request, batch_ref)
        head = issues[0]
        initial = {
            "from_branch": head.from_branch_id,
            "issue_to_type": head.issue_to_type,
            "date": timezone.localtime(head.date).date(),
        }
        if head.issue_to_type == FarmerGoodsIssue.IssueToType.BRANCH:
            initial["issue_to_branch"] = head.issue_to_id
        elif head.issue_to_type == FarmerGoodsIssue.IssueToType.COLLECTION_POINT:
            initial["issue_to_collection_point"] = head.issue_to_id
        elif head.issue_to_type == FarmerGoodsIssue.IssueToType.FARMER:
            initial["issue_to_farmer"] = head.issue_to_id
        form = FarmerGoodsIssueHeaderForm(user=request.user, initial=initial)
        FormSet = get_farmer_goods_issue_line_formset()
        initial_lines = [{"product": row.product_id, "quantity": row.quantity} for row in issues]
        formset = FormSet(
            initial=initial_lines,
            form_kwargs={"from_branch_id": head.from_branch_id},
        )
        return render(
            request,
            self.template_name,
            farmer_goods_issue_form_context(
                request,
                form,
                formset,
                is_edit=True,
                edit_batch_ref=batch_ref,
            ),
        )

    def post(self, request, batch_ref):
        self._get_issues_for_batch(request, batch_ref)
        form = FarmerGoodsIssueHeaderForm(request.POST, user=request.user)
        FormSet = get_farmer_goods_issue_line_formset()
        branch_id = request.POST.get("from_branch")
        formset = FormSet(
            request.POST,
            form_kwargs={"from_branch_id": int(branch_id) if branch_id and str(branch_id).isdigit() else None},
        )
        if form.is_valid() and formset.is_valid():
            lines = []
            for f in formset.forms:
                cd = getattr(f, "cleaned_data", None) or {}
                if cd.get("product") and cd.get("quantity"):
                    lines.append((cd["product"], cd["quantity"]))
            if not lines:
                form.add_error(None, "Add at least one product line with quantity.")
                return render(
                    request,
                    self.template_name,
                    farmer_goods_issue_form_context(
                        request,
                        form,
                        formset,
                        is_edit=True,
                        edit_batch_ref=batch_ref,
                    ),
                )
            try:
                update_farmer_goods_issue_batch(
                    batch_ref=batch_ref,
                    line_items=lines,
                    issue_to_type=form.cleaned_data["issue_to_type"],
                    issue_to_id=form.cleaned_data["issue_to_id"],
                    from_branch=form.cleaned_data["from_branch"],
                    issue_date=form.cleaned_data["date"],
                )
            except ValidationError as exc:
                form.add_error(None, str(exc))
                return render(
                    request,
                    self.template_name,
                    farmer_goods_issue_form_context(
                        request,
                        form,
                        formset,
                        is_edit=True,
                        edit_batch_ref=batch_ref,
                    ),
                )
            messages.success(request, "Issue receipt updated.")
            return redirect(reverse("farmer-goods-issue-print", kwargs={"batch_ref": batch_ref}))
        return render(
            request,
            self.template_name,
            farmer_goods_issue_form_context(
                request,
                form,
                formset,
                is_edit=True,
                edit_batch_ref=batch_ref,
            ),
        )


class FarmerGoodsIssueDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    success_url = reverse_lazy("farmer-goods-issue-list")
    permission_required = "suppliers.delete_farmergoodsissue"

    def post(self, request, batch_ref):
        head = (
            FarmerGoodsIssue.objects.filter(batch_ref=batch_ref)
            .select_related("from_branch")
            .order_by("id")
            .first()
        )
        if not head:
            raise Http404
        branch_ids = get_user_branch_ids(request.user)
        if branch_ids is not None:
            if not head.from_branch_id or head.from_branch_id not in branch_ids:
                raise Http404
        try:
            deleted_count = delete_farmer_goods_issue_batch(batch_ref=batch_ref)
        except ValidationError as exc:
            messages.error(request, str(exc))
            redirect_query = (request.POST.get("redirect_query") or "").strip()
            if redirect_query:
                return redirect(f"{reverse('farmer-goods-issue-list')}?{redirect_query}")
            next_url = (request.POST.get("next") or "").strip()
            if next_url.startswith("/") and not next_url.startswith("//"):
                return redirect(next_url)
            return redirect(self.success_url)
        messages.success(request, f"Issue receipt deleted ({deleted_count} line(s)).")
        redirect_query = (request.POST.get("redirect_query") or "").strip()
        if redirect_query:
            return redirect(f"{reverse('farmer-goods-issue-list')}?{redirect_query}")
        next_url = (request.POST.get("next") or "").strip()
        if next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
        return redirect(self.success_url)


def _farmer_goods_issue_line_for_user(request, issue_id):
    issue = get_object_or_404(
        FarmerGoodsIssue.objects.select_related("product", "from_branch"),
        pk=issue_id,
    )
    branch_ids = get_user_branch_ids(request.user)
    if branch_ids is not None:
        if not issue.from_branch_id or issue.from_branch_id not in branch_ids:
            raise Http404
    return issue


def _farmer_goods_batch_head_for_user(request, batch_ref):
    issues = list(
        FarmerGoodsIssue.objects.select_related("product", "from_branch")
        .filter(batch_ref=batch_ref)
        .order_by("id")
    )
    if not issues:
        raise Http404("Issue receipt not found.")
    head = issues[0]
    branch_ids = get_user_branch_ids(request.user)
    if branch_ids is not None:
        if not head.from_branch_id or head.from_branch_id not in branch_ids:
            raise Http404
    return issues, head


def _farmer_goods_wants_json(request):
    accept = (request.headers.get("Accept") or "").lower()
    return "application/json" in accept or request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _farmer_goods_first_form_error(form):
    if form.non_field_errors():
        return str(form.non_field_errors()[0])
    for errors in form.errors.values():
        if errors:
            return str(errors[0])
    return "Could not save this line."


def _validation_error_message(exc):
    messages_list = getattr(exc, "messages", None)
    if messages_list:
        return str(messages_list[0])
    return str(exc)


class FarmerGoodsIssueLineEditView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "suppliers.change_farmergoodsissue"
    template_name = "suppliers/farmer_goods_issue_line_edit_embed.html"

    def get(self, request, issue_id):
        from .forms import FarmerGoodsIssueLineEntryForm

        issue = _farmer_goods_issue_line_for_user(request, issue_id)
        form = FarmerGoodsIssueLineEntryForm(
            initial={"product": issue.product_id, "quantity": issue.quantity},
            from_branch_id=issue.from_branch_id,
        )
        form.fields["product"].required = True
        form.fields["quantity"].required = True
        return render(
            request,
            self.template_name,
            {
                "form": form,
                "issue": issue,
                "next_url": (request.GET.get("next") or "").strip(),
            },
        )

    def post(self, request, issue_id):
        from .forms import FarmerGoodsIssueLineEntryForm

        issue = _farmer_goods_issue_line_for_user(request, issue_id)
        form = FarmerGoodsIssueLineEntryForm(
            request.POST,
            from_branch_id=issue.from_branch_id,
        )
        form.fields["product"].required = True
        form.fields["quantity"].required = True
        next_url = (request.POST.get("next") or "").strip()
        wants_json = _farmer_goods_wants_json(request)
        if not form.is_valid():
            if wants_json:
                return JsonResponse(
                    {"ok": False, "error": _farmer_goods_first_form_error(form)},
                    status=400,
                )
            return render(
                request,
                self.template_name,
                {"form": form, "issue": issue, "next_url": next_url},
                status=400,
            )
        try:
            new_issue = update_farmer_goods_issue_line(
                issue_id=issue.pk,
                product=form.cleaned_data["product"],
                quantity=form.cleaned_data["quantity"],
            )
        except ValidationError as exc:
            error = _validation_error_message(exc)
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=400)
            form.add_error(None, error)
            return render(
                request,
                self.template_name,
                {"form": form, "issue": issue, "next_url": next_url},
                status=400,
            )
        if wants_json:
            return JsonResponse(
                {
                    "ok": True,
                    "issue_id": new_issue.pk,
                    "batch_ref": new_issue.batch_ref or "",
                }
            )
        messages.success(request, "Issue line updated.")
        if next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
        if issue.batch_ref:
            return redirect("farmer-goods-issue-print", batch_ref=issue.batch_ref)
        return redirect("farmer-goods-issue-list")


class FarmerGoodsIssueLineDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "suppliers.delete_farmergoodsissue"

    def post(self, request, issue_id):
        issue = _farmer_goods_issue_line_for_user(request, issue_id)
        wants_json = _farmer_goods_wants_json(request)
        try:
            result = delete_farmer_goods_issue_line(issue_id=issue.pk)
        except ValidationError as exc:
            error = _validation_error_message(exc)
            if wants_json:
                return JsonResponse({"ok": False, "error": error}, status=400)
            messages.error(request, error)
            result = None
        else:
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "batch_ref": result.get("batch_ref") or "",
                        "remaining": result.get("remaining") or 0,
                    }
                )
            messages.success(request, "Issue line deleted.")
        next_url = (request.POST.get("next") or "").strip()
        if next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
        return redirect("farmer-goods-issue-list")


class FarmerGoodsIssueLineAddView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "suppliers.change_farmergoodsissue"

    def post(self, request, batch_ref):
        from .forms import FarmerGoodsIssueLineEntryForm

        _issues, head = _farmer_goods_batch_head_for_user(request, batch_ref)
        form = FarmerGoodsIssueLineEntryForm(
            request.POST,
            from_branch_id=head.from_branch_id,
        )
        form.fields["product"].required = True
        form.fields["quantity"].required = True
        if not form.is_valid():
            return JsonResponse(
                {"ok": False, "error": _farmer_goods_first_form_error(form)},
                status=400,
            )
        try:
            new_issue = add_farmer_goods_issue_line(
                batch_ref=batch_ref,
                product=form.cleaned_data["product"],
                quantity=form.cleaned_data["quantity"],
            )
        except ValidationError as exc:
            return JsonResponse(
                {"ok": False, "error": _validation_error_message(exc)},
                status=400,
            )
        return JsonResponse(
            {
                "ok": True,
                "issue_id": new_issue.pk,
                "batch_ref": new_issue.batch_ref or batch_ref,
            }
        )


class FarmerGoodsIssueHeaderUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "suppliers.change_farmergoodsissue"

    def post(self, request, batch_ref):
        _issues, _head = _farmer_goods_batch_head_for_user(request, batch_ref)
        issue_date = parse_date((request.POST.get("date") or "").strip())
        if issue_date is None:
            return JsonResponse({"ok": False, "error": "Enter a valid date."}, status=400)
        try:
            issue_to_id = int(request.POST.get("issue_to_id") or "")
        except (TypeError, ValueError):
            return JsonResponse({"ok": False, "error": "Select a destination."}, status=400)
        allowed_branches = get_allowed_branches_qs(request.user)
        from_branch = allowed_branches.filter(pk=request.POST.get("from_branch")).first()
        if from_branch is None:
            return JsonResponse({"ok": False, "error": "Select a valid branch."}, status=400)
        try:
            update_farmer_goods_issue_header(
                batch_ref=batch_ref,
                from_branch=from_branch,
                issue_to_id=issue_to_id,
                issue_date=issue_date,
            )
        except ValidationError as exc:
            return JsonResponse(
                {"ok": False, "error": _validation_error_message(exc)},
                status=400,
            )
        return JsonResponse({"ok": True, "batch_ref": batch_ref})


class InventoryUsageCreateView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    template_name = "suppliers/inventory_usage_form.html"
    form_class = InventoryUsageForm
    success_url = reverse_lazy("grn-list")
    permission_required = "suppliers.add_inventoryusage"

    def form_valid(self, form):
        try:
            record_inventory_usage(**form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, "Inventory usage recorded.")
        return redirect(self.success_url)


class ConsumptionSettlementCreateView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    template_name = "suppliers/consumption_form.html"
    form_class = ConsumptionSettlementForm
    success_url = reverse_lazy("grn-list")
    permission_required = "suppliers.add_consumptionsettlement"

    def form_valid(self, form):
        try:
            consume_goods(**form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, "Consumption recorded.")
        return redirect(self.success_url)


class SupplierPaymentCreateView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    template_name = "suppliers/payment_form.html"
    form_class = SupplierPaymentForm
    success_url = reverse_lazy("supplier-ledger")
    permission_required = "suppliers.add_suppliertransaction"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        record_supplier_payment(
            supplier=form.cleaned_data["supplier"],
            amount=form.cleaned_data["amount"],
            description=form.cleaned_data["description"],
            reference="Payment",
        )
        messages.success(self.request, "Supplier payment recorded.")
        return redirect(self.success_url)


class SupplierCreateAPI(generics.CreateAPIView):
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    permission_classes = [IsAuthenticated, DjangoModelPermissions]


class GRNCreateAPI(generics.CreateAPIView):
    queryset = GRN.objects.all()
    serializer_class = GRNSerializer
    permission_classes = [IsAuthenticated, DjangoModelPermissions]


class ConfirmGRNAPI(generics.CreateAPIView):
    queryset = GRN.objects.all()
    permission_classes = [IsAuthenticated, DjangoModelPermissions]
    serializer_class = ConfirmGRNSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        grn = get_object_or_404(GRN, pk=serializer.validated_data["grn_id"])
        try:
            confirm_grn(grn=grn, actor=request.user)
        except ValidationError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"detail": f"{grn.grn_number} confirmed."}, status=status.HTTP_200_OK)


class FarmerGoodsIssueAPI(generics.CreateAPIView):
    queryset = FarmerGoodsIssue.objects.all()
    serializer_class = FarmerGoodsIssueSerializer
    permission_classes = [IsAuthenticated, DjangoModelPermissions]


class SupplierPaymentAPI(generics.CreateAPIView):
    queryset = SupplierTransaction.objects.all()
    serializer_class = SupplierPaymentSerializer
    permission_classes = [IsAuthenticated, DjangoModelPermissions]


class SupplierLedgerAPI(generics.ListAPIView):
    queryset = SupplierTransaction.objects.select_related("supplier").all()
    serializer_class = SupplierTransactionSerializer
    permission_classes = [IsAuthenticated, DjangoModelPermissions]


class ConsumptionSettlementAPI(generics.CreateAPIView):
    queryset = ConsumptionSettlement.objects.all()
    serializer_class = ConsumptionSettlementSerializer
    permission_classes = [IsAuthenticated, DjangoModelPermissions]


def _serialize_staff_pending_goods_batch(batch, price_map=None):
    from canmee_dairies.context_processors import (
        _serialize_pending_goods_batch_for_context,
    )

    return _serialize_pending_goods_batch_for_context(batch, price_map=price_map)


def _serialize_driver_goods_request_feed_item(req, *, user=None):
    from canmee_dairies.context_processors import (
        _serialize_driver_goods_request_for_context,
    )

    can_reject = bool(
        user is not None and user.has_perm("suppliers.add_farmergoodsissue")
    )
    return _serialize_driver_goods_request_for_context(req, can_reject=can_reject)


def _staff_pending_goods_feed(user):
    from user_management.templatetags.user_tags import (
        user_can_manage_farmer_goods_notifications,
    )

    items = []
    pending_unread = 0
    if (
        user_can_manage_farmer_goods_notifications(user)
        and farmer_goods_accept_notifications_enabled()
    ):
        pending_unread = count_pending_collection_point_batches_for_user(user)
        # Keep feed light for the topbar (full list lives on pending-accept page).
        batches = pending_collection_point_batches_for_user(user, limit=8)
        all_lines = []
        for batch in batches:
            all_lines.extend(batch.get("lines") or [])
        price_map = approx_price_map_for_issues(all_lines)
        items = [_serialize_staff_pending_goods_batch(batch, price_map) for batch in batches]

    driver_requests = []
    driver_unread = 0
    if user_can_view_driver_farmer_goods_requests(user):
        request_qs = pending_driver_farmer_goods_requests_for_user(user)
        driver_unread = request_qs.count()
        driver_requests = [
            _serialize_driver_goods_request_feed_item(req, user=user)
            for req in request_qs.select_related(
                "collection_point", "route"
            ).prefetch_related("lines")[:8]
        ]

    return {
        "ok": True,
        "unread": pending_unread,
        "items": items,
        "driver_request_unread": driver_unread,
        "driver_requests": driver_requests,
    }


class FarmerGoodsStaffNotificationMixin:
    """Restrict to Admin, superuser, or Branch Manager."""

    def dispatch(self, request, *args, **kwargs):
        from django.core.exceptions import PermissionDenied
        from user_management.templatetags.user_tags import (
            user_can_manage_farmer_goods_notifications,
        )

        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not user_can_manage_farmer_goods_notifications(request.user):
            raise PermissionDenied("You cannot manage farmer goods notifications.")
        return super().dispatch(request, *args, **kwargs)


class FarmerGoodsStaffNotificationFeedView(LoginRequiredMixin, View):
    """Pending CP accepts (managers) + driver goods requests (managers + issuers)."""

    def dispatch(self, request, *args, **kwargs):
        from django.core.exceptions import PermissionDenied
        from user_management.templatetags.user_tags import (
            user_can_manage_farmer_goods_notifications,
        )

        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not (
            user_can_manage_farmer_goods_notifications(request.user)
            or user_can_view_driver_farmer_goods_requests(request.user)
        ):
            raise PermissionDenied("You cannot view farmer goods notifications.")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return JsonResponse(_staff_pending_goods_feed(request.user))


class FarmerGoodsStaffAcceptView(LoginRequiredMixin, FarmerGoodsStaffNotificationMixin, View):
    def post(self, request, batch_ref):
        note = (request.POST.get("note") or "").strip()
        try:
            assert_farmer_goods_batch_accessible_to_user(batch_ref, request.user)
            accept_farmer_goods_issue_batch(batch_ref=batch_ref, route=None, note=note)
        except ValidationError as exc:
            message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"ok": False, "error": message}, status=400)
            messages.error(request, message)
            return redirect("farmer-goods-pending-accept-list")
        feed = _staff_pending_goods_feed(request.user)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": True, "action": "accepted", **feed})
        messages.success(request, "Goods issue accepted.")
        return redirect("farmer-goods-pending-accept-list")


class FarmerGoodsStaffRejectView(LoginRequiredMixin, FarmerGoodsStaffNotificationMixin, View):
    def post(self, request, batch_ref):
        note = (request.POST.get("note") or "").strip()
        try:
            assert_farmer_goods_batch_accessible_to_user(batch_ref, request.user)
            reject_farmer_goods_issue_batch(batch_ref=batch_ref, route=None, note=note)
        except ValidationError as exc:
            message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"ok": False, "error": message}, status=400)
            messages.error(request, message)
            return redirect("farmer-goods-pending-accept-list")
        feed = _staff_pending_goods_feed(request.user)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": True, "action": "rejected", **feed})
        messages.success(request, "Goods issue rejected.")
        return redirect("farmer-goods-pending-accept-list")


class FarmerGoodsPendingAcceptListView(
    LoginRequiredMixin, FarmerGoodsStaffNotificationMixin, TemplateView
):
    """Full pending CP issue list for Admin / superuser / Branch Manager."""

    template_name = "suppliers/farmer_goods_pending_accept_list.html"

    def get_context_data(self, **kwargs):
        from datetime import date as date_cls
        from decimal import Decimal
        from django.utils.timesince import timesince
        from django.urls import reverse

        ctx = super().get_context_data(**kwargs)
        batches = pending_collection_point_batches_for_user(self.request.user)
        today = timezone.localdate()
        all_lines = []
        for batch in batches:
            all_lines.extend(batch.get("lines") or [])
        price_map = fifo_price_map_for_issues(all_lines) if all_lines else {}

        rows = []
        total_amount = Decimal("0")
        today_count = 0
        today_amount = Decimal("0")
        branch_names = set()
        route_names = set()
        for batch in batches:
            point = batch["point"]
            route = getattr(point, "route", None)
            issued_at = batch.get("date")
            issued_day = None
            if issued_at is not None:
                issued_day = (
                    timezone.localtime(issued_at).date()
                    if timezone.is_aware(issued_at)
                    else (
                        issued_at.date()
                        if hasattr(issued_at, "date") and not isinstance(issued_at, date_cls)
                        else issued_at
                    )
                )
            is_today = issued_day == today
            lines = batch.get("lines") or []
            amount = Decimal("0")
            product_bits = []
            for issue in lines:
                amount += price_map.get(issue.id, Decimal("0")) or Decimal("0")
                if len(product_bits) < 6 and issue.product_id:
                    product_bits.append(f"{issue.product.name} × {issue.quantity}")
            amount = amount.quantize(Decimal("0.01"))
            total_amount += amount
            if is_today:
                today_count += 1
                today_amount += amount
            branch = batch.get("from_branch")
            if branch:
                branch_names.add(branch.name)
            if route:
                route_names.add(route.name)
            batch_ref = batch["batch_ref"]
            line_rows = []
            for issue in lines:
                line_amount = (
                    price_map.get(issue.id, Decimal("0")) or Decimal("0")
                ).quantize(Decimal("0.01"))
                line_rows.append(
                    {
                        "product_name": issue.product.name if issue.product_id else "—",
                        "quantity": str(issue.quantity),
                        "amount": str(line_amount),
                    }
                )
            review_url = reverse(
                "farmer-goods-issue-print", kwargs={"batch_ref": batch_ref}
            )
            review = {
                "batch_ref": batch_ref,
                "title": f"Goods for {point.number} — {point.name}",
                "body": (
                    f"{len(lines)} line(s) from {branch.name if branch else '—'}. "
                    "Accept to apply stock & payment sheet, or reject to cancel."
                ),
                "point_label": f"{point.number} — {point.name}",
                "branch_label": branch.name if branch else "—",
                "line_count": len(lines),
                "total_amount": str(amount),
                "lines": line_rows,
                "created_ago": f"{timesince(issued_at)} ago" if issued_at else "",
                "review_url": review_url,
                "accept_url": reverse(
                    "farmer-goods-staff-accept", kwargs={"batch_ref": batch_ref}
                ),
                "reject_url": reverse(
                    "farmer-goods-staff-reject", kwargs={"batch_ref": batch_ref}
                ),
            }
            rows.append(
                {
                    "batch_ref": batch_ref,
                    "point_label": f"{point.number} — {point.name}",
                    "branch_label": branch.name if branch else "—",
                    "route_label": route.name if route else "—",
                    "route_id": route.pk if route else "",
                    "branch_id": branch.pk if branch else "",
                    "date": issued_at,
                    "date_sort": (
                        issued_at.isoformat()
                        if hasattr(issued_at, "isoformat")
                        else str(issued_at or "")
                    ),
                    "is_today": is_today,
                    "created_ago": f"{timesince(issued_at)} ago" if issued_at else "",
                    "line_count": len(lines),
                    "total_amount": str(amount),
                    "amount_value": str(amount),
                    "products_summary": ", ".join(product_bits)
                    + ("…" if len(lines) > 6 else ""),
                    "review_url": review_url,
                    "accept_url": review["accept_url"],
                    "reject_url": review["reject_url"],
                    "review": review,
                }
            )
        pending_count = len(rows)
        ctx["pending_batches"] = rows
        ctx["pending_review_map"] = {
            row["batch_ref"]: row["review"] for row in rows
        }
        ctx["pending_count"] = pending_count
        ctx["pending_summary"] = {
            "count": pending_count,
            "total_amount": total_amount.quantize(Decimal("0.01")),
            "today_count": today_count,
            "today_amount": today_amount.quantize(Decimal("0.01")),
            "older_count": pending_count - today_count,
            "branch_count": len(branch_names),
            "route_count": len(route_names),
        }
        ctx["farmer_goods_accept_notifications_enabled"] = (
            farmer_goods_accept_notifications_enabled()
        )
        return ctx


class FarmerGoodsSettingsView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = FarmerGoodsModuleSettings
    form_class = FarmerGoodsModuleSettingsForm
    template_name = "suppliers/farmer_goods_settings.html"
    success_url = reverse_lazy("farmer-goods-settings")

    def test_func(self):
        return self.request.user.is_superuser

    def get_object(self, queryset=None):
        return FarmerGoodsModuleSettings.get_solo()

    def form_valid(self, form):
        previous_enabled = (
            FarmerGoodsModuleSettings.objects.filter(pk=form.instance.pk)
            .values_list("enable_accept_notifications", flat=True)
            .first()
        )
        if previous_enabled is None:
            previous_enabled = True
        now_enabled = form.cleaned_data["enable_accept_notifications"]
        form.instance.updated_by = self.request.user
        response = super().form_valid(form)
        if previous_enabled and not now_enabled:
            result = auto_accept_all_pending_farmer_goods_issues()
            applied = result.get("accepted") or 0
            if applied:
                messages.success(
                    self.request,
                    f"Farmer goods accept notifications disabled. "
                    f"{applied} pending issue(s) applied without accepting.",
                )
            else:
                messages.success(
                    self.request,
                    "Farmer goods accept notifications disabled. "
                    "New collection-point issues apply immediately.",
                )
            errors = result.get("errors") or []
            if errors:
                messages.warning(
                    self.request,
                    "Some pending issues could not be applied and still need Accept or Reject: "
                    + "; ".join(errors[:5]),
                )
        elif not previous_enabled and now_enabled:
            messages.success(
                self.request,
                "Farmer goods accept notifications enabled. "
                "New collection-point issues will wait for Accept.",
            )
        else:
            messages.success(self.request, "Farmer goods settings saved.")
        return response


class FarmerGoodsStaffBulkAcceptView(
    LoginRequiredMixin, FarmerGoodsStaffNotificationMixin, View
):
    def post(self, request):
        note = (request.POST.get("note") or "").strip()
        batch_refs = request.POST.getlist("batch_ref")
        if not batch_refs and request.headers.get("x-requested-with") == "XMLHttpRequest":
            try:
                payload = json.loads(request.body.decode("utf-8") or "{}")
            except (TypeError, ValueError, UnicodeDecodeError):
                payload = {}
            batch_refs = payload.get("batch_refs") or payload.get("batch_ref") or []
            if isinstance(batch_refs, str):
                batch_refs = [batch_refs]
            note = (payload.get("note") or note or "").strip()
        try:
            result = bulk_accept_farmer_goods_issue_batches(
                batch_refs=batch_refs, user=request.user, note=note
            )
        except ValidationError as exc:
            message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"ok": False, "error": message}, status=400)
            messages.error(request, message)
            return redirect("farmer-goods-pending-accept-list")

        feed = _staff_pending_goods_feed(request.user)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": True, "action": "bulk_accepted", **result, **feed})

        parts = [f"Accepted {result['accepted_count']} issue(s)."]
        if result["skipped_count"]:
            parts.append(f"Skipped {result['skipped_count']} already accepted.")
        if result["error_count"]:
            parts.append(f"{result['error_count']} failed.")
            first_errors = "; ".join(
                f"{e['batch_ref'][:8]}…: {e['error']}" for e in result["errors"][:3]
            )
            messages.warning(request, " ".join(parts) + f" {first_errors}")
        else:
            messages.success(request, " ".join(parts))
        return redirect("farmer-goods-pending-accept-list")


class FarmerGoodsDriverRequestViewerMixin:
    def dispatch(self, request, *args, **kwargs):
        from django.core.exceptions import PermissionDenied

        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not user_can_view_driver_farmer_goods_requests(request.user):
            raise PermissionDenied("You cannot view driver farmer goods requests.")
        return super().dispatch(request, *args, **kwargs)


class FarmerGoodsDriverRequestListView(
    LoginRequiredMixin, FarmerGoodsDriverRequestViewerMixin, TemplateView
):
    template_name = "suppliers/farmer_goods_driver_request_list.html"

    def get_context_data(self, **kwargs):
        from django.db.models import Count

        ctx = super().get_context_data(**kwargs)
        requests = driver_farmer_goods_requests_for_user(self.request.user)
        status_rows = (
            driver_farmer_goods_requests_for_user(
                self.request.user, with_line_count=False
            )
            .order_by()
            .values("status")
            .annotate(total=Count("id"))
        )
        counts = {
            "all": 0,
            DriverFarmerGoodsRequest.Status.PENDING: 0,
            DriverFarmerGoodsRequest.Status.ISSUED: 0,
            DriverFarmerGoodsRequest.Status.CANCELLED: 0,
        }
        for row in status_rows:
            key = row["status"]
            total = int(row["total"] or 0)
            counts["all"] += total
            if key in counts:
                counts[key] = total
        filter_status = (self.request.GET.get("status") or "pending").strip().lower()
        if filter_status not in {
            "all",
            DriverFarmerGoodsRequest.Status.PENDING,
            DriverFarmerGoodsRequest.Status.ISSUED,
            DriverFarmerGoodsRequest.Status.CANCELLED,
        }:
            filter_status = "pending"
        ctx["requests"] = requests
        ctx["status_tab_counts"] = counts
        ctx["filter_status"] = filter_status
        ctx["can_issue"] = self.request.user.has_perm("suppliers.add_farmergoodsissue")
        return ctx


def _get_driver_farmer_goods_request_for_user(user, pk, *, pending_only=False):
    from user_management.templatetags.user_tags import user_is_admin_or_superuser
    from suppliers.services import _branch_ids_for_driver_goods_request_viewer

    pending = pending_driver_farmer_goods_requests_for_user(user).filter(pk=pk).first()
    if pending:
        return pending
    if pending_only:
        raise Http404
    # Allow opening issued/cancelled requests in the same branch scope (read-only).
    base = DriverFarmerGoodsRequest.objects.filter(pk=pk).select_related(
        "route",
        "route__branch",
        "collection_point",
        "collection_point__route",
        "from_branch",
        "fulfilled_by",
    ).prefetch_related("lines__product")
    obj = base.first()
    if not obj:
        raise Http404
    if user_is_admin_or_superuser(user):
        return obj
    branch_ids = _branch_ids_for_driver_goods_request_viewer(user)
    if branch_ids is None:
        return obj
    if not branch_ids or not (
        (obj.from_branch_id and obj.from_branch_id in branch_ids)
        or (obj.route.branch_id in branch_ids)
        or (obj.collection_point.route.branch_id in branch_ids)
    ):
        raise Http404
    return obj


class FarmerGoodsDriverRequestFulfillView(
    LoginRequiredMixin, FarmerGoodsDriverRequestViewerMixin, View
):
    template_name = "suppliers/farmer_goods_driver_request_fulfill.html"

    def _get_request_obj(self, request, pk):
        return _get_driver_farmer_goods_request_for_user(request.user, pk)

    def get(self, request, pk):
        req = self._get_request_obj(request, pk)
        lines = []
        for line in req.lines.select_related("product").order_by("id"):
            available = _global_farmer_goods_available_qty(line.product_id)
            will_issue = min(line.quantity, max(available, Decimal("0"))).quantize(
                Decimal("0.01")
            )
            lines.append(
                {
                    "line": line,
                    "available": available.quantize(Decimal("0.01")),
                    "will_issue": will_issue if available > 0 else Decimal("0.00"),
                    "skip": available <= 0,
                    "partial": available > 0 and available < line.quantity,
                }
            )
        can_issue = (
            request.user.has_perm("suppliers.add_farmergoodsissue")
            and req.status == DriverFarmerGoodsRequest.Status.PENDING
        )
        return render(
            request,
            self.template_name,
            {
                "goods_request": req,
                "line_previews": lines,
                "can_issue": can_issue,
                "reject_url": (
                    reverse("farmer-goods-driver-request-reject", kwargs={"pk": req.pk})
                    if can_issue
                    else ""
                ),
            },
        )

    def post(self, request, pk):
        if not request.user.has_perm("suppliers.add_farmergoodsissue"):
            messages.error(request, "You do not have permission to issue farmer goods.")
            return redirect("farmer-goods-driver-request-fulfill", pk=pk)
        req = self._get_request_obj(request, pk)
        note = (request.POST.get("note") or "").strip()
        issue_quantities = {}
        for key, value in request.POST.items():
            if not key.startswith("issue_qty_"):
                continue
            line_id = key.replace("issue_qty_", "", 1).strip()
            if line_id:
                issue_quantities[line_id] = value
        try:
            result = fulfill_driver_farmer_goods_request(
                request_obj=req,
                user=request.user,
                note=note,
                issue_quantities=issue_quantities or None,
            )
        except ValidationError as exc:
            message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            messages.error(request, message)
            return redirect("farmer-goods-driver-request-fulfill", pk=pk)
        skipped = result.get("skipped") or []
        msg = (
            f"Issued {result['issued_count']} line(s) to the collection point. "
            "Driver acceptance is not required for request fulfillments."
        )
        if skipped:
            msg += " Skipped / limited: " + "; ".join(skipped)
        messages.success(request, msg)
        return redirect("farmer-goods-issue-print", batch_ref=result["batch_ref"])


class FarmerGoodsDriverRequestDetailJsonView(
    LoginRequiredMixin, FarmerGoodsDriverRequestViewerMixin, View
):
    """JSON payload for the driver-request View modal."""

    def get(self, request, pk):
        from django.utils.timesince import timesince
        from django.utils.timezone import localtime

        req = _get_driver_farmer_goods_request_for_user(request.user, pk)
        can_issue = (
            request.user.has_perm("suppliers.add_farmergoodsissue")
            and req.status == DriverFarmerGoodsRequest.Status.PENDING
        )
        lines = []
        for line in req.lines.select_related("product").order_by("id"):
            unit = (line.product.unit or "").strip() if line.product_id else ""
            lines.append(
                {
                    "product_name": line.product.name if line.product_id else "—",
                    "quantity": str(line.quantity),
                    "issued_quantity": str(line.issued_quantity),
                    "unit": unit,
                }
            )
        requested_at = localtime(req.requested_at) if req.requested_at else None
        fulfilled_at = localtime(req.fulfilled_at) if req.fulfilled_at else None
        return JsonResponse(
            {
                "ok": True,
                "id": req.pk,
                "status": req.status,
                "status_label": req.get_status_display(),
                "route_name": req.route.name if req.route_id else "—",
                "point_label": (
                    f"{req.collection_point.number} — {req.collection_point.name}"
                    if req.collection_point_id
                    else "—"
                ),
                "requested_at": (
                    requested_at.strftime("%b %d, %Y %H:%M") if requested_at else ""
                ),
                "requested_ago": (
                    f"{timesince(req.requested_at)} ago" if req.requested_at else ""
                ),
                "fulfilled_at": (
                    fulfilled_at.strftime("%b %d, %Y %H:%M") if fulfilled_at else ""
                ),
                "fulfilled_by": (
                    req.fulfilled_by.get_username() if req.fulfilled_by_id else ""
                ),
                "note": req.note or "",
                "fulfill_note": req.fulfill_note or "",
                "skipped_out_of_stock": req.skipped_out_of_stock or "",
                "issued_batch_ref": req.issued_batch_ref or "",
                "fulfill_url": reverse(
                    "farmer-goods-driver-request-fulfill", kwargs={"pk": req.pk}
                ),
                "reject_url": (
                    reverse(
                        "farmer-goods-driver-request-reject", kwargs={"pk": req.pk}
                    )
                    if can_issue
                    else ""
                ),
                "receipt_url": (
                    reverse(
                        "farmer-goods-issue-print",
                        kwargs={"batch_ref": req.issued_batch_ref},
                    )
                    if req.issued_batch_ref
                    else ""
                ),
                "receipt_embed_url": (
                    reverse(
                        "farmer-goods-issue-print",
                        kwargs={"batch_ref": req.issued_batch_ref},
                    )
                    + "?embed=1"
                    if req.issued_batch_ref
                    else ""
                ),
                "can_issue": can_issue,
                "lines": lines,
            }
        )


class FarmerGoodsDriverRequestRejectView(
    LoginRequiredMixin, FarmerGoodsDriverRequestViewerMixin, View
):
    """Staff reject (cancel) a pending driver goods request — no stock change."""

    def post(self, request, pk):
        if not request.user.has_perm("suppliers.add_farmergoodsissue"):
            message = "You do not have permission to reject driver goods requests."
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"ok": False, "error": message}, status=403)
            messages.error(request, message)
            return redirect("farmer-goods-driver-request-list")

        note = (request.POST.get("note") or "").strip()
        try:
            req = _get_driver_farmer_goods_request_for_user(
                request.user, pk, pending_only=True
            )
            cancel_driver_farmer_goods_request(
                request_obj=req, user=request.user, note=note
            )
        except Http404:
            message = "Pending driver goods request not found."
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"ok": False, "error": message}, status=404)
            messages.error(request, message)
            return redirect("farmer-goods-driver-request-list")
        except ValidationError as exc:
            message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"ok": False, "error": message}, status=400)
            messages.error(request, message)
            return redirect("farmer-goods-driver-request-fulfill", pk=pk)

        feed = _staff_pending_goods_feed(request.user)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": True, "action": "rejected", **feed})
        messages.success(
            request,
            "Driver goods request rejected. No stock was issued.",
        )
        return redirect("farmer-goods-driver-request-list")

