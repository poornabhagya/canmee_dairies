from decimal import Decimal
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Spacer, Table, TableStyle

from branches.utils import filter_by_user_branches, get_allowed_branches_qs, get_default_branch_id
from canmee_dairies.mixins import RedirectGetDeleteMixin
from masters.models import CollectionPoint
from reports.manual_settlement import (
    mark_collection_point_loan_instalment_paid,
    mark_collection_point_loan_instalment_pending,
    settlement_from_request,
)
from .payment_ledger import record_collection_point_loan_payment

from .audit import (
    loan_audit_diff,
    loan_audit_snapshot,
    log_collection_point_loan_action,
    log_collection_point_loan_deleted,
)
from .forms import (
    CollectionPointLoanForm,
    CollectionPointLoanSchedulePaidFormSet,
    compute_point_milk_stats,
)
from .models import CollectionPointLoan, CollectionPointLoanActionLog, CollectionPointLoanRepaymentSchedule
from .register_pdf import build_collection_point_loan_register_pdf


def _loan_point_row(point):
    branch_id = point.route.branch_id if point.route_id else None
    stats = compute_point_milk_stats(point)
    return {
        "id": point.pk,
        "branch_id": branch_id,
        "label": f"{point.number} - {point.name} ({point.route.name})",
        "point_reference": (point.number or "").strip(),
        "auto_production": str(stats["average_monthly_milk_production"]) if stats else "",
        "auto_income": str(stats["average_monthly_milk_income"]) if stats else "",
        "metrics_locked": bool(stats),
    }


def _redistribute_schedule_overpayments(schedules, requested_paid_amounts):
    """Cap each instalment at its due amount and spill excess backward, then forward."""
    schedule_list = list(schedules)
    amount_by_id = {
        row.id: (row.installment_amount or Decimal("0.00")).quantize(Decimal("0.01"))
        for row in schedule_list
    }
    assigned = {
        row.id: min(
            (requested_paid_amounts.get(row.id) or Decimal("0.00")).quantize(Decimal("0.01")),
            amount_by_id[row.id],
        )
        for row in schedule_list
    }
    overflow_by_id = {
        row.id: max(
            Decimal("0.00"),
            (requested_paid_amounts.get(row.id) or Decimal("0.00")).quantize(Decimal("0.01"))
            - amount_by_id[row.id],
        ).quantize(Decimal("0.01"))
        for row in schedule_list
    }

    for index, row in enumerate(schedule_list):
        overflow = overflow_by_id[row.id]
        if overflow <= Decimal("0.00"):
            continue

        for prev_row in reversed(schedule_list[:index]):
            space = (amount_by_id[prev_row.id] - assigned[prev_row.id]).quantize(Decimal("0.01"))
            if space <= Decimal("0.00"):
                continue
            moved = min(space, overflow)
            assigned[prev_row.id] = (assigned[prev_row.id] + moved).quantize(Decimal("0.01"))
            overflow = (overflow - moved).quantize(Decimal("0.01"))
            if overflow <= Decimal("0.00"):
                break

        if overflow > Decimal("0.00"):
            for next_row in schedule_list[index + 1 :]:
                space = (amount_by_id[next_row.id] - assigned[next_row.id]).quantize(Decimal("0.01"))
                if space <= Decimal("0.00"):
                    continue
                moved = min(space, overflow)
                assigned[next_row.id] = (assigned[next_row.id] + moved).quantize(Decimal("0.01"))
                overflow = (overflow - moved).quantize(Decimal("0.01"))
                if overflow <= Decimal("0.00"):
                    break

    return assigned


