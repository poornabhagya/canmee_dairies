from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from farmer_loans.schedule import METHOD_SEMI_MONTHLY, build_repayment_due_dates


class CollectionPointLoan(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        PENDING_APPROVAL = "pending_approval", "Pending approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    class InstallmentMethod(models.TextChoices):
        SEMI_MONTHLY = "semi_monthly", "15th & last day (about 2 per month)"
        MONTHLY = "monthly", "Monthly instalments"

    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="collection_point_loans",
    )
    collection_point = models.ForeignKey(
        "masters.CollectionPoint",
        on_delete=models.PROTECT,
        related_name="loans",
    )
    point_reference = models.CharField(max_length=60, blank=True)
    average_monthly_milk_production = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
    )
    average_monthly_milk_income = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
    )
    loan_amount = models.DecimalField(max_digits=14, decimal_places=2)
    loan_date = models.DateField(default=timezone.localdate)
    first_repayment_date = models.DateField()
    installment_method = models.CharField(
        max_length=20,
        choices=InstallmentMethod.choices,
        default=InstallmentMethod.SEMI_MONTHLY,
        help_text="How instalment due dates are scheduled after the first repayment date.",
    )
    installment_count = models.PositiveIntegerField(
        help_text="Total number of instalments.",
    )
    requested_installment_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="When set, each instalment uses this amount except the last (remainder).",
    )
    is_existing_loan = models.BooleanField(
        default=False,
        help_text="Loan already in progress before it was recorded in the system.",
    )
    prior_paid_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Total amount already paid on this loan before system entry.",
    )
    reason = models.TextField()
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="approved_collection_point_loans",
        null=True,
        blank=True,
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="rejected_collection_point_loans",
        null=True,
        blank=True,
    )
    rejected_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_collection_point_loans",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "cp_loan"
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"Loan #{self.pk} - {self.collection_point}"

    @property
    def point_branch_id(self):
        if not self.collection_point_id:
            return None
        route = getattr(self.collection_point, "route", None)
        return route.branch_id if route else None

    def clean(self):
        super().clean()
        if self.collection_point_id and self.branch_id:
            cp_branch_id = self.point_branch_id
            if cp_branch_id and cp_branch_id != self.branch_id:
                raise ValidationError(
                    {"collection_point": "Selected collection point does not belong to the selected branch."}
                )
        if self.loan_amount is not None and self.loan_amount <= Decimal("0"):
            raise ValidationError({"loan_amount": "Loan amount must be greater than zero."})
        if self.installment_count < 1:
            raise ValidationError({"installment_count": "Number of instalments must be at least 1."})
        if self.loan_date and self.first_repayment_date and self.first_repayment_date < self.loan_date:
            raise ValidationError({"first_repayment_date": "First repayment date cannot be before loan date."})
        prior_paid = self.prior_paid_amount or Decimal("0")
        if prior_paid < Decimal("0"):
            raise ValidationError({"prior_paid_amount": "Prior paid amount cannot be negative."})
        if self.loan_amount is not None and prior_paid > self.loan_amount:
            raise ValidationError({"prior_paid_amount": "Prior paid amount cannot exceed the loan amount."})

    def apply_prior_paid_to_schedule(self):
        remaining = (self.prior_paid_amount or Decimal("0")).quantize(Decimal("0.01"))
        if remaining <= Decimal("0"):
            return
        for row in self.repayment_schedules.order_by("installment_number"):
            if remaining <= Decimal("0"):
                break
            outstanding = (row.installment_amount - (row.paid_amount or Decimal("0"))).quantize(Decimal("0.01"))
            if outstanding <= Decimal("0"):
                continue
            payment = min(remaining, outstanding)
            row.paid_amount = ((row.paid_amount or Decimal("0")) + payment).quantize(Decimal("0.01"))
            row.sync_paid_status()
            row.save(update_fields=["paid_amount", "is_paid", "paid_on"])
            remaining = (remaining - payment).quantize(Decimal("0.01"))

    def can_edit(self):
        return self.status in {self.Status.DRAFT, self.Status.PENDING_APPROVAL}

    def can_delete(self):
        return self.status in {self.Status.DRAFT, self.Status.PENDING_APPROVAL}

    def user_can_delete(self, user):
        if self.can_delete():
            return True
        return bool(
            user
            and getattr(user, "is_superuser", False)
            and self.status == self.Status.APPROVED
        )

    def can_decide(self):
        return self.status == self.Status.PENDING_APPROVAL

    @property
    def recorded_paid_total(self):
        """Sum of schedule paid amounts when a schedule exists; else prior_paid_amount."""
        from django.db.models import Sum

        if self.pk and self.repayment_schedules.exists():
            total = self.repayment_schedules.aggregate(total=Sum("paid_amount"))["total"]
            return (total or Decimal("0")).quantize(Decimal("0.01"))
        return (self.prior_paid_amount or Decimal("0")).quantize(Decimal("0.01"))

    def generate_repayment_schedule(self):
        if not self.pk:
            raise ValidationError("Loan must be saved before generating schedule.")
        self.repayment_schedules.all().delete()
        installment_count = max(1, self.installment_count)
        method = self.installment_method or METHOD_SEMI_MONTHLY
        due_dates = build_repayment_due_dates(
            self.first_repayment_date,
            installment_count,
            method=method,
        )
        rows = []
        requested = self.requested_installment_amount
        if requested is not None and requested > Decimal("0"):
            req = requested.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            remaining = self.loan_amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            for i, due_date in enumerate(due_dates):
                if i == len(due_dates) - 1:
                    amount = remaining
                else:
                    amount = min(req, remaining)
                amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                remaining = (remaining - amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                rows.append(
                    CollectionPointLoanRepaymentSchedule(
                        loan=self,
                        installment_number=i + 1,
                        due_date=due_date,
                        installment_amount=amount,
                        available_on=due_date,
                    )
                )
        else:
            base = (self.loan_amount / installment_count).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            running_total = Decimal("0.00")
            for i, due_date in enumerate(due_dates):
                amount = base
                if i == installment_count - 1:
                    amount = (self.loan_amount - running_total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                rows.append(
                    CollectionPointLoanRepaymentSchedule(
                        loan=self,
                        installment_number=i + 1,
                        due_date=due_date,
                        installment_amount=amount,
                        available_on=due_date,
                    )
                )
                running_total += amount
        CollectionPointLoanRepaymentSchedule.objects.bulk_create(rows)
        self.apply_prior_paid_to_schedule()


class CollectionPointLoanActionLog(models.Model):
    class Action(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        DELETED = "deleted", "Deleted"
        SUBMITTED = "submitted", "Submitted for approval"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        REVERTED_TO_DRAFT = "reverted_to_draft", "Reverted to draft"
        SCHEDULE_PAID_UPDATED = "schedule_paid_updated", "Repayment schedule paid amounts updated"

    loan = models.ForeignKey(
        CollectionPointLoan,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="action_logs",
    )
    loan_label = models.CharField(max_length=40, blank=True)
    action = models.CharField(max_length=30, choices=Action.choices)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="collection_point_loan_action_logs",
    )
    performed_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)
    details = models.JSONField(blank=True, default=dict)

    class Meta:
        db_table = "cp_loan_action_log"
        ordering = ["-performed_at", "-id"]

    def __str__(self):
        return f"{self.loan_label or 'Loan'} · {self.get_action_display()}"


class CollectionPointLoanRepaymentSchedule(models.Model):
    class PaymentMethod(models.TextChoices):
        DIRECT = "direct", "Direct"
        MILK_PAYMENT_SHEET = "milk_payment_sheet", "Payment sheet settlement"

    loan = models.ForeignKey(
        CollectionPointLoan,
        on_delete=models.CASCADE,
        related_name="repayment_schedules",
    )
    installment_number = models.PositiveIntegerField()
    due_date = models.DateField()
    installment_amount = models.DecimalField(max_digits=14, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal("0.00"))
    is_paid = models.BooleanField(default=False)
    paid_on = models.DateField(null=True, blank=True)
    payment_method = models.CharField(max_length=20, choices=PaymentMethod.choices, blank=True)
    payment_note = models.CharField(max_length=255, blank=True)
    available_on = models.DateField(
        help_text="First date the remaining instalment can be deducted on a payment sheet.",
    )

    class Meta:
        db_table = "cp_loan_schedule"
        ordering = ["installment_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["loan", "installment_number"],
                name="cp_loan_sched_loan_inst_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["is_paid", "available_on"],
                name="cp_loan_sched_open_avail_idx",
            ),
        ]

    def __str__(self):
        return f"Loan {self.loan_id} - Installment {self.installment_number}"

    def save(self, *args, **kwargs):
        if self.available_on is None:
            self.available_on = self.due_date
        super().save(*args, **kwargs)

    @property
    def remaining_amount(self):
        remaining = (self.installment_amount or Decimal("0")) - (self.paid_amount or Decimal("0"))
        if remaining < 0:
            remaining = Decimal("0")
        return remaining.quantize(Decimal("0.01"))

    @property
    def payment_status_label(self):
        paid = (self.paid_amount or Decimal("0")).quantize(Decimal("0.01"))
        amount = (self.installment_amount or Decimal("0")).quantize(Decimal("0.01"))
        if paid >= amount and amount > Decimal("0"):
            return "Paid"
        if paid > Decimal("0"):
            return "Partial"
        return "Pending"

    def sync_paid_status(self, paid_on=None):
        paid = (self.paid_amount or Decimal("0")).quantize(Decimal("0.01"))
        self.paid_amount = paid
        self.is_paid = paid >= (self.installment_amount or Decimal("0"))
        if self.is_paid:
            self.paid_on = paid_on or self.paid_on or timezone.localdate()
        else:
            self.paid_on = None
            self.payment_method = ""

    def clean(self):
        super().clean()
        paid = self.paid_amount or Decimal("0")
        if paid < Decimal("0"):
            raise ValidationError({"paid_amount": "Paid amount cannot be negative."})


