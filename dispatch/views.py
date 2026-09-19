from decimal import Decimal, InvalidOperation

from canmee_dairies.mixins import AnyPermissionRequiredMixin, ModelFormPageTitleMixin
from branches.utils import (
    filter_by_user_branches,
    filter_m2m_by_user_branches,
    get_allowed_branches_qs,
    get_user_branch_ids,
    master_branch_option_rows,
)
from canmee_dairies.constants import MILK_LITER_FACTOR
from canmee_dairies.formatting import format_money
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.http import Http404, JsonResponse, QueryDict
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View
from django.views.generic import ListView, CreateView, UpdateView
from masters.forms import BuyerPaymentReceiveForm
from masters.models import Buyer
from .forms import (
    MilkDistributionForm,
    MilkDistributionBuyerResultForm,
    MilkDistributionStatusForm,
    MilkDistributionBillingRateForm,
    BranchDispatchRespondForm,
    BranchDispatchReturnResolveForm,
)
from .models import MilkDistribution, DispatchNotification
from .branch_dispatch import (
    accept_branch_return,
    collect_branch_dispatch,
    divert_branch_dispatch_to_branch,
    divert_branch_dispatch_to_buyer,
    reject_branch_return,
    request_branch_return,
)
from .inbox import (
    mark_incoming_dispatch_notifications_read,
    mark_return_requested_notifications_read,
    user_can_access_branch,
    user_can_respond_as_destination,
    user_can_resolve_return_as_source,
)

INCOMING_DISPATCH_LIST_QUERY = "filter_tab=branch&branch_scope=incoming"
OUTGOING_DISPATCH_LIST_QUERY = "filter_tab=branch&branch_scope=outgoing"


def _safe_branch_ids(branch_ids):
    return [int(b) for b in branch_ids if str(b).isdigit()]


def _buyer_returns_to_branches_q(branch_ids):
    return Q(
        buyer_id__isnull=False,
        status=MilkDistribution.DistributionStatus.RETURN,
        returned_branch_id__in=branch_ids,
    )


def _branch_filter_with_buyer_returns_q(branch_ids):
    safe_ids = _safe_branch_ids(branch_ids)
    if not safe_ids:
        return Q()
    return Q(branch_id__in=safe_ids) | _buyer_returns_to_branches_q(safe_ids)


def _all_tab_branch_filter_q(branch_ids):
    """Buyer + branch transfers involving selected branches (source or destination)."""
    safe_ids = _safe_branch_ids(branch_ids)
    if not safe_ids:
        return Q()
    return (
        Q(branch_id__in=safe_ids)
        | Q(destination_branch_id__in=safe_ids)
        | _buyer_returns_to_branches_q(safe_ids)
    )


def _branch_transfer_access_q(user_branch_ids):
    """Branch transfers the user may see (sent from or received at their branches)."""
    if user_branch_ids is None:
        return Q()
    if not user_branch_ids:
        return Q(pk__in=[])
    return Q(branch_id__in=user_branch_ids) | Q(destination_branch_id__in=user_branch_ids)


def _incoming_dispatch_list_url():
    return f"{reverse('distribution-list')}?{INCOMING_DISPATCH_LIST_QUERY}"


def _outgoing_dispatch_list_url():
    return f"{reverse('distribution-list')}?{OUTGOING_DISPATCH_LIST_QUERY}"


def _embed_request(request):
    return request.GET.get("embed") == "1" or request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _ajax_post(request):
    return request.POST.get("embed") == "1" or request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _form_errors_payload(form):
    return {field: list(errors) for field, errors in form.errors.items()}


def _exception_errors_payload(exc):
    if isinstance(exc, ValidationError):
        if hasattr(exc, "message_dict") and exc.message_dict:
            return {field: list(messages) for field, messages in exc.message_dict.items()}
        if hasattr(exc, "messages"):
            return {"__all__": list(exc.messages)}
    return {"__all__": [str(exc)]}

VALID_DISPATCH_STATUSES = {value for value, _ in MilkDistribution.DistributionStatus.choices}
VALID_DISPATCH_PAYMENT_STATUSES = {
    value for value, _ in MilkDistribution.DistributionPaymentStatus.choices
}


def dispatch_list_totals(queryset):
    total_kg = queryset.aggregate(total=Sum("kg"))["total"] or Decimal("0.00")
    total_liters = Decimal("0.00")
    for kg in queryset.values_list("kg", flat=True):
        total_liters += (kg * MILK_LITER_FACTOR).quantize(Decimal("0.01"))
    return {
        "total_kg": total_kg,
        "total_liters": total_liters.quantize(Decimal("0.01")),
        "count": queryset.count(),
    }


def dispatch_qty_totals(queryset):
    totals = dispatch_list_totals(queryset)
    delivered_kg = Decimal("0.00")
    delivered_liters = Decimal("0.00")
    variation_kg = Decimal("0.00")
    variation_liters = Decimal("0.00")
    delivered_count = 0
    for row in queryset:
        if row.delivered_quantity is None:
            continue
        delivered_count += 1
        delivered_kg += row.delivered_quantity
        delivered_liters += row.delivered_liters or Decimal("0.00")
        variation_kg += row.qty_variation_kg or Decimal("0.00")
        variation_liters += row.qty_variation_liters or Decimal("0.00")
    totals["delivered_kg"] = delivered_kg
    totals["delivered_liters"] = delivered_liters
    totals["variation_kg"] = variation_kg
    totals["variation_liters"] = variation_liters
    totals["has_delivered"] = delivered_count > 0
    return totals


class MilkDistributionListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = MilkDistribution
    paginate_by = 250
    page_size_choices = (100, 250, 500, 1000)
    per_page_all = "all"
    template_name = 'dispatch/distribution_list.html'
    permission_required = "dispatch.view_milkdistribution"

    def _dispatch_list_filtered_qs(self, *, ignore_payment=False):
        base_qs = MilkDistribution.objects.select_related(
            "branch",
            "buyer",
            "destination_branch",
            "returned_branch",
            "divert_to_branch",
            "divert_to_buyer",
            "diverted_by_branch",
        )
        filter_tab = (self.request.GET.get("filter_tab") or "buyer").strip()
        if filter_tab not in ("branch", "buyer", "all"):
            filter_tab = "buyer"
        branch_scope = (self.request.GET.get("branch_scope") or "all").strip()
        if branch_scope not in ("all", "outgoing", "incoming"):
            branch_scope = "all"

        user_branch_ids = get_user_branch_ids(self.request.user)
        selected_branch_ids = _safe_branch_ids(
            self.request.GET.getlist("destination_branches")
            or self.request.GET.getlist("branches")
        )
        if user_branch_ids is not None and selected_branch_ids:
            allowed = set(user_branch_ids)
            selected_branch_ids = [b for b in selected_branch_ids if b in allowed]

        if filter_tab == "branch":
            # Branch transfers only.
            qs = base_qs.filter(destination_branch_id__isnull=False)
            access_q = _branch_transfer_access_q(user_branch_ids)
            if access_q:
                qs = qs.filter(access_q)
            elif user_branch_ids is not None and not user_branch_ids:
                qs = qs.none()

            if branch_scope == "outgoing":
                # Sent from selected/user branches to another branch.
                if selected_branch_ids:
                    qs = qs.filter(branch_id__in=selected_branch_ids)
                elif user_branch_ids is not None:
                    qs = qs.filter(branch_id__in=user_branch_ids)
            elif branch_scope == "incoming":
                # Received at selected/user branches from another branch.
                if selected_branch_ids:
                    qs = qs.filter(destination_branch_id__in=selected_branch_ids)
                elif user_branch_ids is not None:
                    qs = qs.filter(destination_branch_id__in=user_branch_ids)
            else:
                # View all branch transfers involving selected/user branches.
                if selected_branch_ids:
                    qs = qs.filter(
                        Q(branch_id__in=selected_branch_ids)
                        | Q(destination_branch_id__in=selected_branch_ids)
                    )
        elif filter_tab == "all":
            if user_branch_ids is None:
                qs = base_qs
            elif not user_branch_ids:
                qs = base_qs.none()
            else:
                qs = base_qs.filter(
                    Q(branch_id__in=user_branch_ids)
                    | Q(destination_branch_id__in=user_branch_ids)
                    | _buyer_returns_to_branches_q(user_branch_ids)
                )
            buyer_id = self.request.GET.get("buyer")
            if buyer_id and str(buyer_id).isdigit():
                qs = qs.filter(buyer_id=int(buyer_id))
            if selected_branch_ids:
                qs = qs.filter(_all_tab_branch_filter_q(selected_branch_ids))
        else:
            # Buyer dispatches (and buyer returns for the user's branches).
            if user_branch_ids is None:
                qs = base_qs
            elif not user_branch_ids:
                qs = base_qs.none()
            else:
                qs = base_qs.filter(
                    Q(branch_id__in=user_branch_ids)
                    | _buyer_returns_to_branches_q(user_branch_ids)
                )
            qs = qs.filter(buyer_id__isnull=False)
            buyer_id = self.request.GET.get("buyer")
            if buyer_id and str(buyer_id).isdigit():
                qs = qs.filter(buyer_id=int(buyer_id))
            if selected_branch_ids:
                qs = qs.filter(_branch_filter_with_buyer_returns_q(selected_branch_ids))

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
        if status in VALID_DISPATCH_STATUSES:
            qs = qs.filter(status=status)
        if not ignore_payment:
            payment_status = (self.request.GET.get("payment_status") or "").strip()
            if payment_status in VALID_DISPATCH_PAYMENT_STATUSES:
                qs = qs.filter(payment_status=payment_status)
        return qs.order_by("-date", "-dispatch_no", "-pk")

    def get_queryset(self):
        return self._dispatch_list_filtered_qs()

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
        per = self._resolve_per_page()
        if per == self.per_page_all:
            return None
        return per

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        filter_tab = (self.request.GET.get("filter_tab") or "buyer").strip()
        if filter_tab not in ("branch", "buyer", "all"):
            filter_tab = "buyer"
        ctx["filter_tab"] = filter_tab
        ctx["show_branch_status_col"] = filter_tab != "buyer"
        branch_scope = (self.request.GET.get("branch_scope") or "all").strip()
        if branch_scope not in ("all", "outgoing", "incoming"):
            branch_scope = "all"
        ctx["branch_scope"] = branch_scope
        branch_qs = get_allowed_branches_qs(self.request.user).order_by("code", "name")
        ctx["branches"] = branch_qs
        ctx["destination_branches"] = branch_qs
        ctx["selected_branches"] = self.request.GET.getlist("branches")
        ctx["selected_destination_branches"] = self.request.GET.getlist("destination_branches")
        if filter_tab == "branch" and not ctx["selected_destination_branches"]:
            ctx["selected_destination_branches"] = ctx["selected_branches"]
        if filter_tab == "buyer" and not ctx["selected_branches"]:
            ctx["selected_branches"] = ctx["selected_destination_branches"]
        if filter_tab == "all" and not ctx["selected_branches"]:
            ctx["selected_branches"] = ctx["selected_destination_branches"]
        ctx["buyers"] = filter_m2m_by_user_branches(Buyer.objects.all(), self.request.user).order_by("name")
        ctx["selected_buyer"] = self.request.GET.get("buyer", "")
        ctx["selected_status"] = self.request.GET.get("status", "")
        ctx["selected_payment_status"] = self.request.GET.get("payment_status", "")
        ctx["date_from"] = self.request.GET.get("date_from", "")
        ctx["date_to"] = self.request.GET.get("date_to", "")
        ctx["status_choices"] = MilkDistribution.DistributionStatus.choices
        ctx["payment_status_choices"] = MilkDistribution.DistributionPaymentStatus.choices
        ctx["return_branches"] = get_allowed_branches_qs(self.request.user).order_by("name")
        q = self.request.GET.copy()
        q.pop("page", None)
        ctx["filter_query"] = q.urlencode()
        tab_q = self.request.GET.copy()
        tab_q.pop("page", None)
        tab_q.pop("filter_tab", None)
        tab_q.pop("branch_scope", None)
        ctx["dispatch_tab_query"] = tab_q.urlencode()
        ctx["pagination_query"] = ctx["filter_query"]
        per = self._resolve_per_page()
        ctx["show_all_rows"] = per == self.per_page_all
        ctx["per_page"] = "All" if per == self.per_page_all else per
        ctx["per_page_choices"] = self.page_size_choices
        if ctx["show_all_rows"]:
            obj_list = ctx.get("object_list")
            ctx["list_total_count"] = len(obj_list) if obj_list is not None else 0
        ctx["dispatch_totals"] = dispatch_list_totals(self._dispatch_list_filtered_qs())
        payment_count_qs = self._dispatch_list_filtered_qs(ignore_payment=True)
        payment_counts = {
            row["payment_status"]: row["c"]
            for row in (
                payment_count_qs.order_by()
                .values("payment_status")
                .annotate(c=Count("id"))
            )
        }
        ctx["dispatch_payment_counts"] = {
            "all": payment_count_qs.count(),
            "pending": payment_counts.get("pending", 0),
            "partial": payment_counts.get("partial", 0),
            "paid": payment_counts.get("paid", 0),
        }
        payment_chip_q = self.request.GET.copy()
        payment_chip_q.pop("page", None)
        payment_chip_q.pop("payment_status", None)
        ctx["dispatch_payment_chip_query"] = payment_chip_q.urlencode()
        respond_ids = set()
        return_ids = set()
        for obj in ctx.get("object_list") or []:
            if user_can_respond_as_destination(self.request.user, obj):
                respond_ids.add(obj.pk)
            if user_can_resolve_return_as_source(self.request.user, obj):
                return_ids.add(obj.pk)
        ctx["branch_dispatch_respond_ids"] = respond_ids
        ctx["branch_dispatch_return_ids"] = return_ids
        user_branch_ids = get_user_branch_ids(self.request.user)
        buyer_return_received_ids = set()
        if user_branch_ids:
            for obj in ctx.get("object_list") or []:
                if obj.is_buyer_return_received_for(user_branch_ids):
                    buyer_return_received_ids.add(obj.pk)
        ctx["buyer_return_received_ids"] = buyer_return_received_ids
        ctx["show_dispatch_select_col"] = (
            self.request.user.is_superuser
            or _user_can_edit_dispatch_billing_rate(self.request.user)
            or self.request.user.has_perm("dispatch.change_milkdistribution")
        )
        return ctx

    def post(self, request, *args, **kwargs):
        if not request.user.is_superuser:
            messages.error(request, "Only superusers can delete dispatches.")
            return self._redirect_with_query((request.POST.get("redirect_query") or "").strip())

        action = (request.POST.get("bulk_action") or "").strip()
        redirect_query = (request.POST.get("redirect_query") or "").strip()

        if action not in {"delete_one", "delete_selected", "delete_all"}:
            messages.error(request, "Invalid bulk action.")
            return self._redirect_with_query(redirect_query)

        base_qs = self._dispatch_list_filtered_qs()

        if action == "delete_all":
            target_qs = base_qs
        elif action == "delete_one":
            one_id = request.POST.get("one_id")
            target_qs = base_qs.filter(pk=one_id) if str(one_id or "").isdigit() else base_qs.none()
        else:
            selected_ids = [int(x) for x in request.POST.getlist("selected_ids") if str(x).isdigit()]
            target_qs = base_qs.filter(pk__in=selected_ids) if selected_ids else base_qs.none()

        deleted_count = target_qs.count()
        if deleted_count == 0:
            messages.warning(request, "No matching dispatches selected.")
            return self._redirect_with_query(redirect_query)

        target_qs.delete()
        messages.success(request, f"Deleted {deleted_count} dispatch record(s).")
        return self._redirect_with_query(redirect_query)

    @staticmethod
    def _redirect_with_query(redirect_query):
        if redirect_query:
            return redirect(f"{reverse('distribution-list')}?{redirect_query}")
        return redirect("distribution-list")