class CollectionPointLoanRegisterQueryMixin:
    model = CollectionPointLoan

    def _selected_branch_ids(self):
        branch_ids_raw = self.request.GET.getlist("branches")
        if not branch_ids_raw:
            default_branch_id = get_default_branch_id(self.request.user)
            if default_branch_id:
                branch_ids_raw = [str(default_branch_id)]
        if not branch_ids_raw:
            return None
        return [int(b) for b in branch_ids_raw if str(b).isdigit()]

    def _branch_filtered_queryset(self):
        qs = self.model.objects.select_related("branch", "collection_point", "collection_point__route")
        qs = filter_by_user_branches(qs, self.request.user, "branch_id")
        branch_ids = self._selected_branch_ids()
        if branch_ids:
            qs = qs.filter(branch_id__in=branch_ids)
        return qs

    def _with_payment_totals(self, qs):
        return qs.annotate(
            paid_total=Coalesce(
                Sum("repayment_schedules__paid_amount"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            ),
        ).annotate(balance_total=F("loan_amount") - F("paid_total"))

    def get_register_queryset(self):
        qs = self._with_payment_totals(self._branch_filtered_queryset())
        status = (self.request.GET.get("status") or "").strip()
        valid_statuses = {value for value, _ in CollectionPointLoan.Status.choices}
        if status == "settled":
            qs = qs.filter(
                status=CollectionPointLoan.Status.APPROVED,
                balance_total__lte=Decimal("0.00"),
            )
        elif status in valid_statuses:
            qs = qs.filter(status=status)
        return qs.order_by("-loan_date", "-id")

    def get_register_labels(self):
        status = (self.request.GET.get("status") or "").strip()
        if status == "settled":
            status_label = "Settled"
        else:
            status_label = dict(CollectionPointLoan.Status.choices).get(status, "All")
        allowed_branches = get_allowed_branches_qs(self.request.user).order_by("code", "name")
        selected_branches = self.request.GET.getlist("branches")
        if not selected_branches:
            default_branch_id = get_default_branch_id(self.request.user)
            if default_branch_id:
                selected_branches = [str(default_branch_id)]
        branch_names = [
            f"{branch.code} — {branch.name}"
            for branch in allowed_branches
            if str(branch.pk) in {str(value) for value in selected_branches}
        ]
        return status_label, branch_names

    def get_register_totals(self, qs):
        totals = qs.aggregate(
            total_loan=Coalesce(Sum("loan_amount"), Value(Decimal("0.00"))),
            total_paid=Coalesce(Sum("paid_total"), Value(Decimal("0.00"))),
        )
        total_loan = (totals["total_loan"] or Decimal("0.00")).quantize(Decimal("0.01"))
        total_paid = (totals["total_paid"] or Decimal("0.00")).quantize(Decimal("0.01"))
        return {
            "count": qs.count(),
            "loan_amount": total_loan,
            "paid": total_paid,
            "balance": (total_loan - total_paid).quantize(Decimal("0.01")),
        }


class CollectionPointLoanListView(
    LoginRequiredMixin, PermissionRequiredMixin, CollectionPointLoanRegisterQueryMixin, ListView
):
    template_name = "collection_point_loans/loan_list.html"
    permission_required = "collection_point_loans.view_collectionpointloan"

    def get_queryset(self):
        return self.get_register_queryset()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        branch_qs = self._branch_filtered_queryset()
        annotated_branch_qs = self._with_payment_totals(branch_qs)
        status_counts = {"all": branch_qs.count()}
        for value, _label in CollectionPointLoan.Status.choices:
            status_counts[value] = branch_qs.filter(status=value).count()
        status_counts["settled"] = annotated_branch_qs.filter(
            status=CollectionPointLoan.Status.APPROVED,
            balance_total__lte=Decimal("0.00"),
        ).count()
        ctx["status_counts"] = status_counts
        ctx["status_choices"] = CollectionPointLoan.Status.choices
        ctx["selected_status"] = (self.request.GET.get("status") or "").strip()
        allowed_branches = get_allowed_branches_qs(self.request.user).order_by("code", "name")
        ctx["branches"] = allowed_branches
        selected_branches = self.request.GET.getlist("branches")
        if not selected_branches:
            default_branch_id = get_default_branch_id(self.request.user)
            if default_branch_id:
                selected_branches = [str(default_branch_id)]
        ctx["selected_branches"] = selected_branches
        status_label, branch_names = self.get_register_labels()
        ctx["selected_status_label"] = status_label
        ctx["selected_branch_names"] = branch_names
        ctx["loan_totals"] = self.get_register_totals(ctx["object_list"])
        return ctx


class CollectionPointLoanRegisterPDFView(
    LoginRequiredMixin, PermissionRequiredMixin, CollectionPointLoanRegisterQueryMixin, View
):
    permission_required = "collection_point_loans.view_collectionpointloan"

    def get(self, request):
        qs = self.get_register_queryset()
        totals = self.get_register_totals(qs)
        status_label, branch_names = self.get_register_labels()
        buffer = BytesIO()
        build_collection_point_loan_register_pdf(
            buffer,
            loans=list(qs),
            totals=totals,
            status_label=status_label,
            branch_names=branch_names,
        )
        buffer.seek(0)
        response = HttpResponse(buffer, content_type="application/pdf")
        response["Content-Disposition"] = 'inline; filename="collection_point_loan_register.pdf"'
        return response


class CollectionPointLoanCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    template_name = "collection_point_loans/loan_form.html"
    model = CollectionPointLoan
    form_class = CollectionPointLoanForm
    permission_required = "collection_point_loans.add_collectionpointloan"
    success_url = reverse_lazy("cp-loan-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.status = CollectionPointLoan.Status.DRAFT
        response = super().form_valid(form)
        log_collection_point_loan_action(
            self.object,
            user=self.request.user,
            action=CollectionPointLoanActionLog.Action.CREATED,
            details={"snapshot": loan_audit_snapshot(self.object)},
        )
        messages.success(self.request, "Loan created as draft.")
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        point_qs = filter_by_user_branches(
            CollectionPoint.objects.select_related("route", "route__branch").order_by(
                "route__branch__code", "route__code", "number"
            ),
            self.request.user,
            "route__branch_id",
        )
        ctx["loan_point_rows"] = [_loan_point_row(point) for point in point_qs]
        return ctx


class CollectionPointLoanUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    template_name = "collection_point_loans/loan_form.html"
    model = CollectionPointLoan
    form_class = CollectionPointLoanForm
    permission_required = "collection_point_loans.change_collectionpointloan"
    success_url = reverse_lazy("cp-loan-list")

    def get_queryset(self):
        return filter_by_user_branches(
            self.model.objects.select_related("branch", "collection_point", "collection_point__route"),
            self.request.user,
            "branch_id",
        )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def dispatch(self, request, *args, **kwargs):
        obj = self.get_object()
        if not obj.can_edit():
            messages.error(request, "Only draft or pending-approval loans can be edited.")
            return redirect("cp-loan-detail", pk=obj.pk)
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        before = loan_audit_snapshot(self.object)
        was_pending = self.object.status == CollectionPointLoan.Status.PENDING_APPROVAL
        response = super().form_valid(form)
        if was_pending:
            self.object.repayment_schedules.all().delete()
            self.object.status = CollectionPointLoan.Status.DRAFT
            self.object.submitted_at = None
            self.object.save(update_fields=["status", "submitted_at", "updated_at"])
            log_collection_point_loan_action(
                self.object,
                user=self.request.user,
                action=CollectionPointLoanActionLog.Action.REVERTED_TO_DRAFT,
                notes="Edited while pending approval. Loan returned to draft; submit again to regenerate schedule.",
            )
        after = loan_audit_snapshot(self.object)
        changes = loan_audit_diff(before, after)
        log_collection_point_loan_action(
            self.object,
            user=self.request.user,
            action=CollectionPointLoanActionLog.Action.UPDATED,
            details={"changes": changes},
        )
        messages.success(
            self.request,
            "Loan updated." if not was_pending else "Loan updated and reverted to draft for resubmission.",
        )
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        point_qs = filter_by_user_branches(
            CollectionPoint.objects.select_related("route", "route__branch").order_by(
                "route__branch__code", "route__code", "number"
            ),
            self.request.user,
            "route__branch_id",
        )
        ctx["loan_point_rows"] = [_loan_point_row(point) for point in point_qs]
        return ctx


class CollectionPointLoanDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    template_name = "collection_point_loans/loan_detail.html"
    model = CollectionPointLoan
    context_object_name = "loan"
    permission_required = "collection_point_loans.view_collectionpointloan"

    def get_queryset(self):
        qs = self.model.objects.select_related(
            "branch", "collection_point", "collection_point__route", "approved_by", "rejected_by", "created_by"
        ).prefetch_related(
            "repayment_schedules",
            "action_logs__performed_by",
            "payment_transactions__allocations__schedule",
            "payment_transactions__created_by",
        )
        return filter_by_user_branches(qs, self.request.user, "branch_id")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        loan = self.object
        ctx["account_summary"] = _loan_account_summary(loan)
        ctx["loan_balance"] = ctx["account_summary"]["balance_total"]
        can_update_schedule = (
            loan.status == CollectionPointLoan.Status.APPROVED
            and self.request.user.has_perm("collection_point_loans.change_collectionpointloan")
            and loan.repayment_schedules.exists()
        )
        ctx["can_update_schedule_paid"] = can_update_schedule
        if can_update_schedule:
            ctx["schedule_paid_formset"] = CollectionPointLoanSchedulePaidFormSet(
                queryset=loan.repayment_schedules.order_by("installment_number"),
            )
        return ctx


def _loan_account_summary(loan):
    schedules = list(loan.repayment_schedules.all())
    paid_total = sum(
        ((row.paid_amount or Decimal("0.00")) for row in schedules),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    loan_amount = (loan.loan_amount or Decimal("0.00")).quantize(Decimal("0.01"))
    paid_instalments = sum(1 for row in schedules if row.payment_status_label == "Paid")
    partial_instalments = sum(1 for row in schedules if row.payment_status_label == "Partial")
    open_instalments = sum(1 for row in schedules if row.payment_status_label != "Paid")
    next_due = next((row.due_date for row in schedules if row.payment_status_label != "Paid"), None)
    return {
        "loan_amount": loan_amount,
        "paid_total": paid_total,
        "balance_total": (loan_amount - paid_total).quantize(Decimal("0.01")),
        "schedule_count": len(schedules),
        "paid_instalments": paid_instalments,
        "partial_instalments": partial_instalments,
        "open_instalments": open_instalments,
        "next_due_date": next_due,
    }


class CollectionPointLoanAccountMixin(LoginRequiredMixin, PermissionRequiredMixin):
    permission_required = "collection_point_loans.view_collectionpointloan"

    def get_loan(self):
        qs = CollectionPointLoan.objects.select_related(
            "branch", "collection_point", "collection_point__route"
        ).prefetch_related(
            "repayment_schedules",
            "payment_transactions__allocations__schedule",
            "payment_transactions__created_by",
        )
        return get_object_or_404(
            filter_by_user_branches(qs, self.request.user, "branch_id"),
            pk=self.kwargs["pk"],
        )


class CollectionPointLoanAccountView(CollectionPointLoanAccountMixin, DetailView):
    template_name = "collection_point_loans/loan_account.html"
    context_object_name = "loan"

    def get_object(self, queryset=None):
        return self.get_loan()

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["account_summary"] = _loan_account_summary(self.object)
        return ctx


def _account_export_section(request):
    section = (request.GET.get("section") or "all").strip().lower()
    if section not in {"all", "payments", "schedule"}:
        section = "all"
    return section


def _account_identity_rows(loan, summary):
    return [
        ["Collection point loan account"],
        ["Loan #", loan.pk],
        ["Collection point", str(loan.collection_point)],
        ["Branch", str(loan.branch.name)],
        ["Loan date", str(loan.loan_date)],
        ["First repayment date", str(loan.first_repayment_date)],
        ["Loan amount", float(summary["loan_amount"])],
        ["Paid total", float(summary["paid_total"])],
        ["Balance", float(summary["balance_total"])],
    ]


def _account_payment_rows(loan):
    rows = [["Date", "Method", "Amount", "Allocations", "Note", "By"]]
    for entry in loan.payment_transactions.all():
        allocations = ", ".join(
            f"Inst. {alloc.schedule.installment_number}: {alloc.amount}"
            for alloc in entry.allocations.all()
        )
        rows.append(
            [
                entry.payment_date.isoformat(),
                entry.get_method_display(),
                float(entry.amount or 0),
                allocations,
                entry.note or "",
                str(entry.created_by or "System"),
            ]
        )
    return rows


def _account_schedule_rows(loan):
    rows = [["Instalment", "Due date", "Amount", "Paid", "Balance", "Paid date", "Method", "Status"]]
    for row in loan.repayment_schedules.all().order_by("installment_number"):
        rows.append(
            [
                row.installment_number,
                row.due_date.isoformat(),
                float(row.installment_amount or 0),
                float(row.paid_amount or 0),
                float(row.remaining_amount or 0),
                row.paid_on.isoformat() if row.paid_on else "",
                row.get_payment_method_display() if row.payment_method else "",
                row.payment_status_label,
            ]
        )
    return rows


class CollectionPointLoanAccountExcelView(CollectionPointLoanAccountMixin, View):
    def get(self, request, pk):
        loan = self.get_loan()
        summary = _loan_account_summary(loan)
        section = _account_export_section(request)
        wb = Workbook()
        ws = wb.active
        ws.title = {"payments": "Payments", "schedule": "Schedule"}.get(section, "Loan Account")

        rows = _account_identity_rows(loan, summary)
        if section in {"all", "payments"}:
            rows += [[], ["Payment history"]] + _account_payment_rows(loan)
        if section in {"all", "schedule"}:
            rows += [[], ["Repayment schedule"]] + _account_schedule_rows(loan)
        for item in rows:
            ws.append(item)

        suffix = "" if section == "all" else f"_{section}"
        response = HttpResponse(
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        response["Content-Disposition"] = (
            f'attachment; filename="collection_point_loan_account_{loan.pk}{suffix}.xlsx"'
        )
        wb.save(response)
        return response


class CollectionPointLoanAccountPDFView(CollectionPointLoanAccountMixin, View):
    def get(self, request, pk):
        loan = self.get_loan()
        summary = _loan_account_summary(loan)
        section = _account_export_section(request)
        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=10 * mm, rightMargin=10 * mm)
        elements = []

        summary_table = Table(
            [
                ["Collection point loan account", f"Loan #{loan.pk}"],
                ["Collection point", str(loan.collection_point)],
                ["Branch", str(loan.branch.name)],
                ["Loan amount", f"{summary['loan_amount']:.2f}"],
                ["Paid total", f"{summary['paid_total']:.2f}"],
                ["Balance", f"{summary['balance_total']:.2f}"],
            ],
            colWidths=[55 * mm, 115 * mm],
        )
        summary_table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ]
            )
        )
        elements.append(summary_table)
        elements.append(Spacer(1, 6 * mm))

        if section in {"all", "payments"}:
            payment_rows = [["Date", "Method", "Amount", "Allocations"]]
            for entry in loan.payment_transactions.all():
                payment_rows.append(
                    [
                        entry.payment_date.isoformat(),
                        entry.get_method_display(),
                        f"{(entry.amount or Decimal('0.00')):.2f}",
                        ", ".join(
                            f"I{alloc.schedule.installment_number}: {alloc.amount}"
                            for alloc in entry.allocations.all()
                        )
                        or "—",
                    ]
                )
            payments_table = Table(
                payment_rows, repeatRows=1, colWidths=[25 * mm, 40 * mm, 25 * mm, 90 * mm]
            )
            payments_table.setStyle(
                TableStyle(
                    [
                        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("ALIGN", (2, 1), (2, -1), "RIGHT"),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ]
                )
            )
            elements.append(payments_table)
            if section == "all":
                elements.append(Spacer(1, 6 * mm))

        if section in {"all", "schedule"}:
            schedule_rows = [["Inst.", "Due date", "Amount", "Paid", "Balance", "Paid date", "Status"]]
            for row in loan.repayment_schedules.all().order_by("installment_number"):
                schedule_rows.append(
                    [
                        row.installment_number,
                        row.due_date.isoformat(),
                        f"{(row.installment_amount or Decimal('0.00')):.2f}",
                        f"{(row.paid_amount or Decimal('0.00')):.2f}",
                        f"{(row.remaining_amount or Decimal('0.00')):.2f}",
                        row.paid_on.isoformat() if row.paid_on else "—",
                        row.payment_status_label,
                    ]
                )
            schedule_table = Table(
                schedule_rows,
                repeatRows=1,
                colWidths=[12 * mm, 28 * mm, 24 * mm, 24 * mm, 24 * mm, 28 * mm, 30 * mm],
            )
            schedule_table.setStyle(
                TableStyle(
                    [
                        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                        ("ALIGN", (2, 1), (4, -1), "RIGHT"),
                    ]
                )
            )
            elements.append(schedule_table)

        doc.build(elements)
        buffer.seek(0)
        suffix = "" if section == "all" else f"_{section}"
        response = HttpResponse(buffer, content_type="application/pdf")
        response["Content-Disposition"] = (
            f'attachment; filename="collection_point_loan_account_{loan.pk}{suffix}.pdf"'
        )
        return response


class CollectionPointLoanSchedulePaidUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "collection_point_loans.change_collectionpointloan"

    def post(self, request, pk):
        loan = get_object_or_404(
            filter_by_user_branches(
                CollectionPointLoan.objects.prefetch_related("repayment_schedules"),
                request.user,
                "branch_id",
            ),
            pk=pk,
        )
        if loan.status != CollectionPointLoan.Status.APPROVED:
            messages.error(request, "Paid instalments can only be updated on approved loans.")
            return redirect("cp-loan-detail", pk=loan.pk)
        formset = CollectionPointLoanSchedulePaidFormSet(
            request.POST,
            queryset=loan.repayment_schedules.order_by("installment_number"),
        )
        if not formset.is_valid():
            messages.error(request, "Could not save paid instalments. Check the amounts and try again.")
            return redirect("cp-loan-detail", pk=loan.pk)
        before = [
            {
                "installment": row.installment_number,
                "paid_amount": str(row.paid_amount),
                "is_paid": row.is_paid,
            }
            for row in loan.repayment_schedules.order_by("installment_number")
        ]
        with transaction.atomic():
            schedules = list(loan.repayment_schedules.order_by("installment_number"))
            before_paid_amounts = {
                row.id: (row.paid_amount or Decimal("0.00")).quantize(Decimal("0.01"))
                for row in schedules
            }
            requested_paid_amounts = {
                form.instance.id: (
                    form.cleaned_data.get("paid_amount") or Decimal("0.00")
                ).quantize(Decimal("0.01"))
                for form in formset.forms
                if form.cleaned_data and not form.cleaned_data.get("DELETE")
            }
            redistributed = _redistribute_schedule_overpayments(
                schedules, requested_paid_amounts
            )
            allocation_deltas = []
            for schedule in schedules:
                prior_paid = (schedule.paid_amount or Decimal("0.00")).quantize(Decimal("0.01"))
                schedule.paid_amount = redistributed.get(schedule.id, Decimal("0.00"))
                delta = (schedule.paid_amount - before_paid_amounts[schedule.id]).quantize(
                    Decimal("0.01")
                )
                schedule.sync_paid_status()
                if schedule.is_paid and prior_paid < (schedule.installment_amount or Decimal("0.00")):
                    schedule.paid_on = timezone.localdate()
                    schedule.payment_method = schedule.PaymentMethod.DIRECT
                elif not schedule.is_paid:
                    schedule.payment_method = ""
                    schedule.payment_note = ""
                if delta != Decimal("0.00"):
                    allocation_deltas.append((schedule, delta))
                schedule.save(
                    update_fields=[
                        "paid_amount",
                        "is_paid",
                        "paid_on",
                        "payment_method",
                        "payment_note",
                    ]
                )
            record_collection_point_loan_payment(
                loan=loan,
                allocations=allocation_deltas,
                payment_date=timezone.localdate(),
                method=CollectionPointLoanRepaymentSchedule.PaymentMethod.DIRECT,
                note="Schedule paid amounts updated from loan detail.",
                created_by=request.user,
            )
            # Avoid stale prefetch — re-read paid amounts from the database.
            total_paid = sum(
                (amt or Decimal("0"))
                for amt in loan.repayment_schedules.values_list("paid_amount", flat=True)
            )
            loan.prior_paid_amount = total_paid.quantize(Decimal("0.01"))
            loan.save(update_fields=["prior_paid_amount", "updated_at"])
        after = [
            {
                "installment": row.installment_number,
                "paid_amount": str(row.paid_amount),
                "is_paid": row.is_paid,
            }
            for row in loan.repayment_schedules.order_by("installment_number")
        ]
        log_collection_point_loan_action(
            loan,
            user=request.user,
            action=CollectionPointLoanActionLog.Action.SCHEDULE_PAID_UPDATED,
            details={"before": before, "after": after},
        )
        messages.success(request, "Repayment schedule paid amounts updated.")
        return redirect("cp-loan-detail", pk=loan.pk)


