from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from canmee_dairies.constants import MILK_LITER_FACTOR

from masters.models import Buyer, AuditModel


class DispatchNotification(models.Model):
    class Kind(models.TextChoices):
        INCOMING_DISPATCH = "incoming_dispatch", "Incoming dispatch"
        DESTINATION_RESPONDED = "destination_responded", "Destination responded"
        RETURN_REQUESTED = "return_requested", "Return requested"
        RETURN_RESOLVED = "return_resolved", "Return resolved"
        RETURN_RECEIVED = "return_received", "Return received"

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dispatch_notifications",
    )
    dispatch = models.ForeignKey(
        "MilkDistribution",
        on_delete=models.CASCADE,
        related_name="notifications",
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=32, choices=Kind.choices)
    title = models.CharField(max_length=160)
    body = models.TextField()
    action_url = models.CharField(max_length=255, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.title} → {self.recipient_id}"


class MilkDistribution(AuditModel):
    class DistributionStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Delivered"
        RETURN = "return", "Return"
        CANCELLED = "cancelled", "Cancelled"

    class DistributionPaymentStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        PARTIAL = "partial", "Partial"
        PAID = "paid", "Paid"

    class BranchDestinationResponse(models.TextChoices):
        PENDING = "pending", "Pending"
        COLLECTED = "collected", "Collected"
        RETURN_REQUESTED = "return_requested", "Return requested"
        DIVERTED = "diverted", "Diverted"

    class BranchSourceReturnResponse(models.TextChoices):
        PENDING = "pending", "Pending acceptance"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    date = models.DateField()
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="milk_distributions",
        null=True,
        blank=True,
    )
    buyer = models.ForeignKey(Buyer, on_delete=models.PROTECT, null=True, blank=True)
    destination_branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="milk_distributions_received",
        null=True,
        blank=True,
        verbose_name="Destination branch",
    )
    dispatch_no = models.CharField(max_length=40)
    browser_number = models.CharField("Browser number", max_length=40, blank=True)
    driver = models.CharField(max_length=120, blank=True)
    temperature = models.DecimalField(
        "Temperature (°C)",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    kq = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    sealing_numbers = models.CharField("Sealing numbers", max_length=255, blank=True)
    in_time = models.TimeField("In time", null=True, blank=True)
    out_time = models.TimeField("Out time", null=True, blank=True)
    remarks = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=DistributionStatus.choices,
        default=DistributionStatus.PENDING,
    )
    buyer_result_quantity = models.DecimalField(
        "Buyer result (quantity)",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    delivered_quantity = models.DecimalField(
        "Delivered quantity (kg)",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Quantity received by the buyer when status is Delivered. Stored in kg.",
    )
    delivered_ts = models.DecimalField(
        "Delivered TS",
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Total solids measured at the buyer for delivered milk.",
    )
    returned_branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="milk_distribution_returns",
        null=True,
        blank=True,
        verbose_name="Returned branch",
    )
    payment_status = models.CharField(
        "Payment status",
        max_length=20,
        choices=DistributionPaymentStatus.choices,
        default=DistributionPaymentStatus.PENDING,
    )
    unit_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Buyer rate applied when this dispatch was billed.",
    )
    is_rate_manual = models.BooleanField(
        default=False,
        help_text="If true, unit_rate is kept as entered instead of the period rate.",
    )
    rate_unit = models.CharField(
        max_length=10,
        choices=Buyer.RateUnit.choices,
        blank=True,
        default="",
        help_text="liter or kg — empty for branch dispatches.",
    )
    ts = models.DecimalField(
        "TS",
        max_digits=6,
        decimal_places=2,
        default=0,
        help_text="Total solids (FAT + SNF).",
    )
    billable_qty = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=0,
        help_text="Quantity used for billing (liters or kg per rate_unit).",
    )
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    is_billed = models.BooleanField(
        default=False,
        help_text="True after the receivable was posted to the buyer account.",
    )
    branch_destination_response = models.CharField(
        max_length=20,
        choices=BranchDestinationResponse.choices,
        default=BranchDestinationResponse.PENDING,
        blank=True,
    )
    branch_source_return_response = models.CharField(
        max_length=20,
        choices=BranchSourceReturnResponse.choices,
        blank=True,
    )
    divert_to_branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="diverted_incoming_dispatches",
        null=True,
        blank=True,
    )
    divert_to_buyer = models.ForeignKey(
        Buyer,
        on_delete=models.PROTECT,
        related_name="diverted_incoming_dispatches",
        null=True,
        blank=True,
    )
    diverted_by_branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="dispatches_diverted_from_here",
        null=True,
        blank=True,
        verbose_name="Diverted by branch",
    )
    branch_response_notes = models.TextField(blank=True)
    branch_return_quantity_kg = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    branch_responded_at = models.DateTimeField(null=True, blank=True)
    branch_responded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="branch_dispatch_responses",
        null=True,
        blank=True,
    )
    source_return_responded_at = models.DateTimeField(null=True, blank=True)
    source_return_responded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="branch_dispatch_return_responses",
        null=True,
        blank=True,
    )
    kg = models.DecimalField(max_digits=10, decimal_places=2)
    fat = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    snf = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    lr = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    alcohol = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    acidity = models.DecimalField(max_digits=6, decimal_places=2, default=0)

    @property
    def liters_equivalent(self):
        """Liters corresponding to stored kg; matches dispatch quantity conversion."""
        return (self.kg * MILK_LITER_FACTOR).quantize(Decimal("0.01"))

    def compute_total_solids(self):
        fat = self.fat or Decimal("0")
        snf = self.snf or Decimal("0")
        return (fat + snf).quantize(Decimal("0.01"))

    @property
    def buyer_result_liters(self):
        if self.buyer_result_quantity is None:
            return None
        return (self.buyer_result_quantity * MILK_LITER_FACTOR).quantize(Decimal("0.01"))

    @property
    def delivered_liters(self):
        if self.delivered_quantity is None:
            return None
        return (self.delivered_quantity * MILK_LITER_FACTOR).quantize(Decimal("0.01"))

    @property
    def qty_variation_kg(self):
        """Delivered minus dispatched, in kg. None until a buyer result is recorded."""
        if self.delivered_quantity is None:
            return None
        dispatched = self.kg or Decimal("0")
        return (self.delivered_quantity - dispatched).quantize(Decimal("0.01"))

    @property
    def qty_variation_liters(self):
        if self.delivered_quantity is None:
            return None
        delivered = self.delivered_liters
        dispatched = self.liters_equivalent
        if delivered is None:
            return None
        return (delivered - dispatched).quantize(Decimal("0.01"))

    @property
    def outstanding_amount(self):
        total = self.total_amount or Decimal("0")
        paid = self.paid_amount or Decimal("0")
        outstanding = total - paid
        return outstanding if outstanding > 0 else Decimal("0.00")

    def payment_status_from_amounts(self):
        """Derive payment status from billed/paid amounts (source of truth)."""
        total = self.total_amount or Decimal("0")
        paid = self.paid_amount or Decimal("0")
        if paid <= 0 or total <= 0:
            return self.DistributionPaymentStatus.PENDING
        if paid + Decimal("0.005") >= total:
            return self.DistributionPaymentStatus.PAID
        return self.DistributionPaymentStatus.PARTIAL

    def refresh_payment_status(self, *, save=False):
        status = self.payment_status_from_amounts()
        total = self.total_amount or Decimal("0")
        paid = self.paid_amount or Decimal("0")
        if status == self.DistributionPaymentStatus.PAID and paid > total:
            self.paid_amount = total
        self.payment_status = status
        if save and self.pk:
            self.save(update_fields=["payment_status", "paid_amount", "updated_at"])
        return status

    def destination_label(self):
        if self.destination_branch_id:
            return self.destination_branch.name
        if self.buyer_id:
            return str(self.buyer)
        return "—"

    def is_buyer_return_received_for(self, branch_ids):
        """Buyer return credited to one of the user's branches (not sent from them)."""
        if not branch_ids or not self.buyer_id:
            return False
        if not self.buyer_result_quantity or self.buyer_result_quantity <= Decimal("0"):
            return False
        if not self.returned_branch_id or self.returned_branch_id not in branch_ids:
            return False
        return self.branch_id not in branch_ids

    def list_status_display(self):
        if self.is_branch_dispatch() and self.status == self.DistributionStatus.RETURN:
            if self.returned_branch_id:
                return f"Return → {self.returned_branch.name}"
            return "Return"
        return self.get_status_display()

    @property
    def divert_target_label(self):
        if self.divert_to_buyer_id:
            return str(self.divert_to_buyer)
        if self.divert_to_branch_id:
            return self.divert_to_branch.name
        return ""

    @property
    def show_outgoing_divert_status(self):
        return bool(self.divert_target_label)

    @property
    def branch_destination_status_label(self):
        if not self.is_branch_dispatch():
            return ""
        labels = {
            self.BranchDestinationResponse.PENDING: "Pending",
            self.BranchDestinationResponse.COLLECTED: "Collected",
            self.BranchDestinationResponse.RETURN_REQUESTED: "Return requested",
            self.BranchDestinationResponse.DIVERTED: "Diverted",
        }
        if not self.branch_destination_response:
            return "Pending"
        return labels.get(
            self.branch_destination_response,
            self.get_branch_destination_response_display(),
        )

    @property
    def branch_destination_status_kind(self):
        """CSS hook: return | divert | collected | pending."""
        if not self.is_branch_dispatch() or not self.branch_destination_response:
            return "pending"
        if self.branch_destination_response == self.BranchDestinationResponse.RETURN_REQUESTED:
            return "return"
        if self.branch_destination_response == self.BranchDestinationResponse.DIVERTED:
            return "divert"
        if self.branch_destination_response == self.BranchDestinationResponse.COLLECTED:
            return "collected"
        return "pending"

    @property
    def branch_source_return_status_label(self):
        if not self.branch_source_return_response:
            return ""
        labels = {
            self.BranchSourceReturnResponse.PENDING: "Pending acceptance",
            self.BranchSourceReturnResponse.ACCEPTED: "Accepted",
            self.BranchSourceReturnResponse.REJECTED: "Rejected",
        }
        return labels.get(
            self.branch_source_return_response,
            self.get_branch_source_return_response_display(),
        )

    def is_branch_dispatch(self):
        return bool(self.destination_branch_id)

    def needs_destination_response(self):
        return (
            self.is_branch_dispatch()
            and self.branch_destination_response == self.BranchDestinationResponse.PENDING
            and self.status != self.DistributionStatus.CANCELLED
        )

    def needs_source_return_acceptance(self):
        return (
            self.is_branch_dispatch()
            and self.branch_destination_response == self.BranchDestinationResponse.RETURN_REQUESTED
            and self.branch_source_return_response == self.BranchSourceReturnResponse.PENDING
            and self.status != self.DistributionStatus.CANCELLED
        )

    def clean(self):
        if self.buyer_id and self.destination_branch_id:
            raise ValidationError("Select either a buyer or a destination branch, not both.")
        if not self.buyer_id and not self.destination_branch_id:
            raise ValidationError("Select a buyer or a destination branch.")
        if self.branch_id and self.destination_branch_id and self.branch_id == self.destination_branch_id:
            raise ValidationError({"destination_branch": "Destination branch must differ from the source branch."})
        if self.status == self.DistributionStatus.RETURN:
            if not self.returned_branch_id:
                raise ValidationError({"returned_branch": "Select the branch receiving the return."})
            qty = self.buyer_result_quantity
            if self.is_branch_dispatch() and self.branch_return_quantity_kg is not None:
                qty = self.branch_return_quantity_kg
            if qty is None or qty <= Decimal("0"):
                raise ValidationError(
                    {"buyer_result_quantity": "Enter the returned quantity (liters)."}
                )
        elif self.buyer_id and self.buyer_result_quantity and self.buyer_result_quantity > Decimal("0"):
            if not self.returned_branch_id:
                raise ValidationError({"returned_branch": "Select the branch receiving the return."})
            dispatched = self.kg or Decimal("0")
            delivered = self.delivered_quantity or Decimal("0")
            if delivered + self.buyer_result_quantity > dispatched:
                raise ValidationError("Delivered plus returned cannot exceed dispatched quantity.")
        if self.is_branch_dispatch():
            if self.divert_to_branch_id and self.divert_to_buyer_id:
                raise ValidationError("Select either a divert branch or a divert buyer, not both.")
            if self.divert_to_branch_id and self.branch_id == self.divert_to_branch_id:
                raise ValidationError({"divert_to_branch": "Divert branch must differ from the source branch."})
            if (
                self.divert_to_branch_id
                and self.destination_branch_id == self.divert_to_branch_id
                and not self.diverted_by_branch_id
            ):
                raise ValidationError({"divert_to_branch": "Divert branch must differ from the current destination."})

    def save(self, *args, **kwargs):
        if self.status != self.DistributionStatus.RETURN and not (
            self.buyer_id and self.buyer_result_quantity and self.buyer_result_quantity > Decimal("0")
        ):
            self.returned_branch = None
        self.ts = self.compute_total_solids()
        self.full_clean()
        skip_billing = kwargs.pop("skip_billing_sync", False)
        super().save(*args, **kwargs)
        if skip_billing or getattr(self, "_skip_billing_sync", False):
            return
        if self.buyer_id:
            from dispatch.buyer_billing import sync_dispatch_billing

            sync_dispatch_billing(self)

    class Meta:
        ordering = ["-date", "-id"]
        permissions = [
            ("respond_branch_dispatch", "Can respond to incoming branch dispatches"),
            (
                "change_dispatch_billing_rate",
                "Can add or change billing rate on a milk dispatch",
            ),
        ]


