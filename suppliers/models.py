from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from canmee_dairies.constants import MILK_LITER_FACTOR

from masters.models import AuditModel


class Supplier(models.Model):
    class Category(models.TextChoices):
        RAW_MILK_SUPPLIER = "raw_milk_supplier", "RAW_MILK_SUPPLIER"
        CORPORATE_GOODS_SUPPLIER = "corporate_goods_supplier", "CORPORATE_GOODS_SUPPLIER"
        FARMER_GOODS_SUPPLIER = "farmer_goods_supplier", "FARMER_GOODS_SUPPLIER"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    class RateUnit(models.TextChoices):
        LITER = "liter", "Per liter"
        KG = "kg", "Per kg"

    name = models.CharField(max_length=140, unique=True)
    category = models.CharField(max_length=40, choices=Category.choices)
    contact_number = models.CharField(max_length=25, blank=True)
    address = models.TextField(blank=True)
    email = models.EmailField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="What we pay this raw-milk supplier (current rate).",
    )
    rate_unit = models.CharField(
        max_length=10,
        choices=RateUnit.choices,
        default=RateUnit.LITER,
        help_text="Whether the rate applies per liter or per kg.",
    )
    branches = models.ManyToManyField(
        "branches.Branch",
        blank=True,
        related_name="suppliers",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        permissions = [
            ("change_supplierrate", "Can change raw milk supplier rate"),
        ]

    def save(self, *args, **kwargs):
        is_adding = self._state.adding
        prior_rate = None
        prior_unit = None
        if not is_adding and self.pk:
            prior = (
                Supplier.objects.filter(pk=self.pk)
                .values("rate", "rate_unit", "category")
                .first()
            )
            if prior:
                prior_rate = prior["rate"]
                prior_unit = prior["rate_unit"]
        super().save(*args, **kwargs)
        if self.category != self.Category.RAW_MILK_SUPPLIER:
            return
        rate_changed = (
            is_adding
            or prior_rate is None
            or prior_rate != self.rate
            or (prior_unit or "") != (self.rate_unit or "")
        )
        if not rate_changed:
            return
        effective_from = getattr(self, "_rate_effective_from", None) or timezone.localdate()
        existing = (
            SupplierRateHistory.objects.filter(supplier=self, effective_from=effective_from)
            .order_by("-id")
            .first()
        )
        if existing:
            existing.rate = self.rate
            existing.rate_unit = self.rate_unit
            existing.save(update_fields=["rate", "rate_unit"])
        else:
            SupplierRateHistory.objects.create(
                supplier=self,
                rate=self.rate,
                rate_unit=self.rate_unit,
                effective_from=effective_from,
            )
        if hasattr(self, "_rate_effective_from"):
            delattr(self, "_rate_effective_from")

    def __str__(self):
        return self.name

    @property
    def is_raw_milk_supplier(self):
        return self.category == self.Category.RAW_MILK_SUPPLIER


class SupplierRateHistory(models.Model):
    supplier = models.ForeignKey(Supplier, on_delete=models.CASCADE, related_name="rate_history")
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    rate_unit = models.CharField(
        max_length=10,
        choices=Supplier.RateUnit.choices,
        default=Supplier.RateUnit.LITER,
    )
    effective_from = models.DateField(default=timezone.localdate, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-effective_from", "-id"]

    def __str__(self):
        return f"{self.supplier_id} {self.rate}/{self.rate_unit} from {self.effective_from}"


class SupplierAccount(models.Model):
    supplier = models.OneToOneField(Supplier, on_delete=models.CASCADE, related_name="account")
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["supplier__name"]

    def __str__(self):
        return f"{self.supplier.name} - {self.balance}"


class SupplierTransaction(models.Model):
    class TransactionType(models.TextChoices):
        CREDIT = "credit", "CREDIT"
        DEBIT = "debit", "DEBIT"

    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="transactions")
    date = models.DateTimeField(default=timezone.now)
    transaction_type = models.CharField(max_length=10, choices=TransactionType.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reference = models.CharField(max_length=60, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ["-date", "-id"]

    def clean(self):
        if self.amount is None or self.amount <= Decimal("0"):
            raise ValidationError({"amount": "Amount must be greater than zero."})

    def __str__(self):
        return f"{self.supplier.name} - {self.get_transaction_type_display()} {self.amount}"


class GRN(models.Model):
    class GRNType(models.TextChoices):
        CORPORATE = "corporate", "CORPORATE"
        FARMER_GOODS = "farmer_goods", "FARMER_GOODS"
        STOCK_CORRECTION = "stock_correction", "STOCK CORRECTION"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"

    class DiscountScope(models.TextChoices):
        LINE = "line", "Per product (free qty & line discount)"
        DOCUMENT = "document", "On GRN total only"

    grn_number = models.CharField(max_length=30, unique=True, blank=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.PROTECT, related_name="grns")
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="grns",
        null=True,
        blank=True,
        help_text="Receiving branch / warehouse context for this GRN.",
    )
    grn_type = models.CharField(max_length=20, choices=GRNType.choices)
    date = models.DateField(default=timezone.now)
    total_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    discount_scope = models.CharField(
        max_length=10,
        choices=DiscountScope.choices,
        default=DiscountScope.LINE,
        help_text="Use per-line discounts, or a single discount on the GRN total (not both).",
    )
    document_discount_percent = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=0,
        help_text="When scope is “on total”, percent off the subtotal (use this or amount, not both).",
    )
    document_discount_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        help_text="When scope is “on total”, flat amount off the subtotal.",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="grns_created",
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="grns_confirmed",
    )
    confirmed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-id"]

    def clean(self):
        if self.supplier_id:
            if self.supplier.status != Supplier.Status.ACTIVE:
                raise ValidationError({"supplier": "Cannot create GRN for an inactive supplier."})
            if self.supplier.category == Supplier.Category.RAW_MILK_SUPPLIER:
                raise ValidationError(
                    {
                        "supplier": "GRN is not allowed for raw milk suppliers; use a corporate or farmer goods supplier."
                    }
                )

    def save(self, *args, **kwargs):
        if not self.grn_number:
            today = timezone.now().strftime("%Y%m%d")
            last = GRN.objects.filter(grn_number__startswith=f"GRN-{today}-").order_by("-grn_number").first()
            next_seq = 1
            if last and last.grn_number:
                try:
                    next_seq = int(last.grn_number.split("-")[-1]) + 1
                except (TypeError, ValueError):
                    next_seq = 1
            self.grn_number = f"GRN-{today}-{next_seq:04d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.grn_number