class CollectionPointLoanInstalmentManualSettleView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "reports.record_manual_settlement"

    def post(self, request, pk):
        schedule = get_object_or_404(
            CollectionPointLoanRepaymentSchedule.objects.select_related("loan", "loan__collection_point"),
            pk=pk,
        )
        loan = get_object_or_404(
            filter_by_user_branches(
                CollectionPointLoan.objects.filter(pk=schedule.loan_id),
                request.user,
                "branch_id",
            ),
        )
        settle = (request.POST.get("settle") or "").strip().lower() in {"1", "true", "yes", "on"}
        try:
            if settle:
                settlement_date, settlement_note = settlement_from_request(request)
                before = str(schedule.paid_amount)
                mark_collection_point_loan_instalment_paid(
                    schedule,
                    paid_on=settlement_date,
                    payment_note=settlement_note,
                    created_by=request.user,
                )
                schedule.refresh_from_db()
                log_collection_point_loan_action(
                    loan,
                    user=request.user,
                    action=CollectionPointLoanActionLog.Action.SCHEDULE_PAID_UPDATED,
                    details={
                        "manual_settlement": True,
                        "installment": schedule.installment_number,
                        "before": before,
                        "after": str(schedule.paid_amount),
                    },
                )
                messages.success(
                    request,
                    f"Instalment #{schedule.installment_number} marked as paid (manual settlement).",
                )
            else:
                before = str(schedule.paid_amount)
                mark_collection_point_loan_instalment_pending(schedule, created_by=request.user)
                schedule.refresh_from_db()
                log_collection_point_loan_action(
                    loan,
                    user=request.user,
                    action=CollectionPointLoanActionLog.Action.SCHEDULE_PAID_UPDATED,
                    details={
                        "manual_settlement": True,
                        "installment": schedule.installment_number,
                        "before": before,
                        "after": str(schedule.paid_amount),
                    },
                )
                messages.success(
                    request,
                    f"Instalment #{schedule.installment_number} marked as pending again.",
                )
        except ValidationError as exc:
            messages.error(request, exc.messages[0] if exc.messages else str(exc))
        next_url = (request.POST.get("next") or "").strip()
        if next_url.startswith("/") and not next_url.startswith("//"):
            return redirect(next_url)
        return redirect("cp-loan-detail", pk=loan.pk)


