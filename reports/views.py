import csv
import json
import re
from collections import defaultdict
from datetime import datetime, time, timedelta
from io import BytesIO
from decimal import Decimal, InvalidOperation
from canmee_dairies.excel_io import cell_missing, get_pandas
from django.contrib import messages
from django.contrib.auth.mixins import PermissionRequiredMixin
from django.contrib.auth.decorators import permission_required
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.db.models import Avg, Sum
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView, ListView, UpdateView, DeleteView
from canmee_dairies.formatting import format_money
from canmee_dairies.mixins import RedirectGetDeleteMixin
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from milk_collections.point_reconciliation import (
    build_branch_point_reconcile_report,
    point_reconcile_report_export_matrix,
)
from .farmer_goods_summary import (
    build_branch_farmer_goods_summary_report,
    farmer_goods_summary_export_matrix,
    filter_farmer_goods_branch_sections,
)
from .point_goods_summary import (
    build_branch_point_goods_summary_report,
    filter_point_goods_branch_sections,
    point_goods_summary_export_matrix,
)
from milk_collections.models import CollectionSource, MilkCollection, MilkFactor
from dispatch.models import MilkDistribution
from masters.models import Route, CollectionPoint, CollectionPointBankAccount, Buyer, Farmer, FarmerRateHistory, FarmerAdvancePayment, CollectionPointAdvancePayment
from branches.models import Branch, BranchMilkStock
from branches.services import branch_stock_snapshot, sum_stock_snapshots
from branches.utils import filter_by_user_branches, filter_m2m_by_user_branches, get_allowed_branches_qs, get_default_branch_id
from canmee_dairies.constants import MILK_LITER_FACTOR
from suppliers.models import RawMilkSupplierCollection, FarmerGoodsIssue
from suppliers.services import summarize_priced_farmer_goods_issues
from farmer_loans.models import FarmerLoan, FarmerLoanRepaymentSchedule
from collection_point_loans.models import CollectionPointLoanRepaymentSchedule
from stock_management.models import Product
from .forms import FarmerAdvancePaymentForm, CollectionPointAdvancePaymentForm, PeriodPaymentRecordForm
from .models import (
    CollectionPointPeriodPayment,
    CollectionPointPaymentCorrection,
    CollectionPointPaymentLoanDeductionSetting,
    CollectionPointPaymentPeriodDeductionSkip,
    CollectionPointPaymentSheetRecord,
    FarmerPaymentLoanDeductionSetting,
    FarmerPaymentPeriodDeductionSkip,
    FarmerPaymentSheetRecord,
    FarmerPeriodPayment,
)
from .deduction_skips import (
    collection_point_advance_remaining,
    collection_point_loan_remaining,
    collection_point_payment_deduction_picker_data,
    collection_point_payment_period_deduction_skip_map,
    cp_loan_deduction_amount_map,
    farmer_payment_deduction_picker_data,
    goods_issue_is_skipped,
    payment_period_deduction_skip_map,
    set_collection_point_advance_deduct,
    set_collection_point_deduction_skip,
    set_collection_point_loan_deduct,
    set_deduction_skip,
)
from .payment_recording import (
    collection_point_period_payment_map,
    record_collection_point_period_payment,
    record_collection_point_bulk_period_payments,
    record_farmer_bulk_period_payments,
    record_farmer_period_payment,
)
from .manual_settlement import (
    set_advance_paid_on,
    set_advance_recovered,
    settlement_datetime_from_date,
    settlement_from_request,
)
from .services import (
    advance_is_bank_loan,
    applied_loan_deduction,
    build_collection_point_payment_sheet_data,
    group_collection_point_payment_sheet_by_route,
    build_collection_point_payslip_context,
    build_farmer_payment_summaries,
    build_farmer_payment_sheet_data,
    group_farmer_payment_sheet_by_route,
    collection_point_balance_for_period,
    collection_point_paid_collector_fee_map,
    collection_point_payment_action_flags,
    collection_point_has_zero_cash_settlement_items,
    collection_point_payment_sheet_export_matrix,
    collection_point_payment_sheet_row_snapshot,
    collection_point_pending_payable_amount,
    set_collection_point_payment_correction,
    cp_loan_deduction_settings_map,
    farmer_balance_for_period,
    farmer_overlapping_paid_data,
    farmer_paid_collection_gross_map,
    farmer_payment_sheet_row_snapshot,
    farmer_sheet_paid_and_balance,
    get_buyer_summary,
    get_collection_summary,
    loan_deduction_items_for_farmer,
    loan_deduction_settings_map,
    payment_sheet_branch_key,
    payment_sheet_export_matrix,
    pending_loan_deduction_map,
)
from .payslip_labels import (
    PAYSLIP_LABELS_EN,
    PAYSLIP_LABELS_SI,
    POINT_PAYSLIP_LABELS_EN,
    POINT_PAYSLIP_LABELS_SI,
    farmer_payslip_display_name,
    payment_sheet_period_english,
    payment_sheet_period_sinhala,
)




def _farmer_loan_print_summary(farmer):
    approved_loans = FarmerLoan.objects.filter(farmer=farmer, status=FarmerLoan.Status.APPROVED)
    loan_amount = approved_loans.aggregate(total=Sum("loan_amount"))["total"] or Decimal("0")
    schedules = FarmerLoanRepaymentSchedule.objects.filter(
        loan__farmer=farmer,
        loan__status=FarmerLoan.Status.APPROVED,
    )
    paid = schedules.aggregate(total=Sum("paid_amount"))["total"] or Decimal("0")
    loan_amount = loan_amount.quantize(Decimal("0.01"))
    paid = paid.quantize(Decimal("0.01"))
    balance = (loan_amount - paid).quantize(Decimal("0.01"))
    return loan_amount, paid, balance


def _payment_sheet_farmer_ids(start, end, branch_ids):
    return list(
        MilkCollection.objects.filter(
            source=CollectionSource.FARMER,
            date__gte=start,
            date__lte=end,
            branch_id__in=branch_ids,
        )
        .values_list("farmer_id", flat=True)
        .distinct()
    )


def _payment_sheet_point_ids(start, end, branch_ids):
    return list(
        MilkCollection.objects.filter(
            source=CollectionSource.POINT,
            date__gte=start,
            date__lte=end,
            branch_id__in=branch_ids,
        )
        .values_list("collection_point_id", flat=True)
        .distinct()
    )


def _build_farmer_payslip_context(farmer, start, end):
    from .services import farmer_payment_gross, payment_qty_for_branch

    cols = _date_range_inclusive(start, end)
    kg_by_day = defaultdict(Decimal)
    liters_by_day = defaultdict(Decimal)
    for r in MilkCollection.objects.filter(
        source=CollectionSource.FARMER, farmer=farmer, date__gte=start, date__lte=end
    ).only("date", "kg", "liters"):
        kg_by_day[r.date] += r.kg or Decimal("0")
        liters_by_day[r.date] += r.liters or Decimal("0")
    total_kg = sum((kg_by_day[d] for d in cols), Decimal("0"))
    total_liters = sum((liters_by_day[d] for d in cols), Decimal("0"))
    branch = farmer.branch
    period_payment = FarmerPeriodPayment.objects.filter(
        farmer=farmer, period_start=start, period_end=end
    ).first()
    if period_payment and period_payment.rate is not None:
        rate = period_payment.rate
        apply_rate_paid = period_payment.apply_rate_paid or "yes"
        if period_payment.total_liters is not None:
            total_liters = period_payment.total_liters
    else:
        latest = FarmerRateHistory.objects.filter(farmer=farmer).order_by("-effective_at", "-id").first()
        rate = latest.rate if latest else farmer.rate
        apply_rate_paid = (latest.apply_rate_paid if latest else farmer.apply_rate_paid) or "yes"
    day_rows = []
    for d in cols:
        kg = kg_by_day[d].quantize(Decimal("0.01"))
        liters = liters_by_day[d].quantize(Decimal("0.01"))
        amount = None
        qty = payment_qty_for_branch(branch, kg=kg, liters=liters)
        if apply_rate_paid == "yes" and qty > 0:
            amount = farmer_payment_gross(
                branch, rate, kg=kg, liters=liters, apply_rate_paid=apply_rate_paid
            )
        day_rows.append({"date": d, "liters": liters, "kg": kg, "amount": amount})
    gross = farmer_payment_gross(
        branch,
        rate,
        kg=total_kg,
        liters=total_liters,
        apply_rate_paid=apply_rate_paid,
    )
    if period_payment and period_payment.gross_amount is not None:
        gross = period_payment.gross_amount
    skip_bucket = payment_period_deduction_skip_map([farmer.id], start, end)[farmer.id]
    advances = [
        a
        for a in FarmerAdvancePayment.objects.filter(
            farmer=farmer, date__gte=start, date__lte=end
        ).order_by("-date")
        if a.id not in skip_bucket["advance_ids"]
    ]
    advance_payments = [a for a in advances if not advance_is_bank_loan(a)]
    bank_loan_advances = [a for a in advances if advance_is_bank_loan(a)]
    adv_total = sum(
        (a.amount or Decimal("0") for a in advance_payments), Decimal("0")
    ).quantize(Decimal("0.01"))
    bank_loan_advance_total = sum(
        (a.amount or Decimal("0") for a in bank_loan_advances), Decimal("0")
    ).quantize(Decimal("0.01"))
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)
    goods_rows = list(
        FarmerGoodsIssue.objects.filter(
            issue_to_type=FarmerGoodsIssue.IssueToType.FARMER,
            issue_to_id=farmer.id,
            date__gte=start_dt,
            date__lt=end_dt,
        )
        .select_related("product")
        .only("date", "product_id", "quantity", "batch_ref", "is_settled", "settled_at", "product__name", "product__category")
        .order_by("-date", "-id")
    )
    goods_rows = [
        issue for issue in goods_rows if not goods_issue_is_skipped(issue, skip_bucket)
    ]
    (
        goods_items,
        goods_total,
        goods_feed_total,
        goods_vitamin_total,
        goods_omi_total,
        goods_minerals_total,
        goods_others_total,
    ) = summarize_priced_farmer_goods_issues(goods_rows)
    loan_items, loan_total_calculated = loan_deduction_items_for_farmer(
        farmer, start, end, pending_only=False
    )
    skipped_loan_ids = skip_bucket["loan_schedule_ids"]
    loan_items = [item for item in loan_items if item["schedule_id"] not in skipped_loan_ids]
    loan_total_calculated = sum(
        (item["amount"] for item in loan_items), Decimal("0")
    ).quantize(Decimal("0.01"))
    settings_map = loan_deduction_settings_map([farmer.id], start, end)
    deduct_loan = settings_map.get(farmer.id, True)
    loan_instalment = applied_loan_deduction(loan_total_calculated, farmer.id, settings_map)
    loan_total_calculated = (loan_total_calculated + bank_loan_advance_total).quantize(
        Decimal("0.01")
    )
    loan_total = (loan_instalment + bank_loan_advance_total).quantize(Decimal("0.01"))
    print_loan_amount, print_loan_paid_recorded, _ = _farmer_loan_print_summary(farmer)
    period_loan_paid = loan_total
    print_loan_paid = (print_loan_paid_recorded + period_loan_paid).quantize(Decimal("0.01"))
    print_loan_balance = (print_loan_amount - print_loan_paid).quantize(Decimal("0.01"))
    return {
        "farmer": farmer,
        "farmer_display_name": farmer_payslip_display_name(farmer),
        "start": start,
        "end": end,
        "date_cols": cols,
        "day_rows": day_rows,
        "rate": rate,
        "apply_rate_paid": apply_rate_paid,
        "total_liters": total_liters.quantize(Decimal("0.01")),
        "gross_amount": gross,
        "advance_total": adv_total,
        "goods_items": goods_items,
        "goods_total": goods_total,
        "goods_feed_total": goods_feed_total,
        "goods_vitamin_total": goods_vitamin_total,
        "goods_omi_total": goods_omi_total,
        "goods_minerals_total": goods_minerals_total,
        "goods_others_total": goods_others_total,
        "loan_items": loan_items,
        "loan_total_calculated": loan_total_calculated,
        "loan_total": loan_total,
        "deduct_loan": deduct_loan,
        "net_amount": (gross - adv_total - goods_total - loan_total).quantize(Decimal("0.01")),
        "payment_sheet_period": payment_sheet_period_english(start, end),
        "payment_sheet_period_si": payment_sheet_period_sinhala(start, end),
        "print_loan_amount": print_loan_amount,
        "print_loan_paid": print_loan_paid,
        "print_loan_balance": print_loan_balance,
        "advances": advance_payments,
        "bank_loan_advances": bank_loan_advances,
    }


def _import_cell_missing(v):
    return cell_missing(v)


def _import_str_cell(row, *keys):
    for k in keys:
        if k not in row:
            continue
        v = row[k]
        if _import_cell_missing(v):
            continue
        s = str(v).strip()
        if s.lower() == "nan":
            continue
        return s
    return ""


def _import_decimal_cell(row, key, default=None):
    if key not in row:
        return default
    v = row[key]
    if _import_cell_missing(v):
        return default
    return v


def _import_time_cell(row, key):
    if key not in row:
        return None
    v = row[key]
    if _import_cell_missing(v):
        return None
    if isinstance(v, time):
        return v
    if isinstance(v, datetime):
        return v.time()
    s = str(v).strip()
    if not s or s.lower() == "nan":
        return None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(s, fmt).time()
        except ValueError:
            continue
    return None


def _import_choice_cell(row, key, allowed, default):
    if key not in row:
        return default
    v = row[key]
    if _import_cell_missing(v):
        return default
    s = str(v).strip().lower().replace(" ", "_")
    if s in allowed:
        return s
    return default


def _date_range_inclusive(start, end):
    days = []
    cur = start
    while cur <= end:
        days.append(cur)
        cur = cur + timedelta(days=1)
    return days


def _latest_farmer_rate_map(farmer_ids):
    latest_rows = {}
    for row in FarmerRateHistory.objects.filter(farmer_id__in=farmer_ids).order_by("farmer_id", "-effective_at", "-id"):
        if row.farmer_id not in latest_rows:
            latest_rows[row.farmer_id] = row
    return latest_rows


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard.html'

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user = self.request.user
        if not user.has_perm("collections.view_milkcollection"):
            ctx["dashboard_welcome_only"] = True
            ctx["user_display_name"] = user.get_full_name() or user.get_username()
            return ctx
        ctx["dashboard_welcome_only"] = False
        default_branch_id = get_default_branch_id(user)
        selected_branch = (self.request.GET.get("branch") or (str(default_branch_id) if default_branch_id else "all")).strip().lower()
        branches_qs = Branch.objects.all() if user.is_superuser else get_allowed_branches_qs(user)
        allowed_ids = list(branches_qs.values_list("id", flat=True))

        milk_qs = MilkCollection.objects.route_collections()
        raw_qs = RawMilkSupplierCollection.objects.exclude(
            status=RawMilkSupplierCollection.CollectionStatus.CANCELLED
        )
        dispatch_qs = MilkDistribution.objects.exclude(
            status=MilkDistribution.DistributionStatus.CANCELLED
        )
        stock_qs = BranchMilkStock.objects.all()

        if selected_branch != "all":
            try:
                selected_branch_id = int(selected_branch)
            except (TypeError, ValueError):
                selected_branch_id = None
            if selected_branch_id and (user.is_superuser or selected_branch_id in allowed_ids):
                milk_qs = milk_qs.filter(branch_id=selected_branch_id)
                raw_qs = raw_qs.filter(branch_id=selected_branch_id)
                dispatch_qs = dispatch_qs.filter(branch_id=selected_branch_id)
                stock_qs = stock_qs.filter(branch_id=selected_branch_id)
                current_branch = branches_qs.filter(pk=selected_branch_id).first()
                ctx["dashboard_scope_label"] = (
                    f"{current_branch.code} - {current_branch.name}" if current_branch else "Selected branch"
                )
                ctx["selected_branch"] = str(selected_branch_id)
            else:
                milk_qs = milk_qs.none()
                raw_qs = raw_qs.none()
                dispatch_qs = dispatch_qs.none()
                stock_qs = stock_qs.none()
                ctx["dashboard_scope_label"] = "No branch access"
                ctx["selected_branch"] = "all"
        else:
            if not user.is_superuser:
                milk_qs = milk_qs.filter(branch_id__in=allowed_ids) if allowed_ids else milk_qs.none()
                raw_qs = raw_qs.filter(branch_id__in=allowed_ids) if allowed_ids else raw_qs.none()
                dispatch_qs = (
                    dispatch_qs.filter(branch_id__in=allowed_ids) if allowed_ids else dispatch_qs.none()
                )
                stock_qs = stock_qs.filter(branch_id__in=allowed_ids) if allowed_ids else stock_qs.none()
            ctx["dashboard_scope_label"] = "All accessible branches"
            ctx["selected_branch"] = "all"

        milk_totals = milk_qs.aggregate(total_kg=Sum("kg"), total_liters=Sum("liters"))
        raw_totals = raw_qs.aggregate(total_kg=Sum("kg"), total_liters=Sum("liters"))
        dispatched = dispatch_qs.aggregate(total_dispatched_kg=Sum("kg"))
        if ctx.get("selected_branch", "all") != "all":
            scope_branches = branches_qs.filter(pk=int(ctx["selected_branch"]))
        else:
            scope_branches = branches_qs
        stock_snap = sum_stock_snapshots(scope_branches)
        opening_kg = stock_snap["opening_kg"]
        opening_liters = (opening_kg * MILK_LITER_FACTOR).quantize(Decimal("0.01"))
        route_data = list(
            milk_qs.values('route__name').annotate(total_kg=Sum('kg')).order_by('route__name')
        )
        raw_total_kg = raw_totals["total_kg"] or 0
        if raw_total_kg:
            route_data.append({"route__name": "RAW_MILK_SUPPLIER", "total_kg": raw_total_kg})
        net_adjustment_kg = stock_snap["net_adjustment_kg"]
        net_adjustment_liters = (net_adjustment_kg * MILK_LITER_FACTOR).quantize(Decimal("0.01"))
        ctx["total_kg"] = (milk_totals["total_kg"] or 0) + (raw_totals["total_kg"] or 0) + opening_kg + net_adjustment_kg
        ctx["total_liters"] = (
            (milk_totals["total_liters"] or 0)
            + (raw_totals["total_liters"] or 0)
            + opening_liters
            + net_adjustment_liters
        )
        ctx["total_dispatched_kg"] = dispatched["total_dispatched_kg"] or 0
        ctx["current_stock_kg"] = stock_snap["current_kg"]
        ctx['chart_labels'] = [item['route__name'] for item in route_data]
        ctx['chart_values'] = [float(item['total_kg']) for item in route_data]
        ctx["dashboard_branches"] = branches_qs.order_by("code", "name")
        return ctx