class GRNActionLog(models.Model):
    """Immutable audit trail for GRN lifecycle (superuser audit modal)."""

    class Action(models.TextChoices):
        CREATED = "created", "Created"
        UPDATED = "updated", "Updated"
        CONFIRMED = "confirmed", "Confirmed"
        REVERTED_TO_DRAFT = "reverted_to_draft", "Reverted to draft"
        DELETED = "deleted", "Deleted"

    grn = models.ForeignKey(
        GRN,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="action_logs",
    )
    grn_number = models.CharField(max_length=30, blank=True)
    action = models.CharField(max_length=30, choices=Action.choices)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="grn_action_logs",
        null=True,
        blank=True,
    )
    performed_at = models.DateTimeField(auto_now_add=True)
    notes = models.TextField(blank=True)
    details = models.JSONField(blank=True, default=dict)

    class Meta:
        ordering = ["-performed_at", "-id"]

    def __str__(self):
        return f"{self.grn_number or 'GRN'} · {self.get_action_display()}"


def _quantize_money(value: Decimal) -> Decimal:
    return (value or Decimal("0")).quantize(Decimal("0.01"))


def compute_line_subtotal(item: "GRNItem") -> Decimal:
    """Line amount before GRN-level (document) discount; used for stock cost allocation."""
    q = item.quantity or Decimal("0")
    fq = item.free_quantity or Decimal("0")
    billable = max(Decimal("0"), q - fq)
    rate = item.unit_price or Decimal("0")
    if not item.grn_id:
        return _quantize_money(billable * rate)
    scope = item.grn.discount_scope
    if scope == GRN.DiscountScope.DOCUMENT:
        return _quantize_money(billable * rate)
    base = billable * rate
    pct = item.line_discount_percent or Decimal("0")
    after_pct = base * (Decimal("1") - pct / Decimal("100"))
    flat = item.line_discount_amount or Decimal("0")
    return _quantize_money(max(Decimal("0"), after_pct - flat))