class CollectionPointLoanDeleteView(LoginRequiredMixin, PermissionRequiredMixin, RedirectGetDeleteMixin, DeleteView):
    model = CollectionPointLoan
    success_url = reverse_lazy("cp-loan-list")
    permission_required = "collection_point_loans.change_collectionpointloan"

    def get_queryset(self):
        return filter_by_user_branches(self.model.objects.select_related("branch"), self.request.user, "branch_id")

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        if not obj.user_can_delete(request.user):
            messages.error(request, "You do not have permission to delete this loan.")
            return redirect("cp-loan-detail", pk=obj.pk)
        notes = ""
        if obj.status == CollectionPointLoan.Status.APPROVED and request.user.is_superuser:
            notes = "Superuser deleted approved loan."
        log_collection_point_loan_deleted(obj, user=request.user, notes=notes)
        messages.success(request, "Loan deleted.")
        return super().post(request, *args, **kwargs)


class CollectionPointLoanSubmitView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "collection_point_loans.change_collectionpointloan"

    def post(self, request, pk):
        loan = get_object_or_404(
            filter_by_user_branches(
                CollectionPointLoan.objects.select_related("branch"),
                request.user,
                "branch_id",
            ),
            pk=pk,
        )
        if not loan.can_edit() or loan.status != CollectionPointLoan.Status.DRAFT:
            messages.error(request, "Only draft loans can be submitted.")
            return redirect("cp-loan-detail", pk=loan.pk)
        with transaction.atomic():
            loan.status = CollectionPointLoan.Status.PENDING_APPROVAL
            loan.submitted_at = timezone.now()
            loan.save(update_fields=["status", "submitted_at", "updated_at"])
            loan.generate_repayment_schedule()
        log_collection_point_loan_action(
            loan,
            user=request.user,
            action=CollectionPointLoanActionLog.Action.SUBMITTED,
            details={"installment_count": loan.installment_count},
        )
        messages.success(request, "Loan submitted for approval and repayment schedule generated.")
        return redirect("cp-loan-detail", pk=loan.pk)


