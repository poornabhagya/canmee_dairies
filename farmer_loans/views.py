from django.contrib import messages

from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.core.exceptions import ValidationError

from django.db import transaction

from django.http import JsonResponse

from django.shortcuts import get_object_or_404, redirect

from django.urls import reverse_lazy

from django.utils import timezone

from django.views import View

from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView



from django.db.models import DecimalField, F, Sum, Value
from django.db.models.functions import Coalesce

from branches.utils import filter_by_user_branches, get_allowed_branches_qs, get_default_branch_id

from canmee_dairies.mixins import RedirectGetDeleteMixin

from masters.models import Farmer



from .audit import loan_audit_diff, loan_audit_snapshot, log_farmer_loan_action, log_farmer_loan_deleted
from reports.manual_settlement import (
    mark_loan_instalment_paid,
    mark_loan_instalment_pending,
    settlement_from_request,
)

from .forms import FarmerLoanForm, FarmerLoanSchedulePaidFormSet, compute_farmer_milk_stats

from .models import FarmerLoan, FarmerLoanActionLog, FarmerLoanRepaymentSchedule





def _loan_farmer_row(farmer):

    stats = compute_farmer_milk_stats(farmer)

    return {

        "id": farmer.pk,

        "branch_id": farmer.branch_id,

        "label": f"{farmer.registration_number or '-'} - {farmer.common_name or farmer.full_name}",

        "farm_registration_number": (farmer.registration_number or "").strip(),

        "auto_production": str(stats["average_monthly_milk_production"]) if stats else "",

        "auto_income": str(stats["average_monthly_milk_income"]) if stats else "",

        "metrics_locked": bool(stats),

    }





class FarmerLoanListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    template_name = "farmer_loans/loan_list.html"
    model = FarmerLoan
    permission_required = "farmer_loans.view_farmerloan"

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
        qs = self.model.objects.select_related("branch", "farmer")
        qs = filter_by_user_branches(qs, self.request.user, "branch_id")
        branch_ids = self._selected_branch_ids()
        if branch_ids:
            qs = qs.filter(branch_id__in=branch_ids)
        return qs

    def get_queryset(self):
        qs = self._branch_filtered_queryset()
        status = (self.request.GET.get("status") or "").strip()
        valid_statuses = {value for value, _ in FarmerLoan.Status.choices}
        if status in valid_statuses:
            qs = qs.filter(status=status)
        farmer_raw = (self.request.GET.get("farmer") or "").strip()
        if farmer_raw.isdigit():
            qs = qs.filter(farmer_id=int(farmer_raw))
        return (
            qs.annotate(
                paid_total=Coalesce(
                    Sum("repayment_schedules__paid_amount"),
                    Value(Decimal("0.00")),
                    output_field=DecimalField(max_digits=14, decimal_places=2),
                ),
            )
            .annotate(balance_total=F("loan_amount") - F("paid_total"))
            .order_by("-loan_date", "-id")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        branch_qs = self._branch_filtered_queryset()
        status_counts = {"all": branch_qs.count()}
        for value, label in FarmerLoan.Status.choices:
            status_counts[value] = branch_qs.filter(status=value).count()
        ctx["status_counts"] = status_counts
        ctx["status_choices"] = FarmerLoan.Status.choices
        ctx["selected_status"] = (self.request.GET.get("status") or "").strip()
        allowed_branches = get_allowed_branches_qs(self.request.user).order_by("code", "name")
        ctx["branches"] = allowed_branches
        selected_branches = self.request.GET.getlist("branches")
        if not selected_branches:
            default_branch_id = get_default_branch_id(self.request.user)
            if default_branch_id:
                selected_branches = [str(default_branch_id)]
        ctx["selected_branches"] = selected_branches
        qs = ctx["object_list"]
        totals = qs.aggregate(
            total_loan=Coalesce(Sum("loan_amount"), Value(Decimal("0.00"))),
            total_paid=Coalesce(Sum("paid_total"), Value(Decimal("0.00"))),
        )
        total_loan = (totals["total_loan"] or Decimal("0.00")).quantize(Decimal("0.01"))
        total_paid = (totals["total_paid"] or Decimal("0.00")).quantize(Decimal("0.01"))
        ctx["loan_totals"] = {
            "count": qs.count(),
            "loan_amount": total_loan,
            "paid": total_paid,
            "balance": (total_loan - total_paid).quantize(Decimal("0.01")),
        }
        return ctx


class FarmerLoanCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):

    template_name = "farmer_loans/loan_form.html"

    model = FarmerLoan

    form_class = FarmerLoanForm

    permission_required = "farmer_loans.add_farmerloan"

    success_url = reverse_lazy("farmer-loan-list")



    def get_form_kwargs(self):

        kwargs = super().get_form_kwargs()

        kwargs["user"] = self.request.user

        return kwargs



    def form_valid(self, form):

        form.instance.created_by = self.request.user

        form.instance.status = FarmerLoan.Status.DRAFT

        response = super().form_valid(form)

        log_farmer_loan_action(

            self.object,

            user=self.request.user,

            action=FarmerLoanActionLog.Action.CREATED,

            details={"snapshot": loan_audit_snapshot(self.object)},

        )

        messages.success(self.request, "Loan created as draft.")

        return response



    def get_context_data(self, **kwargs):

        ctx = super().get_context_data(**kwargs)

        farmer_qs = filter_by_user_branches(

            Farmer.objects.select_related("branch").order_by("common_name", "full_name"),

            self.request.user,

            "branch_id",

        )

        rows = [_loan_farmer_row(farmer) for farmer in farmer_qs]

        ctx["loan_farmer_rows"] = rows

        return ctx





class FarmerLoanUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):

    template_name = "farmer_loans/loan_form.html"

    model = FarmerLoan

    form_class = FarmerLoanForm

    permission_required = "farmer_loans.change_farmerloan"

    success_url = reverse_lazy("farmer-loan-list")



    def get_queryset(self):

        return filter_by_user_branches(self.model.objects.select_related("branch", "farmer"), self.request.user, "branch_id")



    def get_form_kwargs(self):

        kwargs = super().get_form_kwargs()

        kwargs["user"] = self.request.user

        return kwargs



    def dispatch(self, request, *args, **kwargs):

        obj = self.get_object()

        if not obj.can_edit():

            messages.error(request, "Only draft or pending-approval loans can be edited.")

            return redirect("farmer-loan-detail", pk=obj.pk)

        return super().dispatch(request, *args, **kwargs)



    def form_valid(self, form):

        before = loan_audit_snapshot(self.object)

        was_pending = self.object.status == FarmerLoan.Status.PENDING_APPROVAL

        response = super().form_valid(form)

        if was_pending:

            self.object.repayment_schedules.all().delete()

            self.object.status = FarmerLoan.Status.DRAFT

            self.object.submitted_at = None

            self.object.save(update_fields=["status", "submitted_at", "updated_at"])

            log_farmer_loan_action(

                self.object,

                user=self.request.user,

                action=FarmerLoanActionLog.Action.REVERTED_TO_DRAFT,

                notes="Edited while pending approval. Loan returned to draft; submit again to regenerate schedule.",

            )

        after = loan_audit_snapshot(self.object)

        changes = loan_audit_diff(before, after)

        log_farmer_loan_action(

            self.object,

            user=self.request.user,

            action=FarmerLoanActionLog.Action.UPDATED,

            details={"changes": changes},

        )

        messages.success(

            self.request,

            "Loan updated." if not was_pending else "Loan updated and reverted to draft for resubmission.",

        )

        return response



    def get_context_data(self, **kwargs):

        ctx = super().get_context_data(**kwargs)

        farmer_qs = filter_by_user_branches(

            Farmer.objects.select_related("branch").order_by("common_name", "full_name"),

            self.request.user,

            "branch_id",

        )

        rows = [_loan_farmer_row(farmer) for farmer in farmer_qs]

        ctx["loan_farmer_rows"] = rows

        return ctx





class FarmerLoanDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):

    template_name = "farmer_loans/loan_detail.html"

    model = FarmerLoan

    context_object_name = "loan"

    permission_required = "farmer_loans.view_farmerloan"



    def get_queryset(self):

        qs = (

            self.model.objects.select_related("branch", "farmer", "approved_by", "rejected_by", "created_by")

            .prefetch_related("repayment_schedules", "action_logs__performed_by")

        )

        return filter_by_user_branches(qs, self.request.user, "branch_id")

    def get_template_names(self):
        if self.request.GET.get("embed") == "1":
            return ["farmer_loans/loan_detail_embed.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        loan = self.object
        can_update_schedule = (
            loan.status == FarmerLoan.Status.APPROVED
            and self.request.user.has_perm("farmer_loans.change_farmerloan")
            and loan.repayment_schedules.exists()
        )
        ctx["can_update_schedule_paid"] = can_update_schedule
        if can_update_schedule:
            ctx["schedule_paid_formset"] = FarmerLoanSchedulePaidFormSet(
                queryset=loan.repayment_schedules.order_by("installment_number"),
            )
        return ctx


class FarmerLoanSchedulePaidUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "farmer_loans.change_farmerloan"

    def post(self, request, pk):
        loan = get_object_or_404(
            filter_by_user_branches(
                FarmerLoan.objects.prefetch_related("repayment_schedules"),
                request.user,
                "branch_id",
            ),
            pk=pk,
        )
        if loan.status != FarmerLoan.Status.APPROVED:
            messages.error(request, "Paid instalments can only be updated on approved loans.")
            return redirect("farmer-loan-detail", pk=loan.pk)
        formset = FarmerLoanSchedulePaidFormSet(
            request.POST,
            queryset=loan.repayment_schedules.order_by("installment_number"),
        )
        if not formset.is_valid():
            messages.error(request, "Could not save paid instalments. Check the amounts and try again.")
            return redirect("farmer-loan-detail", pk=loan.pk)
        before = [
            {
                "installment": row.installment_number,
                "paid_amount": str(row.paid_amount),
                "is_paid": row.is_paid,
            }
            for row in loan.repayment_schedules.order_by("installment_number")
        ]
        with transaction.atomic():
            formset.save()
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
        log_farmer_loan_action(
            loan,
            user=request.user,
            action=FarmerLoanActionLog.Action.SCHEDULE_PAID_UPDATED,
            details={"before": before, "after": after},
        )
        messages.success(request, "Repayment schedule paid amounts updated.")
        return redirect("farmer-loan-detail", pk=loan.pk)


class FarmerLoanInstalmentManualSettleView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "reports.record_manual_settlement"

    def post(self, request, pk):
        schedule = get_object_or_404(
            FarmerLoanRepaymentSchedule.objects.select_related("loan", "loan__farmer"),
            pk=pk,
        )
        loan = get_object_or_404(
            filter_by_user_branches(
                FarmerLoan.objects.filter(pk=schedule.loan_id),
                request.user,
                "branch_id",
            ),
        )
        settle = (request.POST.get("settle") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        try:
            if settle:
                settlement_date, settlement_note = settlement_from_request(request)
                before = str(schedule.paid_amount)
                mark_loan_instalment_paid(
                    schedule,
                    paid_on=settlement_date,
                    payment_note=settlement_note,
                )
                schedule.refresh_from_db()
                log_farmer_loan_action(
                    loan,
                    user=request.user,
                    action=FarmerLoanActionLog.Action.SCHEDULE_PAID_UPDATED,
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
                mark_loan_instalment_pending(schedule)
                schedule.refresh_from_db()
                log_farmer_loan_action(
                    loan,
                    user=request.user,
                    action=FarmerLoanActionLog.Action.SCHEDULE_PAID_UPDATED,
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
        return redirect("farmer-loan-detail", pk=loan.pk)


class FarmerLoanDeleteView(LoginRequiredMixin, PermissionRequiredMixin, RedirectGetDeleteMixin, DeleteView):

    model = FarmerLoan

    success_url = reverse_lazy("farmer-loan-list")

    permission_required = "farmer_loans.change_farmerloan"



    def get_queryset(self):

        return filter_by_user_branches(self.model.objects.select_related("branch"), self.request.user, "branch_id")



    def post(self, request, *args, **kwargs):

        obj = self.get_object()

        if not obj.user_can_delete(request.user):

            messages.error(request, "You do not have permission to delete this loan.")

            return redirect("farmer-loan-detail", pk=obj.pk)

        notes = ""
        if obj.status == FarmerLoan.Status.APPROVED and request.user.is_superuser:
            notes = "Superuser deleted approved loan."

        log_farmer_loan_deleted(obj, user=request.user, notes=notes)

        messages.success(request, "Loan deleted.")

        return super().post(request, *args, **kwargs)





class FarmerLoanSubmitView(LoginRequiredMixin, PermissionRequiredMixin, View):

    permission_required = "farmer_loans.change_farmerloan"



    def post(self, request, pk):

        loan = get_object_or_404(

            filter_by_user_branches(FarmerLoan.objects.select_related("branch"), request.user, "branch_id"),

            pk=pk,

        )

        if not loan.can_edit() or loan.status != FarmerLoan.Status.DRAFT:

            messages.error(request, "Only draft loans can be submitted.")

            return redirect("farmer-loan-detail", pk=loan.pk)

        with transaction.atomic():

            loan.status = FarmerLoan.Status.PENDING_APPROVAL

            loan.submitted_at = timezone.now()

            loan.save(update_fields=["status", "submitted_at", "updated_at"])

            loan.generate_repayment_schedule()

        log_farmer_loan_action(

            loan,

            user=request.user,

            action=FarmerLoanActionLog.Action.SUBMITTED,

            details={"installment_count": loan.installment_count},

        )

        messages.success(request, "Loan submitted for approval and repayment schedule generated.")

        return redirect("farmer-loan-detail", pk=loan.pk)





class FarmerLoanApproveView(LoginRequiredMixin, PermissionRequiredMixin, View):

    permission_required = "farmer_loans.change_farmerloan"



    def post(self, request, pk):

        loan = get_object_or_404(

            filter_by_user_branches(FarmerLoan.objects.select_related("branch"), request.user, "branch_id"),

            pk=pk,

        )

        if not loan.can_decide():

            messages.error(request, "Only pending loans can be approved.")

            return redirect("farmer-loan-detail", pk=loan.pk)

        loan.status = FarmerLoan.Status.APPROVED

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

        log_farmer_loan_action(loan, user=request.user, action=FarmerLoanActionLog.Action.APPROVED)

        messages.success(request, "Loan approved successfully.")

        return redirect("farmer-loan-detail", pk=loan.pk)





class FarmerLoanRejectView(LoginRequiredMixin, PermissionRequiredMixin, View):

    permission_required = "farmer_loans.change_farmerloan"



    def post(self, request, pk):

        loan = get_object_or_404(

            filter_by_user_branches(FarmerLoan.objects.select_related("branch"), request.user, "branch_id"),

            pk=pk,

        )

        if not loan.can_decide():

            messages.error(request, "Only pending loans can be rejected.")

            return redirect("farmer-loan-detail", pk=loan.pk)

        reason = (request.POST.get("rejection_reason") or "").strip()

        loan.status = FarmerLoan.Status.REJECTED

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

        log_farmer_loan_action(

            loan,

            user=request.user,

            action=FarmerLoanActionLog.Action.REJECTED,

            notes=reason,

        )

        messages.success(request, "Loan rejected.")

        return redirect("farmer-loan-detail", pk=loan.pk)





class BranchFarmerLookupView(LoginRequiredMixin, PermissionRequiredMixin, View):

    permission_required = "farmer_loans.add_farmerloan"



    def get(self, request):

        branch_id = request.GET.get("branch_id")

        if not branch_id or not str(branch_id).isdigit():

            return JsonResponse({"farmers": []})

        allowed_qs = filter_by_user_branches(Farmer.objects.select_related("branch"), request.user, "branch_id")

        farmers = allowed_qs.filter(branch_id=int(branch_id)).order_by("common_name", "full_name")

        rows = [_loan_farmer_row(farmer) for farmer in farmers]

        return JsonResponse({"farmers": rows})