class BuyerAccount(models.Model):
    """Receivable balance: positive means the buyer owes us."""

    buyer = models.OneToOneField(Buyer, on_delete=models.CASCADE, related_name="account")
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["buyer__name"]

    def __str__(self):
        return f"{self.buyer.name} — {self.balance}"


class BuyerTransaction(models.Model):
    class TransactionType(models.TextChoices):
        CREDIT = "credit", "CREDIT"
        DEBIT = "debit", "DEBIT"

    buyer = models.ForeignKey(Buyer, on_delete=models.PROTECT, related_name="transactions")
    date = models.DateTimeField(default=timezone.now)
    transaction_type = models.CharField(max_length=10, choices=TransactionType.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reference = models.CharField(max_length=80, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-id"]

    def clean(self):
        if self.amount is None or self.amount <= Decimal("0"):
            raise ValidationError({"amount": "Amount must be greater than zero."})

    def __str__(self):
        return f"{self.buyer_id} {self.transaction_type} {self.amount}"


class BuyerPayment(models.Model):
    buyer = models.ForeignKey(Buyer, on_delete=models.PROTECT, related_name="payments")
    date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reference = models.CharField(max_length=80, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="buyer_payments_recorded",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    ledger_transaction = models.OneToOneField(
        BuyerTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payment",
    )

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"Payment {self.amount} · {self.buyer_id} · {self.date}"


class BuyerPaymentAllocation(models.Model):
    payment = models.ForeignKey(
        BuyerPayment, on_delete=models.CASCADE, related_name="allocations"
    )
    distribution = models.ForeignKey(
        MilkDistribution, on_delete=models.PROTECT, related_name="payment_allocations"
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["id"]
        unique_together = [("payment", "distribution")]

    def __str__(self):
        return f"{self.payment_id} → dispatch {self.distribution_id}: {self.amount}"