class SummaryView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = 'reports/summary.html'
    permission_required = "collections.view_milkcollection"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        filters = {}
        start = self.request.GET.get('start')
        end = self.request.GET.get('end')
        route = self.request.GET.get('route')
        point = self.request.GET.get('point')
        farmer = self.request.GET.get('farmer')
        branch_ids = self.request.GET.getlist("branches")
        include_all = self.request.GET.get("all_branches") == "1"
        allowed_qs = get_allowed_branches_qs(self.request.user)
        allowed_ids = list(allowed_qs.values_list("id", flat=True))
        default_branch_id = get_default_branch_id(self.request.user)
        if not include_all and not branch_ids and default_branch_id:
            branch_ids = [str(default_branch_id)]
        selected_branch_ids = []
        if start:
            filters['date__gte'] = start
        if end:
            filters['date__lte'] = end
        if route:
            filters['route_id'] = route
        if point:
            filters['collection_point_id'] = point
        if farmer:
            filters['farmer_id'] = farmer
        if include_all:
            if not self.request.user.is_superuser:
                filters["branch_id__in"] = allowed_ids
        elif branch_ids:
            safe_ids = [int(b) for b in branch_ids if str(b).isdigit()]
            if self.request.user.is_superuser:
                filters["branch_id__in"] = safe_ids
                selected_branch_ids = safe_ids
            else:
                selected_branch_ids = [b for b in safe_ids if b in allowed_ids]
                filters["branch_id__in"] = selected_branch_ids
        elif not self.request.user.is_superuser:
            filters["branch_id__in"] = allowed_ids
        ctx['rows'] = get_collection_summary(filters)
        routes_qs = Route.objects.select_related("branch").all()
        points_qs = CollectionPoint.objects.select_related("route", "route__branch")
        farmers_qs = Farmer.objects.select_related("branch").order_by("common_name", "full_name")
        if not self.request.user.is_superuser:
            routes_qs = routes_qs.filter(branch_id__in=allowed_ids)
            points_qs = points_qs.filter(route__branch_id__in=allowed_ids)
            farmers_qs = farmers_qs.filter(branch_id__in=allowed_ids)
        if selected_branch_ids and not include_all:
            routes_qs = routes_qs.filter(branch_id__in=selected_branch_ids)
            points_qs = points_qs.filter(route__branch_id__in=selected_branch_ids)
            farmers_qs = farmers_qs.filter(branch_id__in=selected_branch_ids)
        ctx['routes'] = routes_qs
        ctx['points'] = points_qs
        ctx['farmers'] = farmers_qs
        ctx['branches'] = Branch.objects.filter(id__in=allowed_ids) if not self.request.user.is_superuser else Branch.objects.all()
        ctx["selected_branches"] = [str(b) for b in branch_ids]
        return ctx


class PaymentPeriodBranchMixin:
    SESSION_KEY = "farmer_payment_period"

    def _session_period(self):
        return self.request.session.get(self.SESSION_KEY) or {}

    def _store_payment_period(self, start, end, branch_ids):
        self.request.session[self.SESSION_KEY] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "branches": [int(b) for b in branch_ids],
        }

    def _selected_dates(self):
        start_raw = (self.request.GET.get("start") or "").strip()
        end_raw = (self.request.GET.get("end") or "").strip()
        if not start_raw and not end_raw:
            stored = self._session_period()
            start_raw = (stored.get("start") or "").strip()
            end_raw = (stored.get("end") or "").strip()
        today = datetime.now().date()
        start = datetime.strptime(start_raw, "%Y-%m-%d").date() if start_raw else today.replace(day=1)
        end = datetime.strptime(end_raw, "%Y-%m-%d").date() if end_raw else today
        if start > end:
            start, end = end, start
        return start, end

    def _selected_branch_ids(self):
        allowed_ids = list(get_allowed_branches_qs(self.request.user).values_list("id", flat=True))
        branch_ids_raw = self.request.GET.getlist("branches")
        if not branch_ids_raw:
            stored = self._session_period()
            stored_branches = stored.get("branches") or []
            if stored_branches:
                branch_ids_raw = [str(b) for b in stored_branches]
        if self.request.user.is_superuser:
            if branch_ids_raw:
                return [int(v) for v in branch_ids_raw if str(v).isdigit()]
            return list(Branch.objects.values_list("id", flat=True))
        chosen = [int(v) for v in branch_ids_raw if str(v).isdigit()]
        if chosen:
            return [v for v in chosen if v in allowed_ids]
        return allowed_ids

    def _filter_branches_qs(self):
        if self.request.user.is_superuser:
            return Branch.objects.order_by("code", "name")
        return get_allowed_branches_qs(self.request.user).order_by("code", "name")


def _parse_payment_period_from_post(request, session_key=None):
    start_raw = (request.POST.get("start") or "").strip()
    end_raw = (request.POST.get("end") or "").strip()
    if (not start_raw or not end_raw) and session_key:
        stored = request.session.get(session_key) or {}
        start_raw = start_raw or (stored.get("start") or "").strip()
        end_raw = end_raw or (stored.get("end") or "").strip()
    today = datetime.now().date()
    start = datetime.strptime(start_raw, "%Y-%m-%d").date() if start_raw else today.replace(day=1)
    end = datetime.strptime(end_raw, "%Y-%m-%d").date() if end_raw else today
    if start > end:
        start, end = end, start
    return start, end


def _posted_payment_date(request):
    raw = (request.POST.get("payment_date") or "").strip()
    if not raw:
        return timezone.localdate()
    try:
        return datetime.strptime(raw[:10], "%Y-%m-%d").date()
    except ValueError:
        return timezone.localdate()


def _branch_ids_from_post(request, session_key=None):
    branch_ids_raw = request.POST.getlist("branches")
    if not branch_ids_raw and session_key:
        stored = request.session.get(session_key) or {}
        branch_ids_raw = [str(b) for b in stored.get("branches") or []]
    return [int(v) for v in branch_ids_raw if str(v).isdigit()]


def _safe_return_url(request, fallback_name):
    return_url = (request.POST.get("return_url") or "").strip()
    if return_url.startswith("/"):
        return return_url
    return reverse(fallback_name)


def _wants_json(request):
    accept = (request.headers.get("Accept") or "").lower()
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in accept
    )


class FarmerPaymentSheetView(PaymentPeriodBranchMixin, LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "reports/farmer_payment_sheet.html"
    permission_required = "reports.view_farmerpaymentsheet"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        start, end = self._selected_dates()
        branch_ids = self._selected_branch_ids()
        date_cols, rows, sheet_totals = build_farmer_payment_sheet_data(branch_ids, start, end)
        can_manage_loan_deduction = self.request.user.has_perm("farmer_loans.change_farmerloan")
        ctx["rows"] = rows
        ctx["sheet_totals"] = sheet_totals
        ctx["can_view_payment_settlement"] = self.request.user.has_perm(
            "reports.view_paymentsettlement"
        )
        ctx["can_record_farmer_payment"] = (
            ctx["can_view_payment_settlement"]
            and self.request.user.has_perm("reports.add_farmerperiodpayment")
        )
        ctx["can_manage_loan_deduction"] = can_manage_loan_deduction
        ctx["can_manage_payment_deductions"] = self.request.user.has_perm(
            "reports.change_farmerpaymentperioddeductionskip"
        )
        ctx["can_add_advance"] = self.request.user.has_perm("masters.change_farmer")
        advance_farmers_qs = Farmer.objects.filter(branch_id__in=branch_ids).order_by(
            "common_name", "full_name"
        )
        advance_farmer_ids = list(advance_farmers_qs.values_list("id", flat=True))
        balance_summaries = build_farmer_payment_summaries(
            advance_farmer_ids, branch_ids, start, end, pending_only=True
        )
        ctx["advance_form"] = FarmerAdvancePaymentForm(
            farmers_qs=advance_farmers_qs,
            initial={"date": end},
        )
        ctx["farmer_balance_data"] = {
            str(farmer_id): {
                "gross": str(data["gross_amount"]),
                "advanced": str(data["advance_amount"]),
                "goods": str(data["goods_deduction"]),
                "loan": str(data["loan_deduction"]),
                "net": str(data["net_amount"]),
            }
            for farmer_id, data in balance_summaries.items()
        }
        ctx["date_cols"] = date_cols
        ctx["start"] = start
        ctx["end"] = end
        ctx["route_groups"] = group_farmer_payment_sheet_by_route(rows, date_cols)
        branch_labels = [
            (branch.name or branch.code or str(branch.pk)).strip()
            for branch in self._filter_branches_qs().filter(pk__in=branch_ids)
        ]
        ctx["branch_summary_label"] = ", ".join(branch_labels) if branch_labels else "All branches"
        ctx["branches"] = self._filter_branches_qs()
        ctx["selected_branches"] = [str(v) for v in branch_ids]
        branch_key = payment_sheet_branch_key(branch_ids)
        ctx["paid_sheet_record"] = FarmerPaymentSheetRecord.objects.filter(
            period_start=start,
            period_end=end,
            branch_key=branch_key,
        ).first()
        from .bank_ceft import company_debit_accounts

        ctx["company_debit_accounts"] = company_debit_accounts()
        self._store_payment_period(start, end, branch_ids)
        return ctx


class PaidFarmerPaymentSheetListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    template_name = "reports/paid_farmer_payment_sheet_list.html"
    permission_required = "reports.view_farmerpaymentsheet"
    model = FarmerPaymentSheetRecord
    context_object_name = "records"
    paginate_by = 25

    def get_queryset(self):
        qs = FarmerPaymentSheetRecord.objects.all()
        allowed_ids = set(
            get_allowed_branches_qs(self.request.user).values_list("id", flat=True)
        )
        if not self.request.user.is_superuser:
            visible = []
            for record in qs:
                record_branch_ids = {
                    int(b) for b in (record.branch_ids or []) if str(b).isdigit()
                }
                if not record_branch_ids or record_branch_ids <= allowed_ids:
                    visible.append(record.pk)
            qs = qs.filter(pk__in=visible)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["branch_lookup"] = {
            branch.id: branch
            for branch in get_allowed_branches_qs(self.request.user).order_by("code", "name")
        }
        return ctx


class FarmerPaymentSheetExportMixin(PaymentPeriodBranchMixin):
    def _payment_sheet_export_data(self):
        start, end = self._selected_dates()
        branch_ids = self._selected_branch_ids()
        date_cols, rows, sheet_totals = build_farmer_payment_sheet_data(branch_ids, start, end)
        return date_cols, rows, sheet_totals, start, end


class FarmerPaymentSheetExcelView(
    FarmerPaymentSheetExportMixin, LoginRequiredMixin, PermissionRequiredMixin, View
):
    permission_required = "reports.view_farmerpaymentsheet"

    def get(self, request):
        date_cols, rows, sheet_totals, start, end = self._payment_sheet_export_data()
        include_settlement = request.user.has_perm("reports.view_paymentsettlement")
        matrix = payment_sheet_export_matrix(
            date_cols, rows, sheet_totals, include_settlement=include_settlement
        )
        pd = get_pandas()
        df = pd.DataFrame(matrix[1:], columns=matrix[0])
        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="farmer_payment_sheet_{start}_{end}.xlsx"'
        )
        with pd.ExcelWriter(response, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="PaymentSheet")
        return response


class FarmerPaymentSheetPDFView(
    FarmerPaymentSheetExportMixin, LoginRequiredMixin, PermissionRequiredMixin, View
):
    permission_required = "reports.view_farmerpaymentsheet"

    def get(self, request):
        date_cols, rows, sheet_totals, start, end = self._payment_sheet_export_data()
        include_settlement = request.user.has_perm("reports.view_paymentsettlement")
        matrix = payment_sheet_export_matrix(
            date_cols, rows, sheet_totals, include_settlement=include_settlement
        )

        def fmt_cell(value, col_idx):
            if col_idx == 0 or value == "":
                return "" if value == "" else str(value)
            try:
                return format_money(value, empty=str(value))
            except (TypeError, ValueError):
                return str(value)

        table_data = [
            [fmt_cell(cell, col_idx) for col_idx, cell in enumerate(row)] for row in matrix
        ]

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=8 * mm,
            rightMargin=8 * mm,
            topMargin=10 * mm,
            bottomMargin=10 * mm,
        )
        styles = getSampleStyleSheet()
        elements = [
            Paragraph(f"Farmer Payment Sheet — {start} to {end}", styles["Heading3"]),
            Spacer(1, 6),
        ]
        table = Table(table_data, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 6),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                    ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f8fafc")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        elements.append(table)
        doc.build(elements)
        buffer.seek(0)
        response = HttpResponse(buffer, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="farmer_payment_sheet_{start}_{end}.pdf"'
        )
        return response


class FarmerPaymentSheetBankFileView(
    FarmerPaymentSheetExportMixin, LoginRequiredMixin, PermissionRequiredMixin, View
):
    """Download CEFT-style bank payment upload file for Bank-method farmer rows."""

    permission_required = "reports.view_farmerpaymentsheet"
    http_method_names = ["get", "post"]

    def _param(self, name):
        return (self.request.POST.get(name) or self.request.GET.get(name) or "").strip()

    def _paramlist(self, name):
        values = self.request.POST.getlist(name) or self.request.GET.getlist(name)
        return [str(v) for v in values if str(v).strip()]

    def _selected_dates(self):
        start_raw = self._param("start")
        end_raw = self._param("end")
        if not start_raw and not end_raw:
            stored = self._session_period()
            start_raw = (stored.get("start") or "").strip()
            end_raw = (stored.get("end") or "").strip()
        today = datetime.now().date()
        start = datetime.strptime(start_raw, "%Y-%m-%d").date() if start_raw else today.replace(day=1)
        end = datetime.strptime(end_raw, "%Y-%m-%d").date() if end_raw else today
        if start > end:
            start, end = end, start
        return start, end

    def _selected_branch_ids(self):
        allowed_ids = list(get_allowed_branches_qs(self.request.user).values_list("id", flat=True))
        branch_ids_raw = self._paramlist("branches")
        if not branch_ids_raw:
            stored = self._session_period()
            stored_branches = stored.get("branches") or []
            if stored_branches:
                branch_ids_raw = [str(b) for b in stored_branches]
        if self.request.user.is_superuser:
            if branch_ids_raw:
                return [int(v) for v in branch_ids_raw if str(v).isdigit()]
            return list(Branch.objects.values_list("id", flat=True))
        chosen = [int(v) for v in branch_ids_raw if str(v).isdigit()]
        if chosen:
            return [v for v in chosen if v in allowed_ids]
        return allowed_ids

    def _selected_farmer_ids(self, request):
        raw_ids = request.POST.getlist("farmer_ids") or request.GET.getlist("farmers")
        ids = []
        for raw in raw_ids:
            try:
                ids.append(int(str(raw).strip()))
            except (TypeError, ValueError):
                continue
        return ids

    def post(self, request):
        return self.get(request)

    def get(self, request):
        from .bank_ceft import (
            CEFT_SHEET_NAME,
            build_ceft_matrix,
            format_ceft_value_date,
            resolve_debit_account_no,
        )

        date_cols, rows, sheet_totals, start, end = self._payment_sheet_export_data()
        selected_ids = set(self._selected_farmer_ids(request))
        export_rows = []
        for row in rows:
            farmer = row.get("farmer")
            if not farmer:
                continue
            if selected_ids and farmer.pk not in selected_ids:
                continue
            amount = row.get("balance_amount") or Decimal("0")
            if Decimal(amount or 0) <= 0:
                continue
            if not row.get("can_make_payment"):
                continue
            account = None
            primary_id = row.get("primary_bank_account_id")
            accounts = list(farmer.bank_accounts.all())
            if primary_id:
                account = next((a for a in accounts if a.pk == primary_id), None)
            if account is None:
                account = next((a for a in accounts if a.is_primary), accounts[0] if accounts else None)
            export_rows.append({"farmer": farmer, "amount": amount, "account": account})

        debit_account = resolve_debit_account_no(
            account_id=self._param("debit_account_id"),
            account_no=self._param("debit_account_no"),
        )
        value_date = format_ceft_value_date(self._param("value_date") or end)
        matrix, skipped = build_ceft_matrix(
            export_rows,
            debit_account_no=debit_account,
            value_date=value_date,
            start=start,
            end=end,
        )
        if len(matrix) <= 1:
            detail = (
                "; ".join(skipped[:5])
                if skipped
                else "No payable bank rows with complete account codes."
            )
            messages.error(request, f"Could not build bank payment file. {detail}")
            qs = f"?start={start}&end={end}" + "".join(
                f"&branches={bid}" for bid in self._selected_branch_ids()
            )
            return redirect(reverse("farmer-payment-sheet") + qs)

        pd = get_pandas()
        df = pd.DataFrame(matrix[1:], columns=matrix[0])
        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        branch_ids = self._selected_branch_ids()
        branch_label = "farmers"
        if len(branch_ids) == 1:
            branch = Branch.objects.filter(pk=branch_ids[0]).first()
            if branch:
                branch_label = (
                    re.sub(r"[^A-Za-z0-9]+", "_", (branch.name or branch.code or "farmers")).strip("_")
                    or "farmers"
                )
        response["Content-Disposition"] = (
            f'attachment; filename="{branch_label}_FARMERS_{start.strftime("%b_%d").upper()}.xlsx"'
        )
        with pd.ExcelWriter(response, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name=CEFT_SHEET_NAME)
        if skipped:
            messages.warning(
                request,
                f"Bank file downloaded with {len(matrix) - 1} row(s). Skipped {len(skipped)}: "
                + "; ".join(skipped[:3]),
            )
        return response