def _distribution_form_extra_context(request):
    ctx = {}
    if request.user.has_perm("masters.add_buyer"):
        from masters.forms import BuyerForm

        ctx["buyer_quick_add_form"] = BuyerForm(user=request.user)
        ctx["buyer_quick_add_form"].fields["branches"].widget.attrs["id"] = "dispatch-buyer-add-branches"
    buyer_qs = filter_m2m_by_user_branches(
        Buyer.objects.prefetch_related("branches"),
        request.user,
    )
    ctx["buyer_branch_options"] = master_branch_option_rows(buyer_qs)
    return ctx


class MilkDistributionCreateView(LoginRequiredMixin, PermissionRequiredMixin, ModelFormPageTitleMixin, CreateView):
    permission_required = 'dispatch.add_milkdistribution'
    model = MilkDistribution
    form_class = MilkDistributionForm
    template_name = 'dispatch/distribution_form.html'
    success_url = reverse_lazy('distribution-list')

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_distribution_form_extra_context(self.request))
        return ctx


def _distribution_is_paid_locked(obj):
    if not obj or not getattr(obj, "pk", None):
        return False
    return obj.payment_status_from_amounts() == MilkDistribution.DistributionPaymentStatus.PAID


class MilkDistributionUpdateView(LoginRequiredMixin, PermissionRequiredMixin, ModelFormPageTitleMixin, UpdateView):
    permission_required = "dispatch.change_milkdistribution"
    model = MilkDistribution
    form_class = MilkDistributionForm
    template_name = "dispatch/distribution_form.html"
    success_url = reverse_lazy("distribution-list")

    def get_queryset(self):
        return filter_by_user_branches(
            MilkDistribution.objects.select_related("buyer", "branch", "destination_branch"),
            self.request.user,
            "branch_id",
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        if self.object and _distribution_is_paid_locked(self.object):
            for field in form.fields.values():
                field.disabled = True
        return form

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(_distribution_form_extra_context(self.request))
        ctx["view_only"] = _distribution_is_paid_locked(self.object)
        return ctx

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if request.GET.get("modal") == "1":
            return render(
                request,
                "dispatch/_distribution_edit_modal_form.html",
                self.get_context_data(),
            )
        return super().get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        if _distribution_is_paid_locked(self.object):
            msg = "Paid dispatches cannot be edited."
            if _distribution_is_ajax(request):
                return JsonResponse({"ok": False, "errors": {"__all__": [msg]}}, status=403)
            messages.error(request, msg)
            return _distribution_redirect_next(request, fallback=self.success_url)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        if _distribution_is_paid_locked(form.instance):
            msg = "Paid dispatches cannot be edited."
            if _distribution_is_ajax(self.request):
                return JsonResponse({"ok": False, "errors": {"__all__": [msg]}}, status=403)
            messages.error(self.request, msg)
            return _distribution_redirect_next(self.request, fallback=self.success_url)
        self.object = form.save()
        msg = f"Dispatch {self.object.dispatch_no} updated."
        if _distribution_is_ajax(self.request):
            return JsonResponse({"ok": True, "message": msg})
        messages.success(self.request, msg)
        return _distribution_redirect_next(self.request, fallback=self.success_url)

    def form_invalid(self, form):
        if _distribution_is_ajax(self.request):
            return JsonResponse(
                {"ok": False, "errors": _distribution_form_errors_json(form)},
                status=400,
            )
        if self.request.GET.get("modal") == "1" or self.request.POST.get("modal") == "1":
            return render(
                self.request,
                "dispatch/_distribution_edit_modal_form.html",
                self.get_context_data(form=form),
                status=400,
            )
        return super().form_invalid(form)


class MilkDistributionBuyerResultUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    permission_required = 'dispatch.change_milkdistribution'
    model = MilkDistribution
    form_class = MilkDistributionBuyerResultForm
    template_name = 'dispatch/buyer_result_form.html'
    success_url = reverse_lazy('distribution-list')

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        if obj.is_branch_dispatch():
            raise Http404("Buyer result is only available for buyer dispatches.")
        return obj

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        kwargs["notify_actor"] = self.request.user
        return kwargs

    def form_valid(self, form):
        instance = form.save()
        if _distribution_is_ajax(self.request):
            return JsonResponse(_distribution_status_json(instance))
        return _distribution_redirect_next(self.request, fallback=self.success_url)

    def form_invalid(self, form):
        if _distribution_is_ajax(self.request):
            return JsonResponse({"ok": False, "errors": _distribution_form_errors_json(form)}, status=400)
        return super().form_invalid(form)


def _apply_dispatch_billing_rate(obj, data):
    if (obj.paid_amount or 0) > 0:
        return "paid"
    if obj.is_branch_dispatch() or not obj.buyer_id:
        return "not_buyer"
    if data.get("use_period_rate"):
        obj.is_rate_manual = False
        obj.unit_rate = Decimal("0.00")
        obj.rate_unit = ""
    else:
        obj.is_rate_manual = True
        obj.unit_rate = data["unit_rate"]
        obj.rate_unit = data["rate_unit"]
    obj.save()
    return "ok"


def _user_can_edit_dispatch_billing_rate(user):
    return bool(
        user
        and user.is_authenticated
        and (
            user.is_superuser
            or user.is_staff
            or user.has_perm("dispatch.change_dispatch_billing_rate")
        )
    )


def _distribution_redirect_next(request, *, fallback):
    next_url = (request.POST.get("next") or request.GET.get("next") or "").strip()
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect(fallback)


def _distribution_is_ajax(request):
    return request.headers.get("X-Requested-With") == "XMLHttpRequest"


def _distribution_form_errors_json(form):
    errors = {}
    for field, field_errors in form.errors.items():
        errors[field] = [str(error) for error in field_errors]
    for error in form.non_field_errors():
        errors.setdefault("__all__", []).append(str(error))
    return errors


def _distribution_status_json(instance):
    branch = instance.returned_branch
    payload = {
        "ok": True,
        "status": instance.status,
        "status_display": instance.get_status_display(),
        "returned_branch": str(branch) if branch else "",
        "returned_branch_id": branch.pk if branch else None,
    }
    if instance.buyer_result_quantity is not None:
        liters = str(
            (instance.buyer_result_quantity * MILK_LITER_FACTOR).quantize(Decimal("0.01"))
        )
        payload["buyer_result_liters"] = liters
        payload["buyer_result_liters"] = liters
    if instance.delivered_quantity is not None:
        payload["delivered_kg"] = str(instance.delivered_quantity.quantize(Decimal("0.01")))
        payload["delivered_liters"] = str(
            (instance.delivered_quantity * MILK_LITER_FACTOR).quantize(Decimal("0.01"))
        )
    if instance.delivered_ts is not None:
        payload["delivered_ts"] = str(instance.delivered_ts.quantize(Decimal("0.01")))
    return payload


class MilkDistributionStatusUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    permission_required = "dispatch.change_milkdistribution"
    model = MilkDistribution
    form_class = MilkDistributionStatusForm
    http_method_names = ["post"]

    def form_valid(self, form):
        instance = form.save()
        if _distribution_is_ajax(self.request):
            return JsonResponse(_distribution_status_json(instance))
        return _distribution_redirect_next(self.request, fallback=reverse_lazy("distribution-list"))

    def form_invalid(self, form):
        if _distribution_is_ajax(self.request):
            return JsonResponse({"ok": False, "errors": _distribution_form_errors_json(form)}, status=400)
        for error in form.non_field_errors():
            messages.error(self.request, error)
        for errors in form.errors.values():
            for error in errors:
                messages.error(self.request, error)
        return _distribution_redirect_next(self.request, fallback=reverse_lazy("distribution-list"))


class MilkDistributionBillingRateUpdateView(LoginRequiredMixin, View):
    http_method_names = ["get", "post"]
    success_url = reverse_lazy("distribution-list")

    def has_permission(self):
        return _user_can_edit_dispatch_billing_rate(self.request.user)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if not self.has_permission():
            if _distribution_is_ajax(request):
                return JsonResponse({"ok": False, "errors": {"__all__": ["Permission denied."]}}, status=403)
            messages.error(request, "You do not have permission to change dispatch billing rate.")
            return _distribution_redirect_next(request, fallback=self.success_url)
        return super().dispatch(request, *args, **kwargs)

    def get_object(self):
        qs = filter_by_user_branches(
            MilkDistribution.objects.select_related("buyer"),
            self.request.user,
            "branch_id",
        )
        obj = get_object_or_404(qs, pk=self.kwargs["pk"])
        if obj.is_branch_dispatch() or not obj.buyer_id:
            raise Http404("Billing rate is only available for buyer dispatches.")
        return obj

    def get(self, request, pk):
        return _distribution_redirect_next(request, fallback=self.success_url)

    def post(self, request, pk):
        obj = self.get_object()
        ajax = _distribution_is_ajax(request)
        if (obj.paid_amount or 0) > 0:
            errors = {"__all__": ["Rate cannot be changed after a payment has been recorded."]}
            if ajax:
                return JsonResponse({"ok": False, "errors": errors}, status=400)
            messages.error(request, errors["__all__"][0])
            return _distribution_redirect_next(request, fallback=self.success_url)

        form = MilkDistributionBillingRateForm(request.POST)
        if not form.is_valid():
            if ajax:
                return JsonResponse({"ok": False, "errors": _distribution_form_errors_json(form)}, status=400)
            for error in form.non_field_errors():
                messages.error(request, error)
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
            return _distribution_redirect_next(request, fallback=self.success_url)

        data = form.cleaned_data
        result = _apply_dispatch_billing_rate(obj, data)
        if result == "paid":
            errors = {"__all__": ["Rate cannot be changed after a payment has been recorded."]}
            if ajax:
                return JsonResponse({"ok": False, "errors": errors}, status=400)
            messages.error(request, errors["__all__"][0])
            return _distribution_redirect_next(request, fallback=self.success_url)
        if result == "not_buyer":
            errors = {"__all__": ["Billing rate is only available for buyer dispatches."]}
            if ajax:
                return JsonResponse({"ok": False, "errors": errors}, status=400)
            messages.error(request, errors["__all__"][0])
            return _distribution_redirect_next(request, fallback=self.success_url)
        obj.refresh_from_db()
        msg = (
            f"Rate for dispatch {obj.dispatch_no} updated to {format_money(obj.unit_rate)}"
            f" per {obj.get_rate_unit_display() or obj.rate_unit}."
        )
        if ajax:
            return JsonResponse(
                {
                    "ok": True,
                    "unit_rate": str(obj.unit_rate or Decimal("0.00")),
                    "rate_display": format_money(obj.unit_rate),
                    "rate_unit": obj.rate_unit,
                    "rate_unit_label": obj.get_rate_unit_display() or obj.rate_unit,
                    "is_rate_manual": obj.is_rate_manual,
                    "total_amount": str(obj.total_amount or Decimal("0.00")),
                    "message": msg,
                }
            )
        messages.success(request, msg)
        return _distribution_redirect_next(request, fallback=self.success_url)


class MilkDistributionBulkBillingRateUpdateView(LoginRequiredMixin, View):
    http_method_names = ["post"]
    success_url = reverse_lazy("distribution-list")

    def has_permission(self):
        return _user_can_edit_dispatch_billing_rate(self.request.user)

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if not self.has_permission():
            if _distribution_is_ajax(request):
                return JsonResponse({"ok": False, "errors": {"__all__": ["Permission denied."]}}, status=403)
            messages.error(request, "You do not have permission to change dispatch billing rate.")
            return _distribution_redirect_next(request, fallback=self.success_url)
        return super().dispatch(request, *args, **kwargs)

    def post(self, request):
        ajax = _distribution_is_ajax(request)
        selected_ids = [int(x) for x in request.POST.getlist("selected_ids") if str(x).isdigit()]
        if not selected_ids:
            errors = {"__all__": ["Select at least one dispatch."]}
            if ajax:
                return JsonResponse({"ok": False, "errors": errors}, status=400)
            messages.warning(request, errors["__all__"][0])
            return _distribution_redirect_next(request, fallback=self.success_url)

        form = MilkDistributionBillingRateForm(request.POST)
        if not form.is_valid():
            if ajax:
                return JsonResponse({"ok": False, "errors": _distribution_form_errors_json(form)}, status=400)
            for error in form.non_field_errors():
                messages.error(request, error)
            messages.warning(request, "Could not apply rate to the selected dispatches.")
            return _distribution_redirect_next(request, fallback=self.success_url)

        qs = filter_by_user_branches(
            MilkDistribution.objects.select_related("buyer"),
            request.user,
            "branch_id",
        ).filter(pk__in=selected_ids)

        updated = 0
        skipped_paid = 0
        skipped_other = 0
        data = form.cleaned_data
        for obj in qs:
            result = _apply_dispatch_billing_rate(obj, data)
            if result == "ok":
                updated += 1
            elif result == "paid":
                skipped_paid += 1
            else:
                skipped_other += 1

        missing = len(selected_ids) - qs.count()
        skipped_other += missing
        if updated == 0:
            errors = {
                "__all__": [
                    "No selected dispatches could be updated. Paid or branch transfers were skipped."
                ]
            }
            if ajax:
                return JsonResponse(
                    {
                        "ok": False,
                        "errors": errors,
                        "updated": 0,
                        "skipped_paid": skipped_paid,
                        "skipped_other": skipped_other,
                    },
                    status=400,
                )
            messages.warning(request, errors["__all__"][0])
            return _distribution_redirect_next(request, fallback=self.success_url)

        msg = f"Rate applied to {updated} dispatch(es)."
        extra = []
        if skipped_paid:
            extra.append(f"{skipped_paid} already paid")
        if skipped_other:
            extra.append(f"{skipped_other} skipped")
        if extra:
            msg = f"{msg} ({', '.join(extra)})."
        if ajax:
            return JsonResponse(
                {
                    "ok": True,
                    "updated": updated,
                    "skipped_paid": skipped_paid,
                    "skipped_other": skipped_other,
                    "message": msg,
                }
            )
        messages.success(request, msg)
        return _distribution_redirect_next(request, fallback=self.success_url)


class MilkDistributionPaymentUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "dispatch.change_milkdistribution"
    template_name = "dispatch/payment_form.html"
    success_url = reverse_lazy("distribution-list")

    def get_object(self):
        qs = filter_by_user_branches(
            MilkDistribution.objects.select_related("buyer"),
            self.request.user,
            "branch_id",
        )
        obj = get_object_or_404(qs, pk=self.kwargs["pk"])
        if obj.is_branch_dispatch():
            raise Http404("Payment is only available for buyer dispatches.")
        return obj

    def get(self, request, pk):
        obj = self.get_object()
        next_url = (request.GET.get("next") or "").strip()
        return render(
            request,
            self.template_name,
            {
                "distribution": obj,
                "object": obj,
                "default_date": timezone.localdate().isoformat(),
                "next": next_url if next_url.startswith("/") else "",
                "form_errors": [],
            },
        )

    def post(self, request, pk):
        from dispatch.buyer_billing import record_buyer_payment

        obj = self.get_object()
        form = BuyerPaymentReceiveForm(request.POST)
        ajax = _distribution_is_ajax(request)

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
                    "distribution": obj,
                    "object": obj,
                    "default_date": request.POST.get("date") or timezone.localdate().isoformat(),
                    "next": next_url if next_url.startswith("/") else "",
                    "form_errors": flat,
                },
                status=400,
            )

        if not form.is_valid():
            return error_payload(_distribution_form_errors_json(form))

        try:
            payment = record_buyer_payment(
                buyer=obj.buyer,
                amount=form.cleaned_data["amount"],
                date=form.cleaned_data["date"],
                reference=form.cleaned_data.get("reference") or "",
                note=form.cleaned_data.get("note") or "",
                created_by=request.user,
                distribution_ids=[obj.pk],
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
            f"Payment of {format_money(payment.amount)} recorded for dispatch {obj.dispatch_no}."
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
        return _distribution_redirect_next(request, fallback=self.success_url)


class MilkDistributionBulkPaymentUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "dispatch.change_milkdistribution"
    http_method_names = ["post"]
    success_url = reverse_lazy("distribution-list")

    def post(self, request):
        from dispatch.buyer_billing import record_buyer_payment

        ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
        form = BuyerPaymentReceiveForm(request.POST)
        raw_ids = request.POST.getlist("selected_ids") or request.POST.getlist("distribution_ids")
        selected_ids = []
        for raw in raw_ids:
            try:
                selected_ids.append(int(raw))
            except (TypeError, ValueError):
                continue
        selected_ids = list(dict.fromkeys(selected_ids))

        def error_payload(errors, status=400):
            if ajax:
                return JsonResponse({"ok": False, "errors": errors}, status=status)
            flat = []
            for msgs in errors.values():
                flat.extend(msgs)
            messages.error(request, " ".join(flat) if flat else "Could not record bulk payment.")
            return _distribution_redirect_next(request, fallback=self.success_url)

        if not selected_ids:
            return error_payload({"__all__": ["Select at least one unpaid buyer dispatch."]})

        if not form.is_valid():
            return error_payload(_distribution_form_errors_json(form))

        qs = filter_by_user_branches(
            MilkDistribution.objects.select_related("buyer"),
            request.user,
            "branch_id",
        ).filter(pk__in=selected_ids)

        dists = list(qs)
        if len(dists) != len(selected_ids):
            return error_payload({"__all__": ["One or more selected dispatches were not found."]})

        payable = []
        for dist in dists:
            if dist.is_branch_dispatch() or not dist.buyer_id:
                continue
            if dist.outstanding_amount <= 0:
                continue
            payable.append(dist)

        if not payable:
            return error_payload({"__all__": ["Select unpaid buyer dispatches to record a bulk payment."]})

        buyer_ids = {dist.buyer_id for dist in payable}
        if len(buyer_ids) > 1:
            return error_payload({"__all__": ["Bulk payment can only be recorded for one buyer at a time."]})

        payable_ids = [dist.pk for dist in payable]
        buyer = payable[0].buyer
        try:
            payment = record_buyer_payment(
                buyer=buyer,
                amount=form.cleaned_data["amount"],
                date=form.cleaned_data["date"],
                reference=form.cleaned_data.get("reference") or "",
                note=form.cleaned_data.get("note") or "",
                created_by=request.user,
                distribution_ids=payable_ids,
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

        alloc_count = payment.allocations.count()
        msg = (
            f"Payment of {format_money(payment.amount)} recorded "
            f"across {alloc_count} dispatch(es) for {buyer}."
        )
        if ajax:
            return JsonResponse({"ok": True, "message": msg, "paid_count": alloc_count})
        messages.success(request, msg)
        return _distribution_redirect_next(request, fallback=self.success_url)


class MilkDistributionDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "dispatch.delete_milkdistribution"

    def post(self, request, pk):
        from dispatch.buyer_billing import reverse_dispatch_billing_on_delete

        qs = filter_by_user_branches(
            MilkDistribution.objects.select_related("buyer"),
            request.user,
            "branch_id",
        )
        obj = get_object_or_404(qs, pk=pk)
        dispatch_no = obj.dispatch_no
        reverse_dispatch_billing_on_delete(obj)
        obj.delete()
        messages.success(request, f"Dispatch {dispatch_no} deleted.")
        return _distribution_redirect_next(request, fallback=reverse_lazy("distribution-list"))


class MilkDistributionInlineQuantityView(LoginRequiredMixin, View):
    """Superuser-only inline kg/liters edit from the dispatch list."""

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
            MilkDistribution.objects.select_related("branch", "buyer", "destination_branch"),
            request.user,
            "branch_id",
        )
        try:
            with transaction.atomic():
                dist = qs.select_for_update().get(pk=pk)
                dist.kg = new_kg
                dist.save()
        except MilkDistribution.DoesNotExist:
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
                "kg": str(dist.kg),
                "liters": str(dist.liters_equivalent),
            }
        )