class GRNItem(models.Model):
    grn = models.ForeignKey(GRN, on_delete=models.CASCADE, related_name="items")
    product = models.ForeignKey("stock_management.Product", on_delete=models.PROTECT, related_name="grn_items")
    quantity = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text="Total physical quantity received (including free units).",
    )
    free_quantity = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        help_text="Units received free; not charged at the purchase rate.",
    )
    unit_price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text="Purchase rate per billable unit (after excluding free quantity).",
    )
    issuing_price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        help_text="Issue/selling price per unit for downstream use (informational).",
    )
    line_discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    line_discount_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=0,
        help_text="Line cost before any GRN-level discount (stored subtotal for the line).",
    )

    class Meta:
        ordering = ["id"]

    def clean(self):
        if self.quantity is None or self.quantity <= Decimal("0"):
            raise ValidationError({"quantity": "Quantity must be greater than zero."})
        if self.unit_price is None or self.unit_price < Decimal("0"):
            raise ValidationError({"unit_price": "Unit price cannot be negative."})
        fq = self.free_quantity or Decimal("0")
        if fq < Decimal("0"):
            raise ValidationError({"free_quantity": "Free quantity cannot be negative."})
        if fq > (self.quantity or Decimal("0")):
            raise ValidationError({"free_quantity": "Free quantity cannot exceed total quantity."})
        billable = (self.quantity or Decimal("0")) - fq
        if billable <= Decimal("0"):
            raise ValidationError(
                {"free_quantity": "Billable quantity (total minus free) must be greater than zero."}
            )
        if (self.line_discount_percent or Decimal("0")) < Decimal("0") or (
            self.line_discount_percent or Decimal("0")
        ) > Decimal("100"):
            raise ValidationError({"line_discount_percent": "Enter a percent between 0 and 100."})

    def save(self, *args, **kwargs):
        # Form rows can submit blank optional numeric fields as None.
        # Normalize them so MySQL NOT NULL decimal columns never receive NULL.
        self.free_quantity = self.free_quantity if self.free_quantity is not None else Decimal("0")
        self.issuing_price = self.issuing_price if self.issuing_price is not None else Decimal("0")
        self.line_discount_percent = (
            self.line_discount_percent if self.line_discount_percent is not None else Decimal("0")
        )
        self.line_discount_amount = self.line_discount_amount if self.line_discount_amount is not None else Decimal("0")
        self.total_price = compute_line_subtotal(self)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.grn.grn_number} - {self.product.name}"