class CollectionPointPaymentPeriodBranchMixin(PaymentPeriodBranchMixin):
    SESSION_KEY = "collection_point_payment_period"


class CollectionPointPaymentSheetView(
    CollectionPointPaymentPeriodBranchMixin, LoginRequiredMixin, PermissionRequiredMixin, TemplateView
):
    template_name = "reports/collection_point_payment_sheet.html"
    permission_required = "reports.view_collectionpointpaymentsheet"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        start, end = self._selected_dates()
        branch_ids = self._selected_branch_ids()
        date_cols, rows, sheet_totals = build_collection_point_payment_sheet_data(
            branch_ids, start, end
            )
        ctx["rows"] = rows
        ctx["sheet_totals"] = sheet_totals
        ctx["route_groups"] = group_collection_point_payment_sheet_by_route(
            rows, date_cols, sheet_totals.get("mixed_units", False)
        )
        branch_labels = [
            (branch.name or branch.code or str(branch.pk)).strip()
            for branch in self._filter_branches_qs().filter(pk__in=branch_ids)
        ]
        ctx["branch_summary_label"] = ", ".join(branch_labels) if branch_labels else "All branches"
        ctx["can_view_payment_settlement"] = self.request.user.has_perm(
            "reports.view_paymentsettlement"
        )
        ctx["can_record_point_payment"] = (
            ctx["can_view_payment_settlement"]
            and self.request.user.has_perm("reports.add_collectionpointperiodpayment")
        )
        ctx["can_manage_payment_deductions"] = (
            self.request.user.has_perm("reports.change_collectionpointpaymentperioddeductionskip")
            or self.request.user.has_perm("reports.change_farmerpaymentperioddeductionskip")
        )
        ctx["can_edit_payment_corrections"] = (
            ctx["can_view_payment_settlement"]
            and self.request.user.has_perm("reports.change_collectionpointpaymentcorrection")
        )
        ctx["can_manage_cp_loan_deduction"] = self.request.user.has_perm(
            "collection_point_loans.change_collectionpointloan"
        )
        ctx["date_cols"] = date_cols
        ctx["start"] = start
        ctx["end"] = end
        ctx["branches"] = self._filter_branches_qs()
        ctx["selected_branches"] = [str(v) for v in branch_ids]
        branch_key = payment_sheet_branch_key(branch_ids)
        ctx["paid_sheet_record"] = CollectionPointPaymentSheetRecord.objects.filter(
            period_start=start,
            period_end=end,
            branch_key=branch_key,
        ).first()
        from .bank_ceft import company_debit_accounts

        ctx["company_debit_accounts"] = company_debit_accounts()
        self._store_payment_period(start, end, branch_ids)
        return ctx


class PaidCollectionPointPaymentSheetListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    template_name = "reports/paid_collection_point_payment_sheet_list.html"
    permission_required = "reports.view_collectionpointpaymentsheet"
    model = CollectionPointPaymentSheetRecord
    context_object_name = "records"
    paginate_by = 25

    def get_queryset(self):
        qs = CollectionPointPaymentSheetRecord.objects.all()
        allowed_ids = set(
            get_allowed_branches_qs(self.request.user).values_list("id", flat=True)
        )
        if not self.request.user.is_superuser:
            visible = []
            for record in qs:
                record_branch_ids = {
                    int(b) for b in (record.branch_ids or []) if str(b).isdigit()
                }
                if not record_branch_ids or record_branch_ids <= allowed_ids:
                    visible.append(record.pk)
            qs = qs.filter(pk__in=visible)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["branch_lookup"] = {
            branch.id: branch
            for branch in get_allowed_branches_qs(self.request.user).order_by("code", "name")
        }
        return ctx


class CollectionPointPaymentSheetExportMixin(CollectionPointPaymentPeriodBranchMixin):
    def _payment_sheet_export_data(self):
        start, end = self._selected_dates()
        branch_ids = self._selected_branch_ids()
        date_cols, rows, sheet_totals = build_collection_point_payment_sheet_data(
            branch_ids, start, end
        )
        return date_cols, rows, sheet_totals, start, end


class CollectionPointPaymentSheetExcelView(
    CollectionPointPaymentSheetExportMixin, LoginRequiredMixin, PermissionRequiredMixin, View
):
    permission_required = "reports.view_collectionpointpaymentsheet"

    def get(self, request):
        date_cols, rows, sheet_totals, start, end = self._payment_sheet_export_data()
        include_settlement = request.user.has_perm("reports.view_paymentsettlement")
        matrix = collection_point_payment_sheet_export_matrix(
            date_cols, rows, sheet_totals, include_settlement=include_settlement
        )
        pd = get_pandas()
        df = pd.DataFrame(matrix[1:], columns=matrix[0])
        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="collection_point_payment_sheet_{start}_{end}.xlsx"'
        )
        with pd.ExcelWriter(response, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="PaymentSheet")
        return response


class CollectionPointPaymentSheetPDFView(
    CollectionPointPaymentSheetExportMixin, LoginRequiredMixin, PermissionRequiredMixin, View
):
    permission_required = "reports.view_collectionpointpaymentsheet"

    def get(self, request):
        date_cols, rows, sheet_totals, start, end = self._payment_sheet_export_data()
        include_settlement = request.user.has_perm("reports.view_paymentsettlement")
        matrix = collection_point_payment_sheet_export_matrix(
            date_cols, rows, sheet_totals, include_settlement=include_settlement
        )

        def fmt_cell(value, col_idx):
            if col_idx in {0, 1, 3} or value == "":
                return "" if value == "" else str(value)
            try:
                return format_money(value, empty=str(value))
            except (TypeError, ValueError):
                return str(value)

        table_data = [
            [fmt_cell(cell, col_idx) for col_idx, cell in enumerate(row)] for row in matrix
        ]

        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=8 * mm,
            rightMargin=8 * mm,
            topMargin=10 * mm,
            bottomMargin=10 * mm,
        )
        styles = getSampleStyleSheet()
        elements = [
            Paragraph(f"Collection Point Payment Sheet — {start} to {end}", styles["Heading3"]),
            Spacer(1, 6),
        ]
        table = Table(table_data, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 6),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                    ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f8fafc")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        elements.append(table)
        doc.build(elements)
        buffer.seek(0)
        response = HttpResponse(buffer, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="collection_point_payment_sheet_{start}_{end}.pdf"'
        )
        return response


class CollectionPointPaymentSheetBankFileView(
    CollectionPointPaymentSheetExportMixin, LoginRequiredMixin, PermissionRequiredMixin, View
):
    """Download CEFT-style bank payment upload file for Bank-method rows."""

    permission_required = "reports.view_collectionpointpaymentsheet"
    http_method_names = ["get", "post"]

    def _param(self, name):
        return (self.request.POST.get(name) or self.request.GET.get(name) or "").strip()

    def _paramlist(self, name):
        values = self.request.POST.getlist(name) or self.request.GET.getlist(name)
        return [str(v) for v in values if str(v).strip()]

    def _selected_dates(self):
        start_raw = self._param("start")
        end_raw = self._param("end")
        if not start_raw and not end_raw:
            stored = self._session_period()
            start_raw = (stored.get("start") or "").strip()
            end_raw = (stored.get("end") or "").strip()
        today = datetime.now().date()
        start = datetime.strptime(start_raw, "%Y-%m-%d").date() if start_raw else today.replace(day=1)
        end = datetime.strptime(end_raw, "%Y-%m-%d").date() if end_raw else today
        if start > end:
            start, end = end, start
        return start, end

    def _selected_branch_ids(self):
        allowed_ids = list(get_allowed_branches_qs(self.request.user).values_list("id", flat=True))
        branch_ids_raw = self._paramlist("branches")
        if not branch_ids_raw:
            stored = self._session_period()
            stored_branches = stored.get("branches") or []
            if stored_branches:
                branch_ids_raw = [str(b) for b in stored_branches]
        if self.request.user.is_superuser:
            if branch_ids_raw:
                return [int(v) for v in branch_ids_raw if str(v).isdigit()]
            return list(Branch.objects.values_list("id", flat=True))
        chosen = [int(v) for v in branch_ids_raw if str(v).isdigit()]
        if chosen:
            return [v for v in chosen if v in allowed_ids]
        return allowed_ids

    def _selected_point_ids(self, request):
        raw_ids = request.POST.getlist("point_ids") or request.GET.getlist("points")
        ids = []
        for raw in raw_ids:
            try:
                ids.append(int(str(raw).strip()))
            except (TypeError, ValueError):
                continue
        return ids

    def post(self, request):
        return self.get(request)

    def get(self, request):
        from .bank_ceft import (
            CEFT_SHEET_NAME,
            build_ceft_matrix,
            format_ceft_value_date,
            resolve_debit_account_no,
        )

        date_cols, rows, sheet_totals, start, end = self._payment_sheet_export_data()
        selected_ids = set(self._selected_point_ids(request))
        export_rows = []
        for row in rows:
            point = row.get("point")
            if not point:
                continue
            if selected_ids and point.pk not in selected_ids:
                continue
            amount = row.get("payment_amount") or row.get("balance_amount") or Decimal("0")
            if Decimal(amount or 0) <= 0:
                continue
            if not row.get("can_make_payment"):
                continue
            account = None
            primary_id = row.get("primary_bank_account_id")
            accounts = list(point.bank_accounts.all())
            if primary_id:
                account = next((a for a in accounts if a.pk == primary_id), None)
            if account is None:
                account = next((a for a in accounts if a.is_primary), accounts[0] if accounts else None)
            export_rows.append({"point": point, "amount": amount, "account": account})

        debit_account = resolve_debit_account_no(
            account_id=self._param("debit_account_id"),
            account_no=self._param("debit_account_no"),
        )
        value_date = format_ceft_value_date(self._param("value_date") or end)
        matrix, skipped = build_ceft_matrix(
            export_rows,
            debit_account_no=debit_account,
            value_date=value_date,
            start=start,
            end=end,
        )
        if len(matrix) <= 1:
            detail = (
                "; ".join(skipped[:5])
                if skipped
                else "No payable bank rows with complete account codes."
            )
            messages.error(request, f"Could not build bank payment file. {detail}")
            qs = f"?start={start}&end={end}" + "".join(
                f"&branches={bid}" for bid in self._selected_branch_ids()
            )
            return redirect(reverse("collection-point-payment-sheet") + qs)

        pd = get_pandas()
        df = pd.DataFrame(matrix[1:], columns=matrix[0])
        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        branch_ids = self._selected_branch_ids()
        branch_label = "bank"
        if len(branch_ids) == 1:
            branch = Branch.objects.filter(pk=branch_ids[0]).first()
            if branch:
                branch_label = (
                    re.sub(r"[^A-Za-z0-9]+", "_", (branch.name or branch.code or "bank")).strip("_")
                    or "bank"
                )
        response["Content-Disposition"] = (
            f'attachment; filename="{branch_label}_{start.strftime("%b_%d").upper()}.xlsx"'
        )
        with pd.ExcelWriter(response, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name=CEFT_SHEET_NAME)
        if skipped:
            messages.warning(
                request,
                f"Bank file downloaded with {len(matrix) - 1} row(s). Skipped {len(skipped)}: "
                + "; ".join(skipped[:3]),
            )
        return response


class PointReconcileReportPeriodMixin(PaymentPeriodBranchMixin):
    SESSION_KEY = "point_reconcile_report_period"


class PointReconcileReportView(
    PointReconcileReportPeriodMixin, LoginRequiredMixin, PermissionRequiredMixin, TemplateView
):
    template_name = "reports/point_reconcile_report.html"
    permission_required = "collections.view_milkcollectionreconcile"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        start, end = self._selected_dates()
        branch_ids = self._selected_branch_ids()
        status_filter = (self.request.GET.get("reconcile_status") or "").strip().lower()
        branch_sections, grand_totals = build_branch_point_reconcile_report(
            branch_ids,
            start,
            end,
            status_filter=status_filter,
        )
        ctx["branch_sections"] = branch_sections
        ctx["grand_totals"] = grand_totals
        ctx["reconcile_status"] = status_filter
        ctx["start"] = start
        ctx["end"] = end
        ctx["branches"] = self._filter_branches_qs()
        ctx["selected_branches"] = [str(v) for v in branch_ids]
        self._store_payment_period(start, end, branch_ids)
        return ctx


class PointReconcileReportExportMixin(PointReconcileReportPeriodMixin):
    def _report_export_data(self):
        start, end = self._selected_dates()
        branch_ids = self._selected_branch_ids()
        status_filter = (self.request.GET.get("reconcile_status") or "").strip().lower()
        branch_sections, grand_totals = build_branch_point_reconcile_report(
            branch_ids,
            start,
            end,
            status_filter=status_filter,
        )
        return branch_sections, grand_totals, start, end


class PointReconcileReportExcelView(
    PointReconcileReportExportMixin, LoginRequiredMixin, PermissionRequiredMixin, View
):
    permission_required = "collections.view_milkcollectionreconcile"

    def get(self, request):
        branch_sections, grand_totals, start, end = self._report_export_data()
        matrix = point_reconcile_report_export_matrix(branch_sections, grand_totals)
        pd = get_pandas()
        df = pd.DataFrame(matrix[1:], columns=matrix[0])
        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="point_reconcile_report_{start}_{end}.xlsx"'
        )
        with pd.ExcelWriter(response, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="PointReconcile")
        return response