class MilkDistributionBranchRespondView(LoginRequiredMixin, AnyPermissionRequiredMixin, View):
    permission_required = (
        "dispatch.change_milkdistribution",
        "dispatch.respond_branch_dispatch",
    )
    template_name = "dispatch/branch_dispatch_respond.html"
    embed_template_name = "dispatch/_branch_dispatch_respond_modal_content.html"
    unavailable_template_name = "dispatch/_branch_dispatch_respond_unavailable.html"

    def _load_dispatch(self, pk):
        return get_object_or_404(
            MilkDistribution.objects.select_related(
                "branch", "destination_branch", "buyer"
            ),
            pk=pk,
        )

    def get_dispatch(self, pk, user):
        dispatch = self._load_dispatch(pk)
        if not user_can_respond_as_destination(user, dispatch):
            raise Http404
        return dispatch

    def _respond_context(self, request, dispatch, form):
        redirect_query = (request.GET.get("redirect_query") or INCOMING_DISPATCH_LIST_QUERY).strip()
        return {
            "dispatch": dispatch,
            "form": form,
            "form_action": request.path,
            "redirect_query": redirect_query,
        }

    def get(self, request, pk):
        dispatch = self._load_dispatch(pk)
        if not user_can_access_branch(request.user, dispatch.destination_branch_id):
            raise Http404
        if not dispatch.needs_destination_response():
            if _embed_request(request):
                mark_incoming_dispatch_notifications_read(dispatch)
                return render(
                    request,
                    self.unavailable_template_name,
                    {
                        "dispatch": dispatch,
                        "list_url": _incoming_dispatch_list_url(),
                    },
                )
            raise Http404
        if not user_can_respond_as_destination(request.user, dispatch):
            raise Http404
        form = BranchDispatchRespondForm(dispatch=dispatch, user=request.user)
        context = self._respond_context(request, dispatch, form)
        if _embed_request(request):
            return render(request, self.embed_template_name, context)
        return render(request, self.template_name, context)

    def post(self, request, pk):
        dispatch = self.get_dispatch(pk, request.user)
        form = BranchDispatchRespondForm(request.POST, dispatch=dispatch, user=request.user)
        ajax = _ajax_post(request)
        if not form.is_valid():
            if ajax:
                return JsonResponse(
                    {"ok": False, "errors": _form_errors_payload(form)},
                    status=400,
                )
            return render(
                request,
                self.template_name,
                self._respond_context(request, dispatch, form),
                status=400,
            )
        action = form.cleaned_data["response_action"]
        notes = form.cleaned_data.get("notes") or ""
        try:
            if action == BranchDispatchRespondForm.ACTION_COLLECT:
                collect_branch_dispatch(
                    dispatch,
                    user=request.user,
                    notes=notes,
                    quantity_kg=form.cleaned_data.get("return_quantity_kg"),
                )
                success_message = "Dispatch collected. Source branch has been notified."
            elif action == BranchDispatchRespondForm.ACTION_RETURN:
                request_branch_return(
                    dispatch,
                    user=request.user,
                    notes=notes,
                    quantity_kg=form.cleaned_data["return_quantity_kg"],
                )
                success_message = "Return requested. Source branch has been notified."
            elif action == BranchDispatchRespondForm.ACTION_DIVERT_BRANCH:
                divert_branch_dispatch_to_branch(
                    dispatch,
                    user=request.user,
                    target_branch=form.cleaned_data["divert_branch"],
                    notes=notes,
                    quantity_kg=form.cleaned_data.get("return_quantity_kg"),
                )
                success_message = "Dispatch diverted to the selected branch."
            elif action == BranchDispatchRespondForm.ACTION_DIVERT_BUYER:
                divert_branch_dispatch_to_buyer(
                    dispatch,
                    user=request.user,
                    target_buyer=form.cleaned_data["divert_buyer"],
                    notes=notes,
                    quantity_kg=form.cleaned_data.get("return_quantity_kg"),
                )
                success_message = "Dispatch diverted to the selected buyer."
            else:
                raise ValueError("Invalid action.")
        except (ValidationError, ValueError) as exc:
            if ajax:
                return JsonResponse(
                    {"ok": False, "errors": _exception_errors_payload(exc)},
                    status=400,
                )
            messages.error(request, str(exc))
            return render(
                request,
                self.template_name,
                self._respond_context(request, dispatch, form),
                status=400,
            )

        if ajax:
            redirect_query = (request.POST.get("redirect_query") or INCOMING_DISPATCH_LIST_QUERY).strip()
            redirect_url = (
                f"{reverse('distribution-list')}?{redirect_query}"
                if redirect_query
                else _incoming_dispatch_list_url()
            )
            return JsonResponse({"ok": True, "redirect": redirect_url, "message": success_message})

        messages.success(request, success_message)
        redirect_query = (request.POST.get("redirect_query") or "").strip()
        if redirect_query:
            return redirect(f"{reverse('distribution-list')}?{redirect_query}")
        return redirect("distribution-list")