class FarmerGoodsStock(models.Model):
    product = models.OneToOneField("stock_management.Product", on_delete=models.CASCADE, related_name="farmer_goods_stock")
    quantity = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["product__name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=0),
                name="farmergoodsstock_quantity_gte_0",
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.quantity is not None and self.quantity < 0:
            raise ValidationError({"quantity": "Farmer goods stock cannot be negative."})

    def save(self, *args, **kwargs):
        if self.quantity is not None and self.quantity < 0:
            from django.core.exceptions import ValidationError

            raise ValidationError("Farmer goods stock quantity cannot be negative.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product.name} - {self.quantity}"


class FarmerGoodsModuleSettings(models.Model):
    """Singleton: superuser can skip the collection-point accept step."""

    enable_accept_notifications = models.BooleanField(
        default=True,
        help_text=(
            "When on, collection-point issues wait for Accept before stock and payment. "
            "When off, issues apply immediately and accept notifications are hidden."
        ),
    )
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        verbose_name = "Farmer goods settings"
        verbose_name_plural = "Farmer goods settings"

    def __str__(self):
        return "Farmer goods settings"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class FarmerGoodsIssue(models.Model):
    class IssueToType(models.TextChoices):
        BRANCH = "branch", "BRANCH"
        COLLECTION_POINT = "collection_point", "COLLECTION_POINT"
        FARMER = "farmer", "FARMER"

    class DriverStatus(models.TextChoices):
        NOT_REQUIRED = "not_required", "Not required"
        PENDING = "pending", "Pending driver"
        ACCEPTED = "accepted", "Accepted"
        REJECTED = "rejected", "Rejected"

    from_branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="farmer_goods_issues_from",
        null=True,
        blank=True,
        help_text="Branch context for this issue (issuing location).",
    )
    product = models.ForeignKey("stock_management.Product", on_delete=models.PROTECT, related_name="farmer_goods_issues")
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    issue_to_type = models.CharField(max_length=20, choices=IssueToType.choices)
    issue_to_id = models.PositiveBigIntegerField()
    batch_ref = models.CharField(max_length=36, blank=True, db_index=True)
    date = models.DateTimeField(default=timezone.now)
    is_settled = models.BooleanField(default=False)
    settled_at = models.DateTimeField(null=True, blank=True)
    settlement_note = models.CharField(max_length=255, blank=True)
    driver_status = models.CharField(
        max_length=20,
        choices=DriverStatus.choices,
        default=DriverStatus.NOT_REQUIRED,
        db_index=True,
        help_text="Collection-point issues require accept/reject before payment unless accept notifications are disabled.",
    )
    driver_responded_at = models.DateTimeField(null=True, blank=True)
    driver_response_note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"{self.product.name} issued {self.quantity}"

    @property
    def affects_payment(self):
        """Payment sheets only include accepted CP issues (and non-CP issues)."""
        if self.issue_to_type == self.IssueToType.COLLECTION_POINT:
            return self.driver_status == self.DriverStatus.ACCEPTED
        return self.driver_status != self.DriverStatus.REJECTED

    @property
    def affects_stock_ledger(self):
        """Rejected issues have stock restored and must not consume availability."""
        return self.driver_status != self.DriverStatus.REJECTED


class DriverFarmerGoodsRequest(models.Model):
    """Route driver request for farmer goods at a collection point."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ISSUED = "issued", "Issued"
        CANCELLED = "cancelled", "Cancelled"

    route = models.ForeignKey(
        "masters.Route",
        on_delete=models.PROTECT,
        related_name="farmer_goods_requests",
    )
    collection_point = models.ForeignKey(
        "masters.CollectionPoint",
        on_delete=models.PROTECT,
        related_name="farmer_goods_requests",
    )
    from_branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="driver_farmer_goods_requests",
        null=True,
        blank=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    note = models.CharField(max_length=255, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True, db_index=True)
    fulfilled_at = models.DateTimeField(null=True, blank=True)
    fulfilled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="fulfilled_driver_farmer_goods_requests",
    )
    issued_batch_ref = models.CharField(max_length=36, blank=True, db_index=True)
    fulfill_note = models.CharField(max_length=255, blank=True)
    skipped_out_of_stock = models.TextField(
        blank=True,
        help_text="Products skipped because stock was insufficient when fulfilling.",
    )

    class Meta:
        ordering = ["-requested_at", "-id"]

    def __str__(self):
        return f"Driver goods request #{self.pk} · {self.collection_point}"

    @property
    def has_issue_variance(self):
        """True when any line was issued for less than requested."""
        if self.status != self.Status.ISSUED:
            return False
        for line in self.lines.all():
            if line.has_shortfall:
                return True
        return False


class DriverFarmerGoodsRequestLine(models.Model):
    request = models.ForeignKey(
        DriverFarmerGoodsRequest,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    product = models.ForeignKey(
        "stock_management.Product",
        on_delete=models.PROTECT,
        related_name="driver_farmer_goods_request_lines",
    )
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    issued_quantity = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.product} × {self.quantity}"

    @property
    def shortfall_quantity(self):
        requested = self.quantity or Decimal("0")
        issued = self.issued_quantity or Decimal("0")
        short = requested - issued
        return short if short > Decimal("0") else Decimal("0.00")

    @property
    def has_shortfall(self):
        return self.shortfall_quantity > Decimal("0")


class ConsumptionSettlement(models.Model):
    class SourceType(models.TextChoices):
        COLLECTION_POINT = "collection_point", "COLLECTION_POINT"
        FARMER = "farmer", "FARMER"

    product = models.ForeignKey("stock_management.Product", on_delete=models.PROTECT, related_name="consumptions")
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    source_type = models.CharField(max_length=20, choices=SourceType.choices)
    source_id = models.PositiveBigIntegerField()
    date = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-date", "-id"]


class InventoryUsage(models.Model):
    product = models.ForeignKey("stock_management.Product", on_delete=models.PROTECT, related_name="inventory_usages")
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    usage_reason = models.CharField(max_length=255)
    date = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-date", "-id"]


class RawMilkSupplierCollection(AuditModel):
    class CollectionStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        DISPATCHED = "dispatched", "Dispatched"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class CollectionPaymentStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        PARTIAL = "partial", "Partial"
        PAID = "paid", "Paid"

    date = models.DateField(default=timezone.now)
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="raw_milk_supplier_collections",
    )
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.PROTECT,
        related_name="raw_milk_collections",
        limit_choices_to={"category": Supplier.Category.RAW_MILK_SUPPLIER},
    )
    dispatch_no = models.CharField(max_length=40, default="")
    browser_number = models.CharField("Browser number", max_length=40, blank=True)
    driver = models.CharField(max_length=120, blank=True)
    in_time = models.TimeField("In time", null=True, blank=True)
    out_time = models.TimeField("Out time", null=True, blank=True)
    sealing_numbers = models.CharField("Sealing numbers", max_length=255, blank=True)
    remarks = models.TextField(blank=True)
    status = models.CharField(
        max_length=20,
        choices=CollectionStatus.choices,
        default=CollectionStatus.PENDING,
    )
    supplier_result_quantity = models.DecimalField(
        "Supplier result (quantity)",
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
    )
    payment_status = models.CharField(
        "Payment status",
        max_length=20,
        choices=CollectionPaymentStatus.choices,
        default=CollectionPaymentStatus.PENDING,
    )
    unit_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Supplier rate applied when this collection was billed.",
    )
    rate_unit = models.CharField(
        max_length=10,
        choices=Supplier.RateUnit.choices,
        blank=True,
        default="",
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
        help_text="True after the payable was posted to the supplier account.",
    )
    kg = models.DecimalField(max_digits=10, decimal_places=2, editable=False)
    liters = models.DecimalField("Liters", max_digits=10, decimal_places=2)
    temperature = models.DecimalField(
        "Temperature (°C)",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
    )
    fat = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    snf = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    lr = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    alcohol = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    acidity = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    kq = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="raw_milk_collections_created",
    )

    class Meta:
        ordering = ["-date", "-id"]
        permissions = [
            ("view_rawmilkbulkcollection", "Can view raw milk bulk collection"),
        ]

    def clean(self):
        if self.supplier_id:
            if self.supplier.status != Supplier.Status.ACTIVE:
                raise ValidationError({"supplier": "Cannot collect milk for an inactive supplier."})
            if self.supplier.category != Supplier.Category.RAW_MILK_SUPPLIER:
                raise ValidationError({"supplier": "Select a RAW_MILK_SUPPLIER."})
        if self.liters is None or self.liters < Decimal("0"):
            raise ValidationError({"liters": "Quantity cannot be negative."})

    @property
    def outstanding_amount(self):
        total = self.total_amount or Decimal("0")
        paid = self.paid_amount or Decimal("0")
        outstanding = total - paid
        return outstanding if outstanding > 0 else Decimal("0.00")

    def payment_status_from_amounts(self):
        """Derived payment status from billed/paid amounts (same idea as buyer dispatches)."""
        return self.refresh_payment_status(save=False)

    def refresh_payment_status(self, *, save=False):
        total = self.total_amount or Decimal("0")
        paid = self.paid_amount or Decimal("0")
        if paid <= 0 or total <= 0:
            status = self.CollectionPaymentStatus.PENDING
        elif paid + Decimal("0.005") >= total:
            status = self.CollectionPaymentStatus.PAID
            if paid > total:
                self.paid_amount = total
        else:
            status = self.CollectionPaymentStatus.PARTIAL
        self.payment_status = status
        if save and self.pk:
            self.save(
                update_fields=["payment_status", "paid_amount", "updated_at"],
                skip_billing_sync=True,
            )
        return status

    def save(self, *args, **kwargs):
        self.kg = ((self.liters or Decimal("0")) / MILK_LITER_FACTOR).quantize(Decimal("0.01"))
        self.full_clean()
        skip_billing = kwargs.pop("skip_billing_sync", False)
        super().save(*args, **kwargs)
        if skip_billing or getattr(self, "_skip_billing_sync", False):
            return
        from suppliers.raw_milk_billing import sync_raw_milk_collection_billing

        sync_raw_milk_collection_billing(self)


class RawMilkSupplierPayment(models.Model):
    supplier = models.ForeignKey(
        Supplier, on_delete=models.PROTECT, related_name="raw_milk_payments"
    )
    date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reference = models.CharField(max_length=80, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="raw_milk_payments_recorded",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    ledger_transaction = models.OneToOneField(
        SupplierTransaction,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="raw_milk_payment",
    )

    class Meta:
        ordering = ["-date", "-id"]

    def __str__(self):
        return f"Raw milk payment {self.amount} · {self.supplier_id} · {self.date}"


class RawMilkSupplierPaymentAllocation(models.Model):
    payment = models.ForeignKey(
        RawMilkSupplierPayment, on_delete=models.CASCADE, related_name="allocations"
    )
    collection = models.ForeignKey(
        RawMilkSupplierCollection,
        on_delete=models.PROTECT,
        related_name="payment_allocations",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)

    class Meta:
        ordering = ["id"]
        unique_together = [("payment", "collection")]

    def __str__(self):
        return f"{self.payment_id} → collection {self.collection_id}: {self.amount}"