class PointReconcileReportPDFView(
    PointReconcileReportExportMixin, LoginRequiredMixin, PermissionRequiredMixin, View
):
    permission_required = "collections.view_milkcollectionreconcile"

    def get(self, request):
        branch_sections, grand_totals, start, end = self._report_export_data()
        matrix = point_reconcile_report_export_matrix(branch_sections, grand_totals)

        def fmt_cell(value, col_idx):
            if col_idx in {0, 1, 2, 3, 7, 9} or value == "":
                return "" if value == "" else str(value)
            try:
                return format_money(value, empty=str(value))
            except (TypeError, ValueError):
                return str(value)

        table_data = [
            [fmt_cell(cell, col_idx) for col_idx, cell in enumerate(row)] for row in matrix
        ]
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=landscape(A4),
            leftMargin=8 * mm,
            rightMargin=8 * mm,
            topMargin=10 * mm,
            bottomMargin=10 * mm,
        )
        styles = getSampleStyleSheet()
        elements = [
            Paragraph(f"Point Reconcile Report — {start} to {end}", styles["Heading3"]),
            Spacer(1, 6),
        ]
        table = Table(table_data, repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 7),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("ALIGN", (4, 0), (8, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        elements.append(table)
        doc.build(elements)
        buffer.seek(0)
        response = HttpResponse(buffer, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="point_reconcile_report_{start}_{end}.pdf"'
        )
        return response


class FarmerGoodsSummaryReportPeriodMixin(PaymentPeriodBranchMixin):
    SESSION_KEY = "farmer_goods_summary_period"

    def _selected_branch(self):
        # Only honor explicit GET; empty means All branches (do not restore a forced session branch).
        raw = (self.request.GET.get("branch") or "").strip()
        return raw if raw.isdigit() else ""

    def _selected_branch_ids(self):
        allowed_ids = list(get_allowed_branches_qs(self.request.user).values_list("id", flat=True))
        branch_raw = self._selected_branch()
        if branch_raw.isdigit():
            branch_id = int(branch_raw)
            if self.request.user.is_superuser:
                return [branch_id]
            if branch_id in allowed_ids:
                return [branch_id]
        if self.request.user.is_superuser:
            return list(Branch.objects.values_list("id", flat=True))
        return allowed_ids

    def _store_payment_period(self, start, end, branch_ids):
        branch = (self.request.GET.get("branch") or "").strip()
        self.request.session[self.SESSION_KEY] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "branch": branch if branch.isdigit() else "",
        }

    def _selected_route_id(self):
        raw = (self.request.GET.get("route") or "").strip()
        if not raw.isdigit():
            return None
        route_id = int(raw)
        route = Route.objects.filter(pk=route_id).only("id", "branch_id").first()
        if not route:
            return None
        selected_branch = self._selected_branch()
        if selected_branch.isdigit() and route.branch_id != int(selected_branch):
            return None
        if not self.request.user.is_superuser:
            allowed_ids = set(get_allowed_branches_qs(self.request.user).values_list("id", flat=True))
            if route.branch_id not in allowed_ids:
                return None
        return route_id

    def _selected_farmer_id(self):
        raw = (self.request.GET.get("farmer") or "").strip()
        if not raw.isdigit():
            return None
        farmer_id = int(raw)
        farmer = (
            Farmer.objects.select_related("route", "collection_point", "collection_point__route")
            .filter(pk=farmer_id)
            .first()
        )
        if not farmer:
            return None
        farmer_branch_id = farmer.branch_id
        if not farmer_branch_id and farmer.route_id:
            farmer_branch_id = farmer.route.branch_id
        if not farmer_branch_id and farmer.collection_point_id:
            farmer_branch_id = farmer.collection_point.route.branch_id
        selected_branch = self._selected_branch()
        if selected_branch.isdigit() and farmer_branch_id != int(selected_branch):
            return None
        selected_route = self._selected_route_id()
        farmer_route_id = farmer.route_id
        if not farmer_route_id and farmer.collection_point_id:
            farmer_route_id = farmer.collection_point.route_id
        if selected_route and farmer_route_id != selected_route:
            return None
        if not self.request.user.is_superuser:
            allowed_ids = set(get_allowed_branches_qs(self.request.user).values_list("id", flat=True))
            if farmer_branch_id not in allowed_ids:
                return None
        return farmer_id

    def _farmer_goods_filter_context(self):
        allowed_qs = self._filter_branches_qs()
        route_qs = Route.objects.select_related("branch").order_by("code", "name")
        if not self.request.user.is_superuser:
            allowed_ids = list(allowed_qs.values_list("id", flat=True))
            route_qs = route_qs.filter(branch_id__in=allowed_ids)
        farmer_qs = (
            Farmer.objects.select_related("route", "branch", "collection_point", "collection_point__route")
            .order_by("registration_number", "common_name", "full_name")
        )
        if not self.request.user.is_superuser:
            allowed_ids = list(allowed_qs.values_list("id", flat=True))
            farmer_qs = farmer_qs.filter(branch_id__in=allowed_ids)
        selected_route = self._selected_route_id()
        selected_farmer = self._selected_farmer_id()

        def _farmer_branch_id(f):
            if f.branch_id:
                return f.branch_id
            if f.route_id:
                return f.route.branch_id
            if f.collection_point_id:
                return f.collection_point.route.branch_id
            return ""

        def _farmer_route_id(f):
            if f.route_id:
                return f.route_id
            if f.collection_point_id:
                return f.collection_point.route_id
            return ""

        return {
            "filter_routes": route_qs,
            "selected_route": str(selected_route or ""),
            "filter_farmers": farmer_qs,
            "selected_farmer": str(selected_farmer or ""),
            "farmer_goods_filter_routes_json": [
                {"id": r.id, "branch_id": r.branch_id, "label": r.name}
                for r in route_qs
            ],
            "farmer_goods_filter_farmers_json": [
                {
                    "id": f.id,
                    "branch_id": _farmer_branch_id(f),
                    "route_id": _farmer_route_id(f),
                    "label": str(f),
                }
                for f in farmer_qs
            ],
        }

    def _farmer_goods_filter_query(self, start, end):
        from urllib.parse import urlencode

        params = [("start", start.isoformat()), ("end", end.isoformat())]
        selected_branch = self._selected_branch()
        if selected_branch.isdigit():
            params.append(("branch", selected_branch))
        route_id = self._selected_route_id()
        if route_id:
            params.append(("route", route_id))
        farmer_id = self._selected_farmer_id()
        if farmer_id:
            params.append(("farmer", farmer_id))
        return urlencode(params)

    def _build_farmer_goods_report(self):
        start, end = self._selected_dates()
        branch_ids = self._selected_branch_ids()
        branch_sections, grand_totals = build_branch_farmer_goods_summary_report(
            branch_ids,
            start,
            end,
        )
        branch_sections, grand_totals = filter_farmer_goods_branch_sections(
            branch_sections,
            route_id=self._selected_route_id(),
            farmer_id=self._selected_farmer_id(),
        )
        return branch_sections, grand_totals, start, end


class FarmerGoodsSummaryReportView(
    FarmerGoodsSummaryReportPeriodMixin, LoginRequiredMixin, PermissionRequiredMixin, TemplateView
):
    template_name = "reports/farmer_goods_summary_report.html"
    permission_required = "suppliers.view_farmergoodsissue"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        branch_sections, grand_totals, start, end = self._build_farmer_goods_report()
        ctx["branch_sections"] = branch_sections
        ctx["grand_totals"] = grand_totals
        ctx["start"] = start
        ctx["end"] = end
        ctx["branches"] = self._filter_branches_qs().order_by("name")
        ctx["selected_branch"] = self._selected_branch()
        ctx.update(self._farmer_goods_filter_context())
        ctx["farmer_goods_filter_query"] = self._farmer_goods_filter_query(start, end)
        ctx["generated_at"] = timezone.localtime()
        ctx["print_footer"] = PAYSLIP_LABELS_EN
        self._store_payment_period(start, end, self._selected_branch_ids())
        return ctx


class FarmerGoodsSummarySlipPrintView(
    FarmerGoodsSummaryReportPeriodMixin, LoginRequiredMixin, PermissionRequiredMixin, TemplateView
):
    template_name = "reports/farmer_goods_summary_slip_print.html"
    permission_required = "suppliers.view_farmergoodsissue"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        branch_sections, grand_totals, start, end = self._build_farmer_goods_report()
        ctx["branch_sections"] = branch_sections
        ctx["grand_totals"] = grand_totals
        ctx["start"] = start
        ctx["end"] = end
        ctx["generated_at"] = timezone.localtime()
        ctx["back_url"] = (
            f"{reverse('farmer-goods-summary-report')}?{self._farmer_goods_filter_query(start, end)}"
        )
        self._store_payment_period(start, end, self._selected_branch_ids())
        return ctx


class FarmerGoodsSummaryReportExportMixin(FarmerGoodsSummaryReportPeriodMixin):
    def _report_export_data(self):
        branch_sections, grand_totals, start, end = self._build_farmer_goods_report()
        return branch_sections, grand_totals, start, end


class FarmerGoodsSummaryReportExcelView(
    FarmerGoodsSummaryReportExportMixin, LoginRequiredMixin, PermissionRequiredMixin, View
):
    permission_required = "suppliers.view_farmergoodsissue"

    def get(self, request):
        branch_sections, grand_totals, start, end = self._report_export_data()
        matrix = farmer_goods_summary_export_matrix(branch_sections, grand_totals, start, end)
        pd = get_pandas()
        df = pd.DataFrame(matrix[2:], columns=matrix[1])
        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="farmer_goods_summary_{start}_{end}.xlsx"'
        )
        with pd.ExcelWriter(response, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="FarmerGoods", startrow=1)
            ws = writer.sheets["FarmerGoods"]
            ws.cell(row=1, column=1, value=matrix[0][0])
        return response