class MilkDistributionBranchReturnResolveView(LoginRequiredMixin, AnyPermissionRequiredMixin, View):
    permission_required = (
        "dispatch.change_milkdistribution",
        "dispatch.respond_branch_dispatch",
    )
    template_name = "dispatch/branch_dispatch_return_resolve.html"
    embed_template_name = "dispatch/_branch_dispatch_return_modal_content.html"
    unavailable_template_name = "dispatch/_branch_dispatch_return_unavailable.html"

    def _load_dispatch(self, pk):
        return get_object_or_404(
            MilkDistribution.objects.select_related("branch", "destination_branch"),
            pk=pk,
        )

    def get_dispatch(self, pk, user):
        dispatch = self._load_dispatch(pk)
        if not user_can_resolve_return_as_source(user, dispatch):
            raise Http404
        return dispatch

    def _return_context(self, request, dispatch, form):
        redirect_query = (request.GET.get("redirect_query") or OUTGOING_DISPATCH_LIST_QUERY).strip()
        return {
            "dispatch": dispatch,
            "form": form,
            "form_action": request.path,
            "redirect_query": redirect_query,
        }

    def get(self, request, pk):
        dispatch = self._load_dispatch(pk)
        if not user_can_access_branch(request.user, dispatch.branch_id):
            raise Http404
        if not dispatch.needs_source_return_acceptance():
            if _embed_request(request):
                mark_return_requested_notifications_read(dispatch)
                return render(
                    request,
                    self.unavailable_template_name,
                    {
                        "dispatch": dispatch,
                        "list_url": _outgoing_dispatch_list_url(),
                    },
                )
            raise Http404
        if not user_can_resolve_return_as_source(request.user, dispatch):
            raise Http404
        form = BranchDispatchReturnResolveForm()
        context = self._return_context(request, dispatch, form)
        if _embed_request(request):
            return render(request, self.embed_template_name, context)
        return render(request, self.template_name, context)

    def post(self, request, pk):
        dispatch = self.get_dispatch(pk, request.user)
        form = BranchDispatchReturnResolveForm(request.POST)
        ajax = _ajax_post(request)
        if not form.is_valid():
            if ajax:
                return JsonResponse(
                    {"ok": False, "errors": _form_errors_payload(form)},
                    status=400,
                )
            return render(
                request,
                self.template_name,
                self._return_context(request, dispatch, form),
                status=400,
            )
        notes = form.cleaned_data.get("notes") or ""
        try:
            if form.cleaned_data["decision"] == "accept":
                accept_branch_return(dispatch, user=request.user, notes=notes)
                success_message = "Return accepted. Stock has been added to the source branch."
            else:
                reject_branch_return(dispatch, user=request.user, notes=notes)
                success_message = "Return rejected. Destination branch has been notified."
        except (ValidationError, ValueError) as exc:
            if ajax:
                return JsonResponse(
                    {"ok": False, "errors": _exception_errors_payload(exc)},
                    status=400,
                )
            messages.error(request, str(exc))
            return render(
                request,
                self.template_name,
                self._return_context(request, dispatch, form),
                status=400,
            )

        if ajax:
            redirect_query = (request.POST.get("redirect_query") or OUTGOING_DISPATCH_LIST_QUERY).strip()
            redirect_url = (
                f"{reverse('distribution-list')}?{redirect_query}"
                if redirect_query
                else _outgoing_dispatch_list_url()
            )
            return JsonResponse({"ok": True, "redirect": redirect_url, "message": success_message})

        messages.success(request, success_message)
        redirect_query = (request.POST.get("redirect_query") or "").strip()
        if redirect_query:
            return redirect(f"{reverse('distribution-list')}?{redirect_query}")
        return redirect("distribution-list")