class CollectionPointLoanApproveView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "collection_point_loans.change_collectionpointloan"

    def post(self, request, pk):
        loan = get_object_or_404(
            filter_by_user_branches(
                CollectionPointLoan.objects.select_related("branch"),
                request.user,
                "branch_id",
            ),
            pk=pk,
        )
        if not loan.can_decide():
            messages.error(request, "Only pending loans can be approved.")
            return redirect("cp-loan-detail", pk=loan.pk)
        loan.status = CollectionPointLoan.Status.APPROVED
        loan.approved_by = request.user
        loan.approved_at = timezone.now()
        loan.rejected_by = None
        loan.rejected_at = None
        loan.rejection_reason = ""
        loan.save(
            update_fields=[
                "status",
                "approved_by",
                "approved_at",
                "rejected_by",
                "rejected_at",
                "rejection_reason",
                "updated_at",
            ]
        )
        log_collection_point_loan_action(loan, user=request.user, action=CollectionPointLoanActionLog.Action.APPROVED)
        messages.success(request, "Loan approved successfully.")
        return redirect("cp-loan-detail", pk=loan.pk)


class CollectionPointLoanRejectView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "collection_point_loans.change_collectionpointloan"

    def post(self, request, pk):
        loan = get_object_or_404(
            filter_by_user_branches(
                CollectionPointLoan.objects.select_related("branch"),
                request.user,
                "branch_id",
            ),
            pk=pk,
        )
        if not loan.can_decide():
            messages.error(request, "Only pending loans can be rejected.")
            return redirect("cp-loan-detail", pk=loan.pk)
        reason = (request.POST.get("rejection_reason") or "").strip()
        loan.status = CollectionPointLoan.Status.REJECTED
        loan.rejected_by = request.user
        loan.rejected_at = timezone.now()
        loan.rejection_reason = reason
        loan.approved_by = None
        loan.approved_at = None
        loan.save(
            update_fields=[
                "status",
                "rejected_by",
                "rejected_at",
                "rejection_reason",
                "approved_by",
                "approved_at",
                "updated_at",
            ]
        )
        log_collection_point_loan_action(
            loan,
            user=request.user,
            action=CollectionPointLoanActionLog.Action.REJECTED,
            notes=reason,
        )
        messages.success(request, "Loan rejected.")
        return redirect("cp-loan-detail", pk=loan.pk)


class BranchCollectionPointLookupView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "collection_point_loans.add_collectionpointloan"

    def get(self, request):
        branch_id = request.GET.get("branch_id")
        if not branch_id or not str(branch_id).isdigit():
            return JsonResponse({"points": []})
        allowed_qs = filter_by_user_branches(
            CollectionPoint.objects.select_related("route", "route__branch"),
            request.user,
            "route__branch_id",
        )
        points = allowed_qs.filter(route__branch_id=int(branch_id)).order_by("route__code", "number")
        rows = [_loan_point_row(point) for point in points]
        return JsonResponse({"points": rows})