class CollectionPointLoanPayment(models.Model):
    class Method(models.TextChoices):
        DIRECT = "direct", "Direct"
        MILK_PAYMENT_SHEET = "milk_payment_sheet", "Payment sheet settlement"

    loan = models.ForeignKey(
        CollectionPointLoan,
        on_delete=models.CASCADE,
        related_name="payment_transactions",
    )
    payment_date = models.DateField()
    method = models.CharField(max_length=20, choices=Method.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    note = models.CharField(max_length=255, blank=True)
    source_period_payment = models.ForeignKey(
        "reports.CollectionPointPeriodPayment",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="loan_payment_transactions",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="collection_point_loan_payments_created",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payment_date", "-id"]

    def __str__(self):
        return f"Loan {self.loan_id} {self.payment_date}: {self.amount}"


class CollectionPointLoanPaymentAllocation(models.Model):
    payment = models.ForeignKey(
        CollectionPointLoanPayment,
        on_delete=models.CASCADE,
        related_name="allocations",
    )
    schedule = models.ForeignKey(
        CollectionPointLoanRepaymentSchedule,
        on_delete=models.CASCADE,
        related_name="payment_allocations",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["payment_id", "schedule__installment_number", "id"]

    def __str__(self):
        return f"Payment {self.payment_id} -> instalment {self.schedule_id}: {self.amount}"