class FarmerGoodsSummaryReportPDFView(
    FarmerGoodsSummaryReportExportMixin, LoginRequiredMixin, PermissionRequiredMixin, View
):
    permission_required = "suppliers.view_farmergoodsissue"

    def get(self, request):
        branch_sections, grand_totals, start, end = self._report_export_data()
        matrix = farmer_goods_summary_export_matrix(branch_sections, grand_totals, start, end)

        def fmt_cell(value, col_idx):
            if col_idx in {0, 1, 2, 3, 4} or value == "":
                return "" if value == "" else str(value)
            try:
                return format_money(value, empty=str(value))
            except (TypeError, ValueError):
                return str(value)

        table_data = [
            [fmt_cell(cell, col_idx) for col_idx, cell in enumerate(row)] for row in matrix[2:]
        ]
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=10 * mm,
            rightMargin=10 * mm,
            topMargin=12 * mm,
            bottomMargin=12 * mm,
        )
        styles = getSampleStyleSheet()
        elements = [
            Paragraph(matrix[0][0], styles["Heading3"]),
            Spacer(1, 8),
        ]
        table = Table([matrix[1], *table_data], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("ALIGN", (5, 0), (7, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        elements.append(table)
        doc.build(elements)
        buffer.seek(0)
        response = HttpResponse(buffer, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="farmer_goods_summary_{start}_{end}.pdf"'
        )
        return response


class PointGoodsSummaryReportPeriodMixin(PaymentPeriodBranchMixin):
    SESSION_KEY = "point_goods_summary_period"

    def _selected_branch(self):
        # Only honor explicit GET; empty means All branches (do not restore a forced session branch).
        raw = (self.request.GET.get("branch") or "").strip()
        return raw if raw.isdigit() else ""

    def _selected_branch_ids(self):
        allowed_ids = list(get_allowed_branches_qs(self.request.user).values_list("id", flat=True))
        branch_raw = self._selected_branch()
        if branch_raw.isdigit():
            branch_id = int(branch_raw)
            if self.request.user.is_superuser:
                return [branch_id]
            if branch_id in allowed_ids:
                return [branch_id]
        if self.request.user.is_superuser:
            return list(Branch.objects.values_list("id", flat=True))
        return allowed_ids

    def _store_payment_period(self, start, end, branch_ids):
        branch = (self.request.GET.get("branch") or "").strip()
        self.request.session[self.SESSION_KEY] = {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "branch": branch if branch.isdigit() else "",
        }

    def _selected_route_id(self):
        raw = (self.request.GET.get("route") or "").strip()
        if not raw.isdigit():
            return None
        route_id = int(raw)
        route = Route.objects.filter(pk=route_id).only("id", "branch_id").first()
        if not route:
            return None
        selected_branch = self._selected_branch()
        if selected_branch.isdigit() and route.branch_id != int(selected_branch):
            return None
        if not self.request.user.is_superuser:
            allowed_ids = set(get_allowed_branches_qs(self.request.user).values_list("id", flat=True))
            if route.branch_id not in allowed_ids:
                return None
        return route_id

    def _selected_point_id(self):
        raw = (self.request.GET.get("collection_point") or self.request.GET.get("point") or "").strip()
        if not raw.isdigit():
            return None
        point_id = int(raw)
        point = (
            CollectionPoint.objects.select_related("route")
            .filter(pk=point_id)
            .first()
        )
        if not point:
            return None
        selected_branch = self._selected_branch()
        if selected_branch.isdigit() and point.route.branch_id != int(selected_branch):
            return None
        selected_route = self._selected_route_id()
        if selected_route and point.route_id != selected_route:
            return None
        if not self.request.user.is_superuser:
            allowed_ids = set(get_allowed_branches_qs(self.request.user).values_list("id", flat=True))
            if point.route.branch_id not in allowed_ids:
                return None
        return point_id

    def _point_goods_filter_context(self):
        allowed_qs = self._filter_branches_qs()
        route_qs = Route.objects.select_related("branch").order_by("code", "name")
        if not self.request.user.is_superuser:
            allowed_ids = list(allowed_qs.values_list("id", flat=True))
            route_qs = route_qs.filter(branch_id__in=allowed_ids)
        point_qs = (
            CollectionPoint.objects.select_related("route", "route__branch")
            .order_by("route__code", "route__name", "number", "name")
        )
        if not self.request.user.is_superuser:
            allowed_ids = list(allowed_qs.values_list("id", flat=True))
            point_qs = point_qs.filter(route__branch_id__in=allowed_ids)
        selected_route = self._selected_route_id()
        selected_point = self._selected_point_id()
        return {
            # Full lists for client cascade (Branch → Route → Collection point).
            "filter_routes": route_qs,
            "selected_route": str(selected_route or ""),
            "filter_points": point_qs,
            "selected_collection_point": str(selected_point or ""),
            "point_goods_filter_routes_json": [
                {"id": r.id, "branch_id": r.branch_id, "label": r.name}
                for r in route_qs
            ],
            "point_goods_filter_points_json": [
                {
                    "id": p.id,
                    "branch_id": p.route.branch_id,
                    "route_id": p.route_id,
                    "label": f"{p.number} — {p.name}",
                }
                for p in point_qs
            ],
        }

    def _point_goods_filter_query(self, start, end):
        from urllib.parse import urlencode

        params = [("start", start.isoformat()), ("end", end.isoformat())]
        selected_branch = self._selected_branch()
        if selected_branch.isdigit():
            params.append(("branch", selected_branch))
        route_id = self._selected_route_id()
        if route_id:
            params.append(("route", route_id))
        point_id = self._selected_point_id()
        if point_id:
            params.append(("collection_point", point_id))
        return urlencode(params)

    def _build_point_goods_report(self):
        start, end = self._selected_dates()
        branch_ids = self._selected_branch_ids()
        branch_sections, grand_totals = build_branch_point_goods_summary_report(
            branch_ids,
            start,
            end,
        )
        branch_sections, grand_totals = filter_point_goods_branch_sections(
            branch_sections,
            route_id=self._selected_route_id(),
            point_id=self._selected_point_id(),
        )
        return branch_sections, grand_totals, start, end


class PointGoodsSummaryReportView(
    PointGoodsSummaryReportPeriodMixin, LoginRequiredMixin, PermissionRequiredMixin, TemplateView
):
    template_name = "reports/point_goods_summary_report.html"
    permission_required = "reports.view_pointgoodssummary"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        branch_sections, grand_totals, start, end = self._build_point_goods_report()
        ctx["branch_sections"] = branch_sections
        ctx["grand_totals"] = grand_totals
        ctx["start"] = start
        ctx["end"] = end
        ctx["branches"] = self._filter_branches_qs().order_by("name")
        ctx["selected_branch"] = self._selected_branch()
        ctx.update(self._point_goods_filter_context())
        ctx["point_goods_filter_query"] = self._point_goods_filter_query(start, end)
        ctx["generated_at"] = timezone.localtime()
        ctx["print_footer"] = PAYSLIP_LABELS_EN
        self._store_payment_period(start, end, self._selected_branch_ids())
        return ctx


class PointGoodsSummarySlipPrintView(
    PointGoodsSummaryReportPeriodMixin, LoginRequiredMixin, PermissionRequiredMixin, TemplateView
):
    template_name = "reports/point_goods_summary_slip_print.html"
    permission_required = "reports.view_pointgoodssummary"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        branch_sections, grand_totals, start, end = self._build_point_goods_report()
        ctx["branch_sections"] = branch_sections
        ctx["grand_totals"] = grand_totals
        ctx["start"] = start
        ctx["end"] = end
        ctx["generated_at"] = timezone.localtime()
        ctx["back_url"] = (
            f"{reverse('point-goods-summary-report')}?{self._point_goods_filter_query(start, end)}"
        )
        self._store_payment_period(start, end, self._selected_branch_ids())
        return ctx


class PointGoodsSummaryReportExportMixin(PointGoodsSummaryReportPeriodMixin):
    def _report_export_data(self):
        branch_sections, grand_totals, start, end = self._build_point_goods_report()
        return branch_sections, grand_totals, start, end


class PointGoodsSummaryReportExcelView(
    PointGoodsSummaryReportExportMixin, LoginRequiredMixin, PermissionRequiredMixin, View
):
    permission_required = "reports.view_pointgoodssummary"

    def get(self, request):
        branch_sections, grand_totals, start, end = self._report_export_data()
        matrix = point_goods_summary_export_matrix(branch_sections, grand_totals, start, end)
        pd = get_pandas()
        df = pd.DataFrame(matrix[2:], columns=matrix[1])
        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="point_goods_summary_{start}_{end}.xlsx"'
        )
        with pd.ExcelWriter(response, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="PointGoods", startrow=1)
            ws = writer.sheets["PointGoods"]
            ws.cell(row=1, column=1, value=matrix[0][0])
        return response


class PointGoodsSummaryReportPDFView(
    PointGoodsSummaryReportExportMixin, LoginRequiredMixin, PermissionRequiredMixin, View
):
    permission_required = "reports.view_pointgoodssummary"

    def get(self, request):
        branch_sections, grand_totals, start, end = self._report_export_data()
        matrix = point_goods_summary_export_matrix(branch_sections, grand_totals, start, end)

        def fmt_cell(value, col_idx):
            if col_idx in {0, 1, 2, 3, 4} or value == "":
                return "" if value == "" else str(value)
            try:
                return format_money(value, empty=str(value))
            except (TypeError, ValueError):
                return str(value)

        table_data = [
            [fmt_cell(cell, col_idx) for col_idx, cell in enumerate(row)] for row in matrix[2:]
        ]
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            leftMargin=10 * mm,
            rightMargin=10 * mm,
            topMargin=12 * mm,
            bottomMargin=12 * mm,
        )
        styles = getSampleStyleSheet()
        elements = [
            Paragraph(matrix[0][0], styles["Heading3"]),
            Spacer(1, 8),
        ]
        table = Table([matrix[1], *table_data], repeatRows=1)
        table.setStyle(
            TableStyle(
                [
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e2e8f0")),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("ALIGN", (5, 0), (7, -1), "RIGHT"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        elements.append(table)
        doc.build(elements)
        buffer.seek(0)
        response = HttpResponse(buffer, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="point_goods_summary_{start}_{end}.pdf"'
        )
        return response


class CollectionPointPaySlipView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "reports/collection_point_payslip_print.html"
    permission_required = "reports.view_collectionpointpaymentsheet"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        point = get_object_or_404(
            filter_by_user_branches(
                CollectionPoint.objects.filter(pk=kwargs["pk"]).select_related("route", "route__branch"),
                self.request.user,
                "route__branch_id",
            )
        )
        start_raw = (self.request.GET.get("start") or "").strip()
        end_raw = (self.request.GET.get("end") or "").strip()
        today = datetime.now().date()
        start = datetime.strptime(start_raw, "%Y-%m-%d").date() if start_raw else today.replace(day=1)
        end = datetime.strptime(end_raw, "%Y-%m-%d").date() if end_raw else today
        if start > end:
            start, end = end, start
        branch_ids = [int(v) for v in self.request.GET.getlist("branches") if str(v).isdigit()]
        if not branch_ids:
            stored = self.request.session.get(CollectionPointPaymentPeriodBranchMixin.SESSION_KEY) or {}
            branch_ids = [int(b) for b in stored.get("branches") or [] if str(b).isdigit()]
        if not branch_ids and point.route and point.route.branch_id:
            branch_ids = [point.route.branch_id]
        slip = build_collection_point_payslip_context(point, start, end, branch_ids)
        ctx.update(slip)
        ctx["ps"] = slip
        period_net = Decimal(slip.get("net_amount") or Decimal("0")).quantize(Decimal("0.01"))
        allow_zero_settlement = collection_point_has_zero_cash_settlement_items(
            advance_amount=slip.get("advance_amount"),
            goods_deduction=slip.get("goods_deduction"),
            loan_deduction=slip.get("loan_total"),
            previous_outstanding=slip.get("previous_outstanding_amount"),
        ) and period_net == Decimal("0.00")
        pending = collection_point_pending_payable_amount(
            point, branch_ids, start, end, balance_payment=True
        )
        period_payment = collection_point_period_payment_map([point.id], start, end).get(
            point.id
        )
        action_flags = collection_point_payment_action_flags(
            pending,
            period_payment,
            allow_zero_settlement=allow_zero_settlement,
        )
        balance_amount = action_flags["balance_amount"]
        paid_amount = max(
            Decimal("0"), (period_net - balance_amount).quantize(Decimal("0.01"))
        )
        ctx["paid_amount"] = paid_amount
        ctx["balance_amount"] = balance_amount
        ctx["can_make_payment"] = action_flags["can_make_payment"]
        ctx["needs_carry_forward"] = action_flags["needs_carry_forward"]
        ctx["carry_forward_amount"] = action_flags["carry_forward_amount"]
        ctx["payment_amount"] = (
            Decimal("0.00") if action_flags["needs_carry_forward"] else balance_amount
        )
        ctx["can_view_payment_settlement"] = self.request.user.has_perm(
            "reports.view_paymentsettlement"
        )
        ctx["can_record_payment"] = (
            ctx["can_make_payment"]
            and ctx["can_view_payment_settlement"]
            and self.request.user.has_perm("reports.add_collectionpointperiodpayment")
        )
        ctx["payslip_labels_en"] = POINT_PAYSLIP_LABELS_EN
        ctx["payslip_labels_si"] = POINT_PAYSLIP_LABELS_SI
        ctx["start"] = start
        ctx["end"] = end
        ctx["selected_branches"] = [str(v) for v in branch_ids]
        back_params = [f"start={start.isoformat()}", f"end={end.isoformat()}"]
        for branch_id in branch_ids:
            back_params.append(f"branches={branch_id}")
        ctx["back_url"] = f"{reverse('collection-point-payment-sheet')}?{'&'.join(back_params)}"
        return ctx


class CollectionPointPaySlipBulkPrintView(
    CollectionPointPaymentPeriodBranchMixin, LoginRequiredMixin, PermissionRequiredMixin, TemplateView
):
    template_name = "reports/collection_point_payslip_bulk_print.html"
    permission_required = "reports.view_collectionpointpaymentsheet"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        start, end = self._selected_dates()
        branch_ids = self._selected_branch_ids()
        print_all = self.request.GET.get("print_all") == "1"
        requested_ids = [int(v) for v in self.request.GET.getlist("points") if str(v).isdigit()]
        if print_all:
            point_ids = _payment_sheet_point_ids(start, end, branch_ids)
        else:
            point_ids = requested_ids
        points = list(
            filter_by_user_branches(
                CollectionPoint.objects.filter(id__in=point_ids)
                .select_related("route", "route__branch")
                .order_by("route__branch__code", "route__code", "number"),
                self.request.user,
                "route__branch_id",
            )
        )
        ctx["payslips"] = [
            build_collection_point_payslip_context(point, start, end, branch_ids) for point in points
        ]
        ctx["payslip_labels_en"] = POINT_PAYSLIP_LABELS_EN
        ctx["payslip_labels_si"] = POINT_PAYSLIP_LABELS_SI
        ctx["start"] = start
        ctx["end"] = end
        ctx["selected_branches"] = [str(v) for v in branch_ids]
        back_params = [f"start={start.isoformat()}", f"end={end.isoformat()}"]
        for branch_id in branch_ids:
            back_params.append(f"branches={branch_id}")
        ctx["back_url"] = f"{reverse('collection-point-payment-sheet')}?{'&'.join(back_params)}"
        return ctx


class FarmerAdvancePaymentListView(PaymentPeriodBranchMixin, LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "reports/farmer_advance_payment_list.html"
    permission_required = "masters.view_farmeradvancepayment"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        start, end = self._selected_dates()
        branch_ids = self._selected_branch_ids()
        advance_farmers_qs = Farmer.objects.filter(branch_id__in=branch_ids).order_by(
            "common_name", "full_name"
        )
        advance_farmer_ids = list(advance_farmers_qs.values_list("id", flat=True))
        payment_summaries = build_farmer_payment_summaries(
            advance_farmer_ids, branch_ids, start, end, pending_only=True
        )
        ctx["can_add_advance"] = self.request.user.has_perm("masters.change_farmer")
        ctx["advance_form"] = FarmerAdvancePaymentForm(
            farmers_qs=advance_farmers_qs,
            initial={"date": end},
        )
        ctx["advances"] = (
            FarmerAdvancePayment.objects.filter(
                farmer__branch_id__in=branch_ids,
            )
            .select_related("farmer", "created_by")
            .order_by("-date", "-id")
        )
        ctx["farmer_balance_data"] = {
            str(farmer_id): {
                "gross": str(data["gross_amount"]),
                "advanced": str(data["advance_amount"]),
                "goods": str(data["goods_deduction"]),
                "loan": str(data["loan_deduction"]),
                "net": str(data["net_amount"]),
            }
            for farmer_id, data in payment_summaries.items()
        }
        ctx["payment_period_start"] = start
        ctx["payment_period_end"] = end
        ctx["branches"] = self._filter_branches_qs()
        ctx["selected_branches"] = [str(v) for v in branch_ids]
        return ctx


class FarmerPaySlipView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "reports/farmer_payslip_print.html"
    permission_required = "reports.view_farmerpaymentsheet"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        farmer = get_object_or_404(Farmer, pk=kwargs["pk"])
        farmer = get_object_or_404(
            filter_by_user_branches(Farmer.objects.filter(pk=farmer.pk), self.request.user, "branch_id")
        )
        start_raw = (self.request.GET.get("start") or "").strip()
        end_raw = (self.request.GET.get("end") or "").strip()
        today = datetime.now().date()
        start = datetime.strptime(start_raw, "%Y-%m-%d").date() if start_raw else today.replace(day=1)
        end = datetime.strptime(end_raw, "%Y-%m-%d").date() if end_raw else today
        if start > end:
            start, end = end, start
        slip = _build_farmer_payslip_context(farmer, start, end)
        ctx.update(slip)
        ctx["ps"] = slip
        branch_ids = [int(v) for v in self.request.GET.getlist("branches") if str(v).isdigit()]
        if not branch_ids:
            stored = self.request.session.get(PaymentPeriodBranchMixin.SESSION_KEY) or {}
            branch_ids = [int(b) for b in stored.get("branches") or [] if str(b).isdigit()]
        if not branch_ids and farmer.branch_id:
            branch_ids = [farmer.branch_id]
        period_summary = build_farmer_payment_summaries(
            [farmer.id], branch_ids, start, end, pending_only=False
        ).get(farmer.id)
        pending_summary = build_farmer_payment_summaries(
            [farmer.id], branch_ids, start, end, pending_only=True
        ).get(farmer.id)
        cash_map, period_payments = farmer_overlapping_paid_data([farmer.id], start, end)
        paid_gross = farmer_paid_collection_gross_map(
            [farmer.id], branch_ids, start, end
        ).get(farmer.id, Decimal("0"))
        payment_totals = farmer_sheet_paid_and_balance(
            period_summary,
            pending_summary,
            paid_collection_gross=paid_gross,
            cash_paid=cash_map.get(farmer.id, Decimal("0")),
        )
        ctx["paid_amount"] = payment_totals["paid_amount"]
        ctx["cash_paid"] = payment_totals["cash_paid"]
        ctx["paid_advance"] = payment_totals["settled_advance"]
        ctx["paid_goods"] = payment_totals["settled_goods"]
        ctx["paid_loan"] = payment_totals["settled_loan"]
        ctx["balance_amount"] = payment_totals["balance_amount"]
        ctx["period_payments"] = period_payments.get(farmer.id, [])
        ctx["can_make_payment"] = payment_totals["balance_amount"] > Decimal("0")
        ctx["payment_amount"] = payment_totals["balance_amount"]
        ctx["can_view_payment_settlement"] = self.request.user.has_perm(
            "reports.view_paymentsettlement"
        )
        ctx["can_record_payment"] = (
            ctx["can_make_payment"]
            and ctx["can_view_payment_settlement"]
            and self.request.user.has_perm("reports.add_farmerperiodpayment")
        )
        ctx["payslip_labels_en"] = PAYSLIP_LABELS_EN
        ctx["payslip_labels_si"] = PAYSLIP_LABELS_SI
        ctx["selected_branches"] = [str(v) for v in branch_ids]
        back_params = [f"start={start.isoformat()}", f"end={end.isoformat()}"]
        for branch_id in branch_ids:
            if str(branch_id).isdigit():
                back_params.append(f"branches={branch_id}")
        ctx["back_url"] = (
            f"{reverse('farmer-payment-sheet')}?{'&'.join(back_params)}"
            if back_params
            else reverse("farmer-payment-sheet")
        )
        return ctx


class FarmerPaySlipBulkPrintView(PaymentPeriodBranchMixin, LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "reports/farmer_payslip_bulk_print.html"
    permission_required = "reports.view_farmerpaymentsheet"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        start, end = self._selected_dates()
        branch_ids = self._selected_branch_ids()
        print_all = self.request.GET.get("print_all") == "1"
        requested_ids = [int(v) for v in self.request.GET.getlist("farmers") if str(v).isdigit()]
        if print_all:
            farmer_ids = _payment_sheet_farmer_ids(start, end, branch_ids)
        else:
            farmer_ids = requested_ids
        farmers = list(
            filter_by_user_branches(
                Farmer.objects.filter(id__in=farmer_ids).order_by("common_name", "full_name"),
                self.request.user,
                "branch_id",
            )
        )
        ctx["payslips"] = [_build_farmer_payslip_context(farmer, start, end) for farmer in farmers]
        ctx["payslip_labels_en"] = PAYSLIP_LABELS_EN
        ctx["payslip_labels_si"] = PAYSLIP_LABELS_SI
        ctx["start"] = start
        ctx["end"] = end
        ctx["selected_branches"] = [str(v) for v in branch_ids]
        back_params = [f"start={start.isoformat()}", f"end={end.isoformat()}"]
        for branch_id in branch_ids:
            back_params.append(f"branches={branch_id}")
        ctx["back_url"] = f"{reverse('farmer-payment-sheet')}?{'&'.join(back_params)}"
        return ctx


class FarmerPaymentLoanDeductionToggleView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "farmer_loans.change_farmerloan"

    def post(self, request):
        farmer_id = request.POST.get("farmer")
        start_raw = (request.POST.get("start") or "").strip()
        end_raw = (request.POST.get("end") or "").strip()
        deduct_raw = (request.POST.get("deduct_loan") or "").strip().lower()
        if not farmer_id or not str(farmer_id).isdigit():
            return HttpResponse('{"ok": false, "error": "Invalid farmer."}', status=400, content_type="application/json")
        if not start_raw or not end_raw:
            return HttpResponse('{"ok": false, "error": "Period is required."}', status=400, content_type="application/json")
        try:
            period_start = datetime.strptime(start_raw, "%Y-%m-%d").date()
            period_end = datetime.strptime(end_raw, "%Y-%m-%d").date()
        except ValueError:
            return HttpResponse('{"ok": false, "error": "Invalid period."}', status=400, content_type="application/json")
        if period_start > period_end:
            period_start, period_end = period_end, period_start
        deduct_loan = deduct_raw in {"1", "true", "yes", "on"}
        farmer = get_object_or_404(
            filter_by_user_branches(Farmer.objects.filter(pk=int(farmer_id)), request.user, "branch_id")
        )
        obj, _created = FarmerPaymentLoanDeductionSetting.objects.update_or_create(
            farmer=farmer,
            period_start=period_start,
            period_end=period_end,
            defaults={"deduct_loan": deduct_loan, "updated_by": request.user},
        )
        calculated = pending_loan_deduction_map([farmer.id], period_start, period_end).get(farmer.id, Decimal("0"))
        calculated = calculated.quantize(Decimal("0.01"))
        applied = applied_loan_deduction(calculated, farmer.id, {farmer.id: obj.deduct_loan})
        branch_ids = [int(v) for v in request.POST.getlist("branches") if str(v).isdigit()]
        if not branch_ids:
            stored = request.session.get(PaymentPeriodBranchMixin.SESSION_KEY) or {}
            branch_ids = [int(b) for b in stored.get("branches") or [] if str(b).isdigit()]
        if not branch_ids and farmer.branch_id:
            branch_ids = [farmer.branch_id]
        row_totals = farmer_payment_sheet_row_snapshot(
            farmer.id, branch_ids, period_start, period_end
        )
        return HttpResponse(
            json.dumps(
                {
                    "ok": True,
                    "deduct_loan": obj.deduct_loan,
                    "loan_deduction_calculated": str(calculated),
                    "loan_deduction": str(applied),
                    "row": row_totals,
                }
            ),
            content_type="application/json",
        )


class FarmerPaymentDeductionPickerView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "reports.change_farmerpaymentperioddeductionskip"

    def get(self, request):
        farmer_id = request.GET.get("farmer")
        start_raw = (request.GET.get("start") or "").strip()
        end_raw = (request.GET.get("end") or "").strip()
        if not farmer_id or not str(farmer_id).isdigit():
            return HttpResponse('{"ok": false, "error": "Invalid farmer."}', status=400, content_type="application/json")
        if not start_raw or not end_raw:
            return HttpResponse('{"ok": false, "error": "Period is required."}', status=400, content_type="application/json")
        try:
            period_start = datetime.strptime(start_raw, "%Y-%m-%d").date()
            period_end = datetime.strptime(end_raw, "%Y-%m-%d").date()
        except ValueError:
            return HttpResponse('{"ok": false, "error": "Invalid period."}', status=400, content_type="application/json")
        if period_start > period_end:
            period_start, period_end = period_end, period_start
        farmer = get_object_or_404(
            filter_by_user_branches(Farmer.objects.filter(pk=int(farmer_id)), request.user, "branch_id")
        )
        branch_ids = [int(v) for v in request.GET.getlist("branches") if str(v).isdigit()]
        if not branch_ids:
            stored = request.session.get(PaymentPeriodBranchMixin.SESSION_KEY) or {}
            branch_ids = [int(b) for b in stored.get("branches") or [] if str(b).isdigit()]
        if not branch_ids and farmer.branch_id:
            branch_ids = [farmer.branch_id]
        skip_map = payment_period_deduction_skip_map([farmer.id], period_start, period_end)
        loan_settings_map = loan_deduction_settings_map([farmer.id], period_start, period_end)
        picker = farmer_payment_deduction_picker_data(
            farmer, period_start, period_end, skip_map, loan_settings_map
        )
        row_totals = farmer_payment_sheet_row_snapshot(
            farmer.id, branch_ids, period_start, period_end
        )
        return HttpResponse(
            json.dumps({"ok": True, "picker": picker, "row": row_totals}),
            content_type="application/json",
        )


class FarmerPaymentDeductionSkipToggleView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "reports.change_farmerpaymentperioddeductionskip"

    def post(self, request):
        farmer_id = request.POST.get("farmer")
        start_raw = (request.POST.get("start") or "").strip()
        end_raw = (request.POST.get("end") or "").strip()
        kind = (request.POST.get("kind") or "").strip()
        skip_raw = (request.POST.get("skip") or "").strip().lower()
        reference_id = request.POST.get("reference_id") or "0"
        reference_key = (request.POST.get("reference_key") or "").strip()
        valid_kinds = {choice.value for choice in FarmerPaymentPeriodDeductionSkip.Kind}
        if not farmer_id or not str(farmer_id).isdigit():
            return HttpResponse('{"ok": false, "error": "Invalid farmer."}', status=400, content_type="application/json")
        if kind not in valid_kinds:
            return HttpResponse('{"ok": false, "error": "Invalid deduction type."}', status=400, content_type="application/json")
        if not start_raw or not end_raw:
            return HttpResponse('{"ok": false, "error": "Period is required."}', status=400, content_type="application/json")
        try:
            period_start = datetime.strptime(start_raw, "%Y-%m-%d").date()
            period_end = datetime.strptime(end_raw, "%Y-%m-%d").date()
        except ValueError:
            return HttpResponse('{"ok": false, "error": "Invalid period."}', status=400, content_type="application/json")
        if period_start > period_end:
            period_start, period_end = period_end, period_start
        skip = skip_raw in {"1", "true", "yes", "on"}
        farmer = get_object_or_404(
            filter_by_user_branches(Farmer.objects.filter(pk=int(farmer_id)), request.user, "branch_id")
        )
        set_deduction_skip(
            farmer=farmer,
            period_start=period_start,
            period_end=period_end,
            kind=kind,
            skip=skip,
            reference_id=reference_id,
            reference_key=reference_key,
            updated_by=request.user,
        )
        if kind == FarmerPaymentPeriodDeductionSkip.Kind.GOODS_RECEIPT and not skip and reference_key:
            line_ids = FarmerGoodsIssue.objects.filter(
                batch_ref=reference_key,
                issue_to_type=FarmerGoodsIssue.IssueToType.FARMER,
                issue_to_id=farmer.id,
            ).values_list("id", flat=True)
            FarmerPaymentPeriodDeductionSkip.objects.filter(
                farmer=farmer,
                period_start=period_start,
                period_end=period_end,
                kind=FarmerPaymentPeriodDeductionSkip.Kind.GOODS_LINE,
                reference_id__in=line_ids,
            ).delete()
        branch_ids = [int(v) for v in request.POST.getlist("branches") if str(v).isdigit()]
        if not branch_ids:
            stored = request.session.get(PaymentPeriodBranchMixin.SESSION_KEY) or {}
            branch_ids = [int(b) for b in stored.get("branches") or [] if str(b).isdigit()]
        if not branch_ids and farmer.branch_id:
            branch_ids = [farmer.branch_id]
        row_totals = farmer_payment_sheet_row_snapshot(
            farmer.id, branch_ids, period_start, period_end
        )
        skip_map = payment_period_deduction_skip_map([farmer.id], period_start, period_end)
        loan_settings_map = loan_deduction_settings_map([farmer.id], period_start, period_end)
        picker = farmer_payment_deduction_picker_data(
            farmer, period_start, period_end, skip_map, loan_settings_map
        )
        return HttpResponse(
            json.dumps({"ok": True, "row": row_totals, "picker": picker}),
            content_type="application/json",
        )


class CollectionPointPaymentDeductionPickerView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "reports.view_collectionpointpaymentsheet"

    def get(self, request):
        point_id = request.GET.get("point")
        start_raw = (request.GET.get("start") or "").strip()
        end_raw = (request.GET.get("end") or "").strip()
        if not point_id or not str(point_id).isdigit():
            return HttpResponse('{"ok": false, "error": "Invalid collection point."}', status=400, content_type="application/json")
        if not start_raw or not end_raw:
            return HttpResponse('{"ok": false, "error": "Period is required."}', status=400, content_type="application/json")
        try:
            period_start = datetime.strptime(start_raw, "%Y-%m-%d").date()
            period_end = datetime.strptime(end_raw, "%Y-%m-%d").date()
        except ValueError:
            return HttpResponse('{"ok": false, "error": "Invalid period."}', status=400, content_type="application/json")
        if period_start > period_end:
            period_start, period_end = period_end, period_start
        point = get_object_or_404(
            filter_by_user_branches(
                CollectionPoint.objects.filter(pk=int(point_id)).select_related("route"),
                request.user,
                "route__branch_id",
            )
        )
        branch_ids = [int(v) for v in request.GET.getlist("branches") if str(v).isdigit()]
        if not branch_ids:
            stored = request.session.get(CollectionPointPaymentPeriodBranchMixin.SESSION_KEY) or {}
            branch_ids = [int(b) for b in stored.get("branches") or [] if str(b).isdigit()]
        if not branch_ids and point.route and point.route.branch_id:
            branch_ids = [point.route.branch_id]
        farmer_ids = list(
            Farmer.objects.filter(collection_point=point).values_list("id", flat=True)
        )
        can_manage = request.user.has_perm(
            "reports.change_collectionpointpaymentperioddeductionskip"
        ) or request.user.has_perm("reports.change_farmerpaymentperioddeductionskip")
        is_paid = CollectionPointPeriodPayment.objects.filter(
            collection_point=point,
            period_start=period_start,
            period_end=period_end,
        ).exists()
        readonly = is_paid or not can_manage
        cp_skip_map = collection_point_payment_period_deduction_skip_map(
            [point.id], period_start, period_end
        )
        farmer_skip_map = payment_period_deduction_skip_map(
            farmer_ids, period_start, period_end
        )
        loan_settings_map = loan_deduction_settings_map(farmer_ids, period_start, period_end)
        cp_loan_settings_map = cp_loan_deduction_settings_map([point.id], period_start, period_end)
        picker = collection_point_payment_deduction_picker_data(
            point,
            farmer_ids,
            period_start,
            period_end,
            cp_skip_map,
            farmer_skip_map,
            loan_settings_map,
            cp_loan_settings_map,
            pending_only=not is_paid,
        )
        row_totals = collection_point_payment_sheet_row_snapshot(
            point.id, branch_ids, period_start, period_end
        )
        return HttpResponse(
            json.dumps(
                {
                    "ok": True,
                    "readonly": readonly,
                    "picker": picker,
                    "row": row_totals,
                }
            ),
            content_type="application/json",
        )


class CollectionPointPaymentLoanDeductionToggleView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "collection_point_loans.change_collectionpointloan"

    def post(self, request):
        point_id = request.POST.get("point")
        start_raw = (request.POST.get("start") or "").strip()
        end_raw = (request.POST.get("end") or "").strip()
        deduct_raw = (request.POST.get("deduct_loan") or "").strip().lower()
        if not point_id or not str(point_id).isdigit():
            return HttpResponse('{"ok": false, "error": "Invalid collection point."}', status=400, content_type="application/json")
        if not start_raw or not end_raw:
            return HttpResponse('{"ok": false, "error": "Period is required."}', status=400, content_type="application/json")
        try:
            period_start = datetime.strptime(start_raw, "%Y-%m-%d").date()
            period_end = datetime.strptime(end_raw, "%Y-%m-%d").date()
        except ValueError:
            return HttpResponse('{"ok": false, "error": "Invalid period."}', status=400, content_type="application/json")
        if period_start > period_end:
            period_start, period_end = period_end, period_start
        deduct_loan = deduct_raw in {"1", "true", "yes", "on"}
        point = get_object_or_404(
            filter_by_user_branches(
                CollectionPoint.objects.filter(pk=int(point_id)).select_related("route"),
                request.user,
                "route__branch_id",
            )
        )
        if CollectionPointPeriodPayment.objects.filter(
            collection_point=point,
            period_start=period_start,
            period_end=period_end,
        ).exists():
            return HttpResponse(
                '{"ok": false, "error": "Payment already recorded; deductions cannot be changed."}',
                status=400,
                content_type="application/json",
            )
        obj, _created = CollectionPointPaymentLoanDeductionSetting.objects.update_or_create(
            collection_point=point,
            period_start=period_start,
            period_end=period_end,
            defaults={"deduct_loan": deduct_loan, "updated_by": request.user},
        )
        cp_skip_map = collection_point_payment_period_deduction_skip_map(
            [point.id], period_start, period_end
        )
        calculated = cp_loan_deduction_amount_map(
            [point.id],
            period_start,
            period_end,
            pending_only=True,
            skip_map=cp_skip_map,
            loan_settings_map={point.id: True},
        ).get(point.id, Decimal("0"))
        calculated = calculated.quantize(Decimal("0.01"))
        applied = applied_loan_deduction(calculated, point.id, {point.id: obj.deduct_loan})
        branch_ids = [int(v) for v in request.POST.getlist("branches") if str(v).isdigit()]
        if not branch_ids:
            stored = request.session.get(CollectionPointPaymentPeriodBranchMixin.SESSION_KEY) or {}
            branch_ids = [int(b) for b in stored.get("branches") or [] if str(b).isdigit()]
        if not branch_ids and point.route and point.route.branch_id:
            branch_ids = [point.route.branch_id]
        row_totals = collection_point_payment_sheet_row_snapshot(
            point.id, branch_ids, period_start, period_end
        )
        return HttpResponse(
            json.dumps(
                {
                    "ok": True,
                    "deduct_loan": obj.deduct_loan,
                    "loan_deduction_calculated": str(calculated),
                    "loan_deduction": str(applied),
                    "row": row_totals,
                }
            ),
            content_type="application/json",
        )


class CollectionPointPaymentCorrectionSaveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "reports.change_collectionpointpaymentcorrection"

    def post(self, request):
        point_id = request.POST.get("point")
        start_raw = (request.POST.get("start") or "").strip()
        end_raw = (request.POST.get("end") or "").strip()
        amount_raw = (request.POST.get("amount") or "").strip().replace(",", "")
        if not point_id or not str(point_id).isdigit():
            return HttpResponse(
                '{"ok": false, "error": "Invalid collection point."}',
                status=400,
                content_type="application/json",
            )
        if not start_raw or not end_raw:
            return HttpResponse(
                '{"ok": false, "error": "Period is required."}',
                status=400,
                content_type="application/json",
            )
        try:
            period_start = datetime.strptime(start_raw, "%Y-%m-%d").date()
            period_end = datetime.strptime(end_raw, "%Y-%m-%d").date()
        except ValueError:
            return HttpResponse(
                '{"ok": false, "error": "Invalid period."}',
                status=400,
                content_type="application/json",
            )
        if period_start > period_end:
            period_start, period_end = period_end, period_start
        try:
            amount = Decimal(amount_raw or "0")
        except InvalidOperation:
            return HttpResponse(
                '{"ok": false, "error": "Invalid correction amount."}',
                status=400,
                content_type="application/json",
            )
        point = get_object_or_404(
            filter_by_user_branches(
                CollectionPoint.objects.filter(pk=int(point_id)).select_related("route"),
                request.user,
                "route__branch_id",
            )
        )
        if CollectionPointPeriodPayment.objects.filter(
            collection_point=point,
            period_start=period_start,
            period_end=period_end,
        ).exists():
            return HttpResponse(
                '{"ok": false, "error": "Payment already recorded; corrections cannot be changed."}',
                status=400,
                content_type="application/json",
            )
        set_collection_point_payment_correction(
            point,
            period_start,
            period_end,
            amount,
            updated_by=request.user,
        )
        branch_ids = [int(v) for v in request.POST.getlist("branches") if str(v).isdigit()]
        if not branch_ids:
            stored = request.session.get(CollectionPointPaymentPeriodBranchMixin.SESSION_KEY) or {}
            branch_ids = [int(b) for b in stored.get("branches") or [] if str(b).isdigit()]
        if not branch_ids and point.route and point.route.branch_id:
            branch_ids = [point.route.branch_id]
        row_totals = collection_point_payment_sheet_row_snapshot(
            point.id, branch_ids, period_start, period_end
        )
        return HttpResponse(
            json.dumps({"ok": True, "row": row_totals}),
            content_type="application/json",
        )


class CollectionPointPaymentDeductionSkipToggleView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "reports.view_collectionpointpaymentsheet"

    def post(self, request):
        point_id = request.POST.get("point")
        farmer_id = request.POST.get("farmer")
        scope = (request.POST.get("scope") or "point").strip().lower()
        start_raw = (request.POST.get("start") or "").strip()
        end_raw = (request.POST.get("end") or "").strip()
        kind = (request.POST.get("kind") or "").strip()
        skip_raw = (request.POST.get("skip") or "").strip().lower()
        reference_id = request.POST.get("reference_id") or "0"
        reference_key = (request.POST.get("reference_key") or "").strip()
        if not point_id or not str(point_id).isdigit():
            return HttpResponse('{"ok": false, "error": "Invalid collection point."}', status=400, content_type="application/json")
        if not start_raw or not end_raw:
            return HttpResponse('{"ok": false, "error": "Period is required."}', status=400, content_type="application/json")
        try:
            period_start = datetime.strptime(start_raw, "%Y-%m-%d").date()
            period_end = datetime.strptime(end_raw, "%Y-%m-%d").date()
        except ValueError:
            return HttpResponse('{"ok": false, "error": "Invalid period."}', status=400, content_type="application/json")
        if period_start > period_end:
            period_start, period_end = period_end, period_start
        skip = skip_raw in {"1", "true", "yes", "on"}
        deduct_raw = request.POST.get("deduct_amount")
        point = get_object_or_404(
            filter_by_user_branches(
                CollectionPoint.objects.filter(pk=int(point_id)).select_related("route"),
                request.user,
                "route__branch_id",
            )
        )
        if CollectionPointPeriodPayment.objects.filter(
            collection_point=point,
            period_start=period_start,
            period_end=period_end,
        ).exists():
            return HttpResponse(
                '{"ok": false, "error": "Payment already recorded; deductions cannot be changed."}',
                status=400,
                content_type="application/json",
            )
        branch_ids = [int(v) for v in request.POST.getlist("branches") if str(v).isdigit()]
        if not branch_ids:
            stored = request.session.get(CollectionPointPaymentPeriodBranchMixin.SESSION_KEY) or {}
            branch_ids = [int(b) for b in stored.get("branches") or [] if str(b).isdigit()]
        if not branch_ids and point.route and point.route.branch_id:
            branch_ids = [point.route.branch_id]
        farmer_ids = list(
            Farmer.objects.filter(collection_point=point).values_list("id", flat=True)
        )

        if scope == "farmer":
            if not request.user.has_perm("reports.change_farmerpaymentperioddeductionskip"):
                return HttpResponse('{"ok": false, "error": "Permission denied."}', status=403, content_type="application/json")
            valid_kinds = {choice.value for choice in FarmerPaymentPeriodDeductionSkip.Kind}
            if kind not in valid_kinds:
                return HttpResponse('{"ok": false, "error": "Invalid deduction type."}', status=400, content_type="application/json")
            if not farmer_id or not str(farmer_id).isdigit():
                return HttpResponse('{"ok": false, "error": "Invalid farmer."}', status=400, content_type="application/json")
            farmer = get_object_or_404(
                filter_by_user_branches(
                    Farmer.objects.filter(pk=int(farmer_id), collection_point=point),
                    request.user,
                    "branch_id",
                )
            )
            set_deduction_skip(
                farmer=farmer,
                period_start=period_start,
                period_end=period_end,
                kind=kind,
                skip=skip,
                reference_id=reference_id,
                reference_key=reference_key,
                updated_by=request.user,
            )
            if kind == FarmerPaymentPeriodDeductionSkip.Kind.GOODS_RECEIPT and not skip and reference_key:
                line_ids = FarmerGoodsIssue.objects.filter(
                    batch_ref=reference_key,
                    issue_to_type=FarmerGoodsIssue.IssueToType.FARMER,
                    issue_to_id=farmer.id,
                ).values_list("id", flat=True)
                FarmerPaymentPeriodDeductionSkip.objects.filter(
                    farmer=farmer,
                    period_start=period_start,
                    period_end=period_end,
                    kind=FarmerPaymentPeriodDeductionSkip.Kind.GOODS_LINE,
                    reference_id__in=line_ids,
                ).delete()
        else:
            if not request.user.has_perm("reports.change_collectionpointpaymentperioddeductionskip"):
                return HttpResponse('{"ok": false, "error": "Permission denied."}', status=403, content_type="application/json")
            valid_kinds = {choice.value for choice in CollectionPointPaymentPeriodDeductionSkip.Kind}
            if kind not in valid_kinds:
                return HttpResponse('{"ok": false, "error": "Invalid deduction type."}', status=400, content_type="application/json")
            if kind == CollectionPointPaymentPeriodDeductionSkip.Kind.ADVANCE:
                if not str(reference_id).isdigit():
                    return HttpResponse('{"ok": false, "error": "Invalid advance."}', status=400, content_type="application/json")
                advance = CollectionPointAdvancePayment.objects.filter(
                    pk=int(reference_id),
                    collection_point=point,
                ).exclude(advance_type="outstanding").first()
                if advance is None:
                    return HttpResponse('{"ok": false, "error": "Advance not found."}', status=400, content_type="application/json")
                if deduct_raw is not None and str(deduct_raw).strip() != "":
                    try:
                        deduct_amount = Decimal(str(deduct_raw).replace(",", "").strip()).quantize(
                            Decimal("0.01")
                        )
                    except InvalidOperation:
                        return HttpResponse(
                            '{"ok": false, "error": "Enter a valid deduct amount."}',
                            status=400,
                            content_type="application/json",
                        )
                else:
                    remaining = collection_point_advance_remaining(advance)
                    deduct_amount = Decimal("0.00") if skip else remaining
                set_collection_point_advance_deduct(
                    collection_point=point,
                    period_start=period_start,
                    period_end=period_end,
                    advance=advance,
                    deduct_amount=deduct_amount,
                    updated_by=request.user,
                )
            elif kind == CollectionPointPaymentPeriodDeductionSkip.Kind.LOAN_INSTALMENT:
                if not str(reference_id).isdigit():
                    return HttpResponse('{"ok": false, "error": "Invalid loan instalment."}', status=400, content_type="application/json")
                schedule = (
                    CollectionPointLoanRepaymentSchedule.objects.select_related("loan")
                    .filter(pk=int(reference_id), loan__collection_point=point)
                    .first()
                )
                if schedule is None:
                    return HttpResponse('{"ok": false, "error": "Loan instalment not found."}', status=400, content_type="application/json")
                if deduct_raw is not None and str(deduct_raw).strip() != "":
                    try:
                        deduct_amount = Decimal(str(deduct_raw).replace(",", "").strip()).quantize(
                            Decimal("0.01")
                        )
                    except InvalidOperation:
                        return HttpResponse(
                            '{"ok": false, "error": "Enter a valid deduct amount."}',
                            status=400,
                            content_type="application/json",
                        )
                else:
                    remaining = collection_point_loan_remaining(schedule)
                    deduct_amount = Decimal("0.00") if skip else remaining
                set_collection_point_loan_deduct(
                    collection_point=point,
                    period_start=period_start,
                    period_end=period_end,
                    schedule=schedule,
                    deduct_amount=deduct_amount,
                    updated_by=request.user,
                )
            else:
                set_collection_point_deduction_skip(
                    collection_point=point,
                    period_start=period_start,
                    period_end=period_end,
                    kind=kind,
                    skip=skip,
                    reference_id=reference_id,
                    reference_key=reference_key,
                    updated_by=request.user,
                )
            if (
                kind == CollectionPointPaymentPeriodDeductionSkip.Kind.GOODS_RECEIPT
                and not skip
                and reference_key
            ):
                from .services import _collection_point_issue_id_candidates

                line_ids = FarmerGoodsIssue.objects.filter(
                    batch_ref=reference_key,
                    issue_to_type=FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
                    issue_to_id__in=_collection_point_issue_id_candidates(point),
                ).values_list("id", flat=True)
                CollectionPointPaymentPeriodDeductionSkip.objects.filter(
                    collection_point=point,
                    period_start=period_start,
                    period_end=period_end,
                    kind=CollectionPointPaymentPeriodDeductionSkip.Kind.GOODS_LINE,
                    reference_id__in=line_ids,
                ).delete()

        cp_skip_map = collection_point_payment_period_deduction_skip_map(
            [point.id], period_start, period_end
        )
        farmer_skip_map = payment_period_deduction_skip_map(
            farmer_ids, period_start, period_end
        )
        loan_settings_map = loan_deduction_settings_map(farmer_ids, period_start, period_end)
        cp_loan_settings_map = cp_loan_deduction_settings_map([point.id], period_start, period_end)
        picker = collection_point_payment_deduction_picker_data(
            point,
            farmer_ids,
            period_start,
            period_end,
            cp_skip_map,
            farmer_skip_map,
            loan_settings_map,
            cp_loan_settings_map,
        )
        row_totals = collection_point_payment_sheet_row_snapshot(
            point.id, branch_ids, period_start, period_end
        )
        return HttpResponse(
            json.dumps({"ok": True, "row": row_totals, "picker": picker}),
            content_type="application/json",
        )


class FarmerPeriodPaymentRecordView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "reports.add_farmerperiodpayment"

    def post(self, request):
        wants_json = _wants_json(request)
        farmer_id = request.POST.get("farmer_id")
        if not farmer_id or not str(farmer_id).isdigit():
            if wants_json:
                return JsonResponse({"ok": False, "error": "Invalid farmer."}, status=400)
            messages.error(request, "Invalid farmer.")
            return redirect(_safe_return_url(request, "farmer-payment-sheet"))
        farmer = get_object_or_404(
            filter_by_user_branches(
                Farmer.objects.filter(pk=int(farmer_id)), request.user, "branch_id"
            )
        )
        start, end = _parse_payment_period_from_post(request, PaymentPeriodBranchMixin.SESSION_KEY)
        branch_ids = _branch_ids_from_post(request, PaymentPeriodBranchMixin.SESSION_KEY)
        return_url = _safe_return_url(request, "farmer-payment-sheet")
        display_name = farmer.common_name or farmer.full_name
        form = PeriodPaymentRecordForm(request.POST)
        if not form.is_valid():
            if wants_json:
                return JsonResponse(
                    {
                        "ok": False,
                        "farmer_id": farmer.id,
                        "farmer_name": display_name,
                        "error": "Could not record payment. Check the amount and try again.",
                    },
                    status=400,
                )
            messages.error(request, "Could not record payment. Check the amount and try again.")
            return redirect(return_url)
        expected = farmer_balance_for_period(farmer.id, branch_ids, start, end)
        payment_method = (form.cleaned_data.get("payment_method") or "cash").strip()
        bank_account = None
        bank_account_id = form.cleaned_data.get("bank_account_id")
        if payment_method == "bank_transfer" and bank_account_id:
            from masters.models import FarmerBankAccount

            bank_account = FarmerBankAccount.objects.filter(
                pk=bank_account_id,
                farmer=farmer,
            ).first()
        try:
            payment = record_farmer_period_payment(
                farmer=farmer,
                period_start=start,
                period_end=end,
                amount=form.cleaned_data["amount"],
                expected_amount=expected,
                recorded_by=request.user,
                branch_ids=branch_ids,
                note=form.cleaned_data.get("note", ""),
                payment_method=payment_method,
                bank_account=bank_account,
                payment_date=form.cleaned_data.get("payment_date") or timezone.localdate(),
            )
            if wants_json:
                return JsonResponse(
                    {
                        "ok": True,
                        "farmer_id": farmer.id,
                        "farmer_name": display_name,
                        "amount": str(payment.amount),
                        "message": f"Payment recorded for {display_name}.",
                    }
                )
            messages.success(request, f"Payment recorded for {display_name}.")
        except ValidationError as exc:
            error = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            if wants_json:
                return JsonResponse(
                    {
                        "ok": False,
                        "farmer_id": farmer.id,
                        "farmer_name": display_name,
                        "error": error,
                    },
                    status=400,
                )
            messages.error(request, error)
        return redirect(return_url)


class FarmerBulkPeriodPaymentRecordView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "reports.add_farmerperiodpayment"

    def post(self, request):
        if not request.user.has_perm("reports.view_paymentsettlement"):
            messages.error(request, "You do not have permission to record payments.")
            return redirect(_safe_return_url(request, "farmer-payment-sheet"))

        farmer_ids_raw = request.POST.getlist("farmer_ids")
        farmer_ids = [int(v) for v in farmer_ids_raw if str(v).isdigit()]
        if not farmer_ids:
            messages.error(request, "No farmers selected for payment.")
            return redirect(_safe_return_url(request, "farmer-payment-sheet"))

        start, end = _parse_payment_period_from_post(request, PaymentPeriodBranchMixin.SESSION_KEY)
        branch_ids = _branch_ids_from_post(request, PaymentPeriodBranchMixin.SESSION_KEY)
        return_url = _safe_return_url(request, "farmer-payment-sheet")
        farmers = list(
            filter_by_user_branches(
                Farmer.objects.filter(id__in=farmer_ids).prefetch_related("bank_accounts"),
                request.user,
                "branch_id",
            ).order_by("common_name", "full_name")
        )
        if not farmers:
            messages.error(request, "No valid farmers selected for payment.")
            return redirect(return_url)

        note = (request.POST.get("note") or "").strip()
        payment_method = (request.POST.get("payment_method") or "").strip() or None
        result = record_farmer_bulk_period_payments(
            farmers=farmers,
            period_start=start,
            period_end=end,
            branch_ids=branch_ids,
            recorded_by=request.user,
            note=note,
            payment_method=payment_method,
            payment_date=_posted_payment_date(request),
        )
        if result["paid"]:
            messages.success(
                request,
                f"Payment recorded for {len(result['paid'])} farmer(s).",
            )
        for name, reason in result["skipped"]:
            messages.warning(request, f"{name}: {reason}")
        for name, reason in result["failed"]:
            messages.error(request, f"{name}: {reason}")
        if not result["paid"] and not result["skipped"] and not result["failed"]:
            messages.error(request, "No payments were recorded.")
        return redirect(return_url)


class CollectionPointPeriodPaymentRecordView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "reports.add_collectionpointperiodpayment"

    def post(self, request):
        wants_json = _wants_json(request)
        point_id = request.POST.get("collection_point_id")
        if not point_id or not str(point_id).isdigit():
            if wants_json:
                return JsonResponse({"ok": False, "error": "Invalid collection point."}, status=400)
            messages.error(request, "Invalid collection point.")
            return redirect(_safe_return_url(request, "collection-point-payment-sheet"))
        point = get_object_or_404(
            filter_by_user_branches(
                CollectionPoint.objects.filter(pk=int(point_id)).select_related("route"),
                request.user,
                "route__branch_id",
            )
        )
        start, end = _parse_payment_period_from_post(
            request, CollectionPointPaymentPeriodBranchMixin.SESSION_KEY
        )
        branch_ids = _branch_ids_from_post(
            request, CollectionPointPaymentPeriodBranchMixin.SESSION_KEY
        )
        if not branch_ids and point.route and point.route.branch_id:
            branch_ids = [point.route.branch_id]
        return_url = _safe_return_url(request, "collection-point-payment-sheet")
        display_name = f"{point.number} — {point.name}"
        form = PeriodPaymentRecordForm(request.POST)
        if not form.is_valid():
            if wants_json:
                return JsonResponse(
                    {
                        "ok": False,
                        "point_id": point.id,
                        "point_name": display_name,
                        "error": "Could not record payment. Check the amount and try again.",
                    },
                    status=400,
                )
            messages.error(request, "Could not record payment. Check the amount and try again.")
            return redirect(return_url)
        payment_basis = (request.POST.get("payment_basis") or "collector_fee").strip()
        settle_deductions = payment_basis == "balance"
        payment_method = (form.cleaned_data.get("payment_method") or "cash").strip()
        bank_account = None
        bank_account_id = form.cleaned_data.get("bank_account_id")
        if payment_method == "bank_transfer" and bank_account_id:
            bank_account = CollectionPointBankAccount.objects.filter(
                pk=bank_account_id,
                collection_point=point,
            ).first()
        pending = collection_point_pending_payable_amount(
            point, branch_ids, start, end, balance_payment=settle_deductions
        )
        if pending > Decimal("0"):
            expected = pending
        elif pending < Decimal("0") and settle_deductions:
            expected = Decimal("0.00")
        else:
            expected = Decimal("0.00")
        try:
            payment = record_collection_point_period_payment(
                collection_point=point,
                period_start=start,
                period_end=end,
                amount=form.cleaned_data["amount"],
                expected_amount=expected,
                recorded_by=request.user,
                branch_ids=branch_ids,
                note=form.cleaned_data.get("note", ""),
                settle_deductions=settle_deductions,
                payment_method=payment_method,
                bank_account=bank_account,
                payment_date=form.cleaned_data.get("payment_date") or timezone.localdate(),
            )
            if wants_json:
                carried = Decimal(payment.net_amount or 0) < Decimal("0") and payment.amount == 0
                return JsonResponse(
                    {
                        "ok": True,
                        "point_id": point.id,
                        "point_name": display_name,
                        "amount": str(payment.amount),
                        "message": (
                            f"Settled {display_name}. Minus balance carried forward as previous outstanding."
                            if carried
                            else f"Payment recorded for {display_name}."
                        ),
                    }
                )
            if Decimal(payment.net_amount or 0) < Decimal("0") and payment.amount == 0:
                messages.success(
                    request,
                    f"Settled {display_name}. Minus balance carried forward as previous outstanding for the next payment.",
                )
            else:
                messages.success(request, f"Payment recorded for {display_name}.")
        except ValidationError as exc:
            error = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            if wants_json:
                return JsonResponse(
                    {
                        "ok": False,
                        "point_id": point.id,
                        "point_name": display_name,
                        "error": error,
                    },
                    status=400,
                )
            messages.error(request, error)
        return redirect(return_url)


class CollectionPointBulkPeriodPaymentRecordView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "reports.add_collectionpointperiodpayment"

    def post(self, request):
        if not request.user.has_perm("reports.view_paymentsettlement"):
            messages.error(request, "You do not have permission to record payments.")
            return redirect(_safe_return_url(request, "collection-point-payment-sheet"))

        point_ids_raw = request.POST.getlist("collection_point_ids")
        point_ids = [int(v) for v in point_ids_raw if str(v).isdigit()]
        if not point_ids:
            messages.error(request, "No collection points selected for payment.")
            return redirect(_safe_return_url(request, "collection-point-payment-sheet"))

        start, end = _parse_payment_period_from_post(
            request, CollectionPointPaymentPeriodBranchMixin.SESSION_KEY
        )
        branch_ids = _branch_ids_from_post(
            request, CollectionPointPaymentPeriodBranchMixin.SESSION_KEY
        )
        return_url = _safe_return_url(request, "collection-point-payment-sheet")
        points = list(
            filter_by_user_branches(
                CollectionPoint.objects.filter(id__in=point_ids)
                .select_related("route")
                .prefetch_related("bank_accounts"),
                request.user,
                "route__branch_id",
            ).order_by("number", "name")
        )
        if not points:
            messages.error(request, "No valid collection points selected for payment.")
            return redirect(return_url)

        note = (request.POST.get("note") or "").strip()
        payment_method = (request.POST.get("payment_method") or "cash").strip()
        result = record_collection_point_bulk_period_payments(
            collection_points=points,
            period_start=start,
            period_end=end,
            branch_ids=branch_ids,
            recorded_by=request.user,
            note=note,
            settle_deductions=True,
            payment_method=payment_method,
            payment_date=_posted_payment_date(request),
        )
        if result["paid"]:
            messages.success(
                request,
                f"Payment recorded for {len(result['paid'])} collection point(s).",
            )
        for name, reason in result["skipped"]:
            messages.warning(request, f"{name}: {reason}")
        for name, reason in result["failed"]:
            messages.error(request, f"{name}: {reason}")
        if not result["paid"] and not result["skipped"] and not result["failed"]:
            messages.error(request, "No payments were recorded.")
        return redirect(return_url)


class AdvancePaymentReturnMixin:
    def _advance_return_url(self, request, return_to=None, *, list_url_name="farmer-advance-list", sheet_url_name="farmer-payment-sheet"):
        if return_to is None:
            return_to = (request.POST.get("return_to") or request.GET.get("return_to") or "advance-list").strip()
        params = []
        branch_ids = request.POST.getlist("return_branches") or request.GET.getlist("branches")
        if return_to == "advance-list":
            for branch_id in branch_ids:
                if str(branch_id).isdigit():
                    params.append(f"branches={branch_id}")
            base = reverse(list_url_name)
            if params:
                return f"{base}?{'&'.join(params)}"
            return str(base)

        start = (request.POST.get("return_start") or request.GET.get("start") or "").strip()
        end = (request.POST.get("return_end") or request.GET.get("end") or "").strip()
        if not start and not end:
            stored = request.session.get(PaymentPeriodBranchMixin.SESSION_KEY) or {}
            start = (stored.get("start") or "").strip()
            end = (stored.get("end") or "").strip()
        if start:
            params.append(f"start={start}")
        if end:
            params.append(f"end={end}")
        if not branch_ids:
            stored = request.session.get(PaymentPeriodBranchMixin.SESSION_KEY) or {}
            branch_ids = [str(b) for b in stored.get("branches") or []]
        for branch_id in branch_ids:
            if str(branch_id).isdigit():
                params.append(f"branches={branch_id}")
        base = reverse(sheet_url_name)
        if params:
            return f"{base}?{'&'.join(params)}"
        return str(base)


class CollectionPointAdvanceReturnMixin(AdvancePaymentReturnMixin):
    def _advance_return_url(self, request, return_to=None):
        return super()._advance_return_url(
            request,
            return_to=return_to,
            list_url_name="collection-point-advance-list",
            sheet_url_name="collection-point-payment-sheet",
        )


class FarmerAdvancePaymentCreateView(AdvancePaymentReturnMixin, LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "masters.change_farmer"

    def post(self, request):
        allowed_farmers = filter_by_user_branches(Farmer.objects.all(), request.user, "branch_id")
        form = FarmerAdvancePaymentForm(request.POST, farmers_qs=allowed_farmers)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.created_by = request.user
            obj.save()
            messages.success(request, "Advance payment recorded.")
        else:
            messages.error(request, "Could not save advance payment. Check farmer, date, and amount.")
        return redirect(self._advance_return_url(request))


class FarmerAdvancePaymentUpdateView(
    PaymentPeriodBranchMixin,
    AdvancePaymentReturnMixin,
    LoginRequiredMixin,
    PermissionRequiredMixin,
    UpdateView,
):
    model = FarmerAdvancePayment
    form_class = FarmerAdvancePaymentForm
    template_name = "reports/farmer_advance_payment_edit.html"
    permission_required = "masters.change_farmer"
    context_object_name = "advance"

    def get_queryset(self):
        return filter_by_user_branches(
            FarmerAdvancePayment.objects.select_related("farmer"),
            self.request.user,
            "farmer__branch_id",
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["farmers_qs"] = filter_by_user_branches(
            Farmer.objects.all(), self.request.user, "branch_id"
        )
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["selected_branches"] = [str(v) for v in self._selected_branch_ids()]
        return ctx

    def form_valid(self, form):
        messages.success(self.request, "Advance payment updated.")
        return super().form_valid(form)

    def get_success_url(self):
        return self._advance_return_url(self.request)


class FarmerAdvancePaymentDeleteView(
    AdvancePaymentReturnMixin,
    LoginRequiredMixin,
    PermissionRequiredMixin,
    RedirectGetDeleteMixin,
    DeleteView,
):
    model = FarmerAdvancePayment
    permission_required = "masters.change_farmer"

    def get_queryset(self):
        return filter_by_user_branches(
            FarmerAdvancePayment.objects.select_related("farmer"),
            self.request.user,
            "farmer__branch_id",
        )

    def post(self, request, *args, **kwargs):
        messages.success(request, "Advance payment deleted.")
        return super().post(request, *args, **kwargs)

    def get_success_url(self):
        return self._advance_return_url(self.request)


class FarmerAdvancePaymentEmbedView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "reports/farmer_advance_embed.html"
    permission_required = "masters.view_farmeradvancepayment"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        advance = get_object_or_404(
            FarmerAdvancePayment.objects.select_related("farmer", "created_by"),
            pk=kwargs["pk"],
        )
        get_object_or_404(
            filter_by_user_branches(
                Farmer.objects.filter(pk=advance.farmer_id),
                self.request.user,
                "branch_id",
            ),
        )
        ctx["advance"] = advance
        return ctx


def _manual_settlement_redirect(request, fallback_name):
    next_url = (request.POST.get("next") or "").strip()
    if next_url.startswith("/") and not next_url.startswith("//"):
        return redirect(next_url)
    return redirect(fallback_name)


class FarmerAdvanceManualSettleView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "reports.record_manual_settlement"

    def post(self, request, pk):
        advance = get_object_or_404(
            filter_by_user_branches(
                FarmerAdvancePayment.objects.select_related("farmer"),
                request.user,
                "farmer__branch_id",
            ),
            pk=pk,
        )
        recovered = (request.POST.get("recovered") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        try:
            if recovered:
                settlement_date, settlement_note = settlement_from_request(request)
                set_advance_recovered(
                    advance,
                    recovered=True,
                    recovered_at=settlement_datetime_from_date(settlement_date),
                    recovery_note=settlement_note,
                )
            else:
                set_advance_recovered(advance, recovered=False)
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
            return _manual_settlement_redirect(request, "farmer-advance-list")
        if recovered:
            messages.success(
                request,
                "Advance marked as recovered. Use this when the farmer paid outside the payment sheet.",
            )
        else:
            messages.success(request, "Advance marked as pending again.")
        return _manual_settlement_redirect(request, "farmer-advance-list")


class CollectionPointAdvancePaymentListView(
    PaymentPeriodBranchMixin, LoginRequiredMixin, PermissionRequiredMixin, TemplateView
):
    template_name = "reports/collection_point_advance_payment_list.html"
    permission_required = "masters.view_collectionpointadvancepayment"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        start, end = self._selected_dates()
        branch_ids = self._selected_branch_ids()
        points_qs = (
            CollectionPoint.objects.filter(route__branch_id__in=branch_ids, is_deleted=False)
            .select_related("route", "route__branch")
            .order_by("number", "name")
        )
        ctx["can_add_advance"] = self.request.user.has_perm("masters.change_collectionpoint")
        ctx["advance_form"] = CollectionPointAdvancePaymentForm(
            points_qs=points_qs,
            initial={"date": end},
        )
        ctx["advances"] = (
            CollectionPointAdvancePayment.objects.filter(
                collection_point__route__branch_id__in=branch_ids,
            )
            .select_related("collection_point", "collection_point__route", "created_by")
            .order_by("-date", "-id")[:200]
        )
        ctx["payment_period_start"] = start
        ctx["payment_period_end"] = end
        ctx["branches"] = self._filter_branches_qs()
        ctx["selected_branches"] = [str(v) for v in branch_ids]
        ctx["point_balance_url"] = reverse("collection-point-advance-balance")
        return ctx


class CollectionPointAdvanceBalanceView(
    PaymentPeriodBranchMixin, LoginRequiredMixin, PermissionRequiredMixin, View
):
    """On-demand balance preview for one collection point (keeps the list page fast)."""

    permission_required = "masters.view_collectionpointadvancepayment"

    def get(self, request):
        from .services import (
            _aggregate_point_farmer_payment_data,
            collection_point_pending_payable_amount,
        )

        point_id = (request.GET.get("point") or "").strip()
        if not point_id.isdigit():
            return JsonResponse({"ok": False, "error": "Select a collection point."}, status=400)
        point = get_object_or_404(
            filter_by_user_branches(
                CollectionPoint.objects.filter(is_deleted=False),
                request.user,
                "route__branch_id",
            ),
            pk=int(point_id),
        )
        start, end = self._selected_dates()
        branch_ids = self._selected_branch_ids()
        if point.route_id and point.route.branch_id not in branch_ids:
            # Align period branch filter with the point's branch when needed.
            branch_ids = [point.route.branch_id]
        farmer_ids = list(
            Farmer.objects.filter(collection_point_id=point.id).values_list("id", flat=True)
        )
        pending = _aggregate_point_farmer_payment_data(
            farmer_ids, branch_ids, start, end, point=point, pending_only=True
        )
        net = collection_point_pending_payable_amount(
            point, branch_ids, start, end, balance_payment=True
        )
        return JsonResponse(
            {
                "ok": True,
                "point_id": point.id,
                "gross": str(pending["gross_amount"]),
                "advanced": str(pending["advance_amount"]),
                "goods": str(pending["goods_deduction"]),
                "loan": str(pending["loan_deduction"]),
                "net": str(net),
            }
        )

class CollectionPointAdvancePaymentCreateView(
    CollectionPointAdvanceReturnMixin, LoginRequiredMixin, PermissionRequiredMixin, View
):
    permission_required = "masters.change_collectionpoint"

    def post(self, request):
        allowed_points = filter_by_user_branches(
            CollectionPoint.objects.filter(is_deleted=False),
            request.user,
            "route__branch_id",
        )
        form = CollectionPointAdvancePaymentForm(request.POST, points_qs=allowed_points)
        if form.is_valid():
            obj = form.save(commit=False)
            obj.created_by = request.user
            obj.save()
            messages.success(request, "Collection point advance recorded.")
        else:
            messages.error(
                request,
                "Could not save advance payment. Check collection point, date, and amount.",
            )
        return redirect(self._advance_return_url(request))


class CollectionPointAdvancePaymentUpdateView(
    PaymentPeriodBranchMixin,
    CollectionPointAdvanceReturnMixin,
    LoginRequiredMixin,
    PermissionRequiredMixin,
    UpdateView,
):
    model = CollectionPointAdvancePayment
    form_class = CollectionPointAdvancePaymentForm
    template_name = "reports/collection_point_advance_payment_edit.html"
    permission_required = "masters.change_collectionpoint"
    context_object_name = "advance"

    def get_queryset(self):
        return filter_by_user_branches(
            CollectionPointAdvancePayment.objects.select_related(
                "collection_point", "collection_point__route"
            ),
            self.request.user,
            "collection_point__route__branch_id",
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["points_qs"] = filter_by_user_branches(
            CollectionPoint.objects.filter(is_deleted=False),
            self.request.user,
            "route__branch_id",
        )
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["selected_branches"] = [str(v) for v in self._selected_branch_ids()]
        return ctx

    def form_valid(self, form):
        messages.success(self.request, "Collection point advance updated.")
        return super().form_valid(form)

    def get_success_url(self):
        return self._advance_return_url(self.request)


class CollectionPointAdvancePaymentDeleteView(
    CollectionPointAdvanceReturnMixin,
    LoginRequiredMixin,
    PermissionRequiredMixin,
    RedirectGetDeleteMixin,
    DeleteView,
):
    model = CollectionPointAdvancePayment
    permission_required = "masters.change_collectionpoint"

    def get_queryset(self):
        return filter_by_user_branches(
            CollectionPointAdvancePayment.objects.select_related("collection_point"),
            self.request.user,
            "collection_point__route__branch_id",
        )

    def post(self, request, *args, **kwargs):
        messages.success(request, "Collection point advance deleted.")
        return super().post(request, *args, **kwargs)

    def get_success_url(self):
        return self._advance_return_url(self.request)


class CollectionPointAdvanceManualSettleView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "reports.record_manual_settlement"

    def post(self, request, pk):
        advance = get_object_or_404(
            filter_by_user_branches(
                CollectionPointAdvancePayment.objects.select_related("collection_point"),
                request.user,
                "collection_point__route__branch_id",
            ),
            pk=pk,
        )
        recovered = (request.POST.get("recovered") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        action = (request.POST.get("action") or "").strip().lower()
        try:
            if action == "update_paid_on":
                settlement_date, settlement_note = settlement_from_request(request)
                set_advance_paid_on(
                    advance,
                    paid_on=settlement_date,
                    recovery_note=settlement_note or None,
                )
                messages.success(request, "Advance paid-on date updated.")
                return _manual_settlement_redirect(request, "collection-point-advance-list")
            if recovered:
                settlement_date, settlement_note = settlement_from_request(request)
                set_advance_recovered(
                    advance,
                    recovered=True,
                    recovered_at=settlement_datetime_from_date(settlement_date),
                    recovery_note=settlement_note,
                )
            else:
                set_advance_recovered(advance, recovered=False)
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
            return _manual_settlement_redirect(request, "collection-point-advance-list")
        if recovered:
            messages.success(
                request,
                "Advance marked as recovered. Use this when the point paid outside the payment sheet.",
            )
        else:
            messages.success(request, "Advance marked as pending again.")
        return _manual_settlement_redirect(request, "collection-point-advance-list")


class BuyerSummaryView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = 'reports/buyer_summary.html'
    permission_required = "dispatch.view_milkdistribution"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        buyer_id = self.request.GET.get('buyer')
        branch_ids = self.request.GET.getlist("branches")
        include_all = self.request.GET.get("all_branches") == "1"
        allowed_qs = get_allowed_branches_qs(self.request.user)
        allowed_ids = list(allowed_qs.values_list("id", flat=True))
        default_branch_id = get_default_branch_id(self.request.user)
        if not include_all and not branch_ids and default_branch_id:
            branch_ids = [str(default_branch_id)]
        filters = {'buyer_id': buyer_id} if buyer_id else {}
        if include_all:
            if not self.request.user.is_superuser:
                filters["branch_id__in"] = allowed_ids
        elif branch_ids:
            safe_ids = [int(b) for b in branch_ids if str(b).isdigit()]
            if self.request.user.is_superuser:
                filters["branch_id__in"] = safe_ids
            else:
                filters["branch_id__in"] = [b for b in safe_ids if b in allowed_ids]
        elif not self.request.user.is_superuser:
            filters["branch_id__in"] = allowed_ids
        ctx['rows'] = get_buyer_summary(filters)
        ctx['buyers'] = filter_m2m_by_user_branches(Buyer.objects.all(), self.request.user).order_by("name")
        ctx["branches"] = Branch.objects.filter(id__in=allowed_ids) if not self.request.user.is_superuser else Branch.objects.all()
        ctx["selected_branches"] = [str(b) for b in branch_ids]
        return ctx

class ExportCollectionCSVView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "collections.view_milkcollection"

    def get(self, request):
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename=collection_{datetime.now().date()}.csv'
        writer = csv.writer(response)
        writer.writerow(['Date', 'Route', 'Point / farmer', 'Total Kg', 'Total Liters'])
        for row in get_collection_summary():
            writer.writerow([row['date'], row['route__name'], row['place_name'], row['total_kg'], row['total_liters']])
        return response

class ExportCollectionExcelView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "collections.view_milkcollection"

    def get(self, request):
        pd = get_pandas()
        data = list(get_collection_summary())
        df = pd.DataFrame(data)
        response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        response['Content-Disposition'] = f'attachment; filename=collection_{datetime.now().date()}.xlsx'
        with pd.ExcelWriter(response, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='CollectionSummary')
        return response

class ExportCollectionPDFView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "collections.view_milkcollection"

    def get(self, request):
        buffer = BytesIO()
        p = canvas.Canvas(buffer, pagesize=A4)
        p.drawString(40, 800, 'Milk Collection Summary')
        y = 770
        for row in get_collection_summary()[:35]:
            p.drawString(40, y, f"{row['date']} | {row['route__name']} | {row['place_name']} | Kg:{row['total_kg']} | L:{row['total_liters']}")
            y -= 20
        p.showPage()
        p.save()
        buffer.seek(0)
        return HttpResponse(buffer, content_type='application/pdf')

class ImportCollectionView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "collections.add_milkcollection"

    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            return redirect('summary')
        pd = get_pandas()
        if file.name.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
        for _, row in df.iterrows():
            src = str(row.get('source', 'point')).strip().lower()
            if src == 'farmer':
                farmer_id = int(row['farmer_id'])
                MilkCollection.objects.update_or_create(
                    date=row['date'],
                    route_id=int(row['route_id']),
                    farmer_id=farmer_id,
                    defaults={
                        'source': CollectionSource.FARMER,
                        'collection_point': None,
                        'kg': row['kg'],
                        'created_by': request.user,
                    },
                )
            else:
                MilkCollection.objects.update_or_create(
                    date=row['date'],
                    route_id=int(row['route_id']),
                    collection_point_id=int(row['collection_point_id']),
                    defaults={
                        'source': CollectionSource.POINT,
                        'farmer': None,
                        'kg': row['kg'],
                        'created_by': request.user,
                    },
                )
        return redirect('milk-collection-list')

class ImportDistributionView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "dispatch.add_milkdistribution"

    def post(self, request):
        file = request.FILES.get('file')
        if not file:
            return redirect('distribution-list')
        pd = get_pandas()
        if file.name.endswith('.csv'):
            df = pd.read_csv(file)
        else:
            df = pd.read_excel(file)
        status_ok = {c[0] for c in MilkDistribution.DistributionStatus.choices}
        pay_ok = {c[0] for c in MilkDistribution.DistributionPaymentStatus.choices}
        for _, row in df.iterrows():
            fat = _import_decimal_cell(row, 'fat', 0)
            snf = _import_decimal_cell(row, 'snf', 0)
            lr = _import_decimal_cell(row, 'lr', 0)
            alcohol = _import_decimal_cell(row, 'alcohol', 0)
            acidity = _import_decimal_cell(row, 'acidity', 0)
            kq = _import_decimal_cell(row, 'kq', 0)
            MilkDistribution.objects.create(
                date=row['date'],
                buyer_id=int(row['buyer_id']),
                dispatch_no=row['dispatch_no'],
                browser_number=_import_str_cell(row, 'browser_number', 'lorry_no'),
                driver=_import_str_cell(row, 'driver'),
                temperature=_import_decimal_cell(row, 'temperature', None),
                kq=kq if kq is not None else 0,
                sealing_numbers=_import_str_cell(row, 'sealing_numbers'),
                in_time=_import_time_cell(row, 'in_time'),
                out_time=_import_time_cell(row, 'out_time'),
                remarks=_import_str_cell(row, 'remarks'),
                status=_import_choice_cell(
                    row,
                    'status',
                    status_ok,
                    MilkDistribution.DistributionStatus.PENDING,
                ),
                buyer_result_quantity=_import_decimal_cell(row, 'buyer_result_quantity', None),
                payment_status=_import_choice_cell(
                    row,
                    'payment_status',
                    pay_ok,
                    MilkDistribution.DistributionPaymentStatus.PENDING,
                ),
                kg=row['kg'],
                fat=fat if fat is not None else 0,
                snf=snf if snf is not None else 0,
                lr=lr if lr is not None else 0,
                alcohol=alcohol if alcohol is not None else 0,
                acidity=acidity if acidity is not None else 0,
            )
        return redirect('distribution-list')


@login_required
@permission_required("collections.view_milkfactor", raise_exception=True)
def overall_dashboard(request):
    allowed_qs = get_allowed_branches_qs(request.user)
    if request.user.is_superuser:
        branches_qs = Branch.objects.all()
        branch_ids = list(branches_qs.values_list("id", flat=True))
    else:
        branches_qs = allowed_qs
        branch_ids = list(allowed_qs.values_list("id", flat=True))

    stock_qs = BranchMilkStock.objects.select_related("branch")
    if request.user.is_superuser:
        stock_qs = stock_qs.all()
    else:
        stock_qs = stock_qs.filter(branch_id__in=branch_ids)

    milk_factor_qs = MilkFactor.objects.select_related("route", "branch")
    if not request.user.is_superuser:
        milk_factor_qs = milk_factor_qs.filter(branch_id__in=branch_ids)

    route_factor_rows = (
        milk_factor_qs.values("route__code", "route__name")
        .annotate(
            avg_fat=Avg("fat"),
            avg_snf=Avg("snf"),
            avg_lr=Avg("lr"),
            avg_alcohol=Avg("alcohol"),
            avg_acidity=Avg("acidity"),
            avg_kq=Avg("kq"),
        )
        .order_by("route__code")
    )
    branch_factor_rows = (
        milk_factor_qs.values("branch__code", "branch__name")
        .annotate(
            avg_fat=Avg("fat"),
            avg_snf=Avg("snf"),
            avg_lr=Avg("lr"),
            avg_alcohol=Avg("alcohol"),
            avg_acidity=Avg("acidity"),
            avg_kq=Avg("kq"),
        )
        .order_by("branch__code")
    )
    total_factor = milk_factor_qs.aggregate(
        avg_fat=Avg("fat"),
        avg_snf=Avg("snf"),
        avg_lr=Avg("lr"),
        avg_alcohol=Avg("alcohol"),
        avg_acidity=Avg("acidity"),
        avg_kq=Avg("kq"),
    )
    stock_rows = list(stock_qs.order_by("branch__code"))
    total_stock = {
        "collected": Decimal("0.00"),
        "dispatched": Decimal("0.00"),
        "current": Decimal("0.00"),
    }
    for row in stock_rows:
        snap = branch_stock_snapshot(row.branch)
        row.collected_kg = snap["collected_kg"]
        row.dispatched_kg = snap["dispatched_kg"]
        row.current_kg = snap["current_kg"]
        total_stock["collected"] += snap["collected_kg"]
        total_stock["dispatched"] += snap["dispatched_kg"]
        total_stock["current"] += snap["current_kg"]

    return render(
        request,
        "reports/overall_dashboard.html",
        {
            "stock_rows": stock_rows,
            "total_stock": total_stock,
            "route_factor_rows": route_factor_rows,
            "branch_factor_rows": branch_factor_rows,
            "total_factor": total_factor,
            "branches": branches_qs,
        },
    )