class DispatchNotificationFeedView(LoginRequiredMixin, View):
    def get(self, request):
        from dispatch.inbox import dispatch_notifications_feed

        return JsonResponse(dispatch_notifications_feed(request.user))


class DispatchNotificationMarkReadView(LoginRequiredMixin, View):
    def post(self, request, pk):
        from dispatch.inbox import actionable_dispatch_notifications_qs, mark_notification_read

        notification = DispatchNotification.objects.filter(
            pk=pk,
            recipient=request.user,
            is_read=False,
        ).first()
        updated = mark_notification_read(notification) if notification else 0
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            unread = actionable_dispatch_notifications_qs(request.user).filter(
                is_read=False
            ).count()
            return JsonResponse({"ok": bool(updated), "unread": unread})
        return redirect(request.POST.get("next") or "distribution-list")


class DispatchNotificationMarkAllReadView(LoginRequiredMixin, View):
    def post(self, request):
        from dispatch.inbox import mark_all_actionable_notifications_read

        mark_all_actionable_notifications_read(request.user)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": True, "unread": 0})
        return redirect(request.POST.get("next") or "distribution-list")


class BuyerRateForDateView(LoginRequiredMixin, View):
    """JSON: period rate for a buyer on a dispatch date."""

    def get(self, request):
        if not (
            request.user.has_perm("dispatch.add_milkdistribution")
            or request.user.has_perm("dispatch.change_milkdistribution")
            or _user_can_edit_dispatch_billing_rate(request.user)
        ):
            return JsonResponse({"ok": False, "error": "Permission denied."}, status=403)
        from masters.buyer_rates import buyer_rate_at_date

        buyer_id = (request.GET.get("buyer") or "").strip()
        on_date = parse_date((request.GET.get("date") or "").strip()) or timezone.localdate()
        if not buyer_id.isdigit():
            return JsonResponse({"ok": False, "error": "Select a buyer."}, status=400)
        buyer = (
            filter_m2m_by_user_branches(Buyer.objects.all(), request.user)
            .filter(pk=int(buyer_id))
            .first()
        )
        if not buyer:
            return JsonResponse({"ok": False, "error": "Buyer not found."}, status=404)
        info = buyer_rate_at_date(buyer, on_date)
        unit = info.get("rate_unit") or Buyer.RateUnit.LITER
        return JsonResponse(
            {
                "ok": True,
                "rate": str(info.get("rate") or "0.00"),
                "rate_unit": unit,
                "rate_unit_label": dict(Buyer.RateUnit.choices).get(unit, unit),
            }
        )
