from decimal import Decimal

from django.conf import settings
from django.db import models
from django.utils import timezone


class ProductCategory(models.Model):
    code = models.SlugField(max_length=50, unique=True)
    name = models.CharField(max_length=120, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Product categories"

    def __str__(self):
        return self.name


class Product(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"

    name = models.CharField(max_length=120, unique=True)
    category = models.CharField(max_length=50)
    unit = models.CharField(max_length=20, default="kg")
    description = models.TextField(blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def get_category_display(self):
        name = (
            ProductCategory.objects.filter(code=self.category)
            .values_list("name", flat=True)
            .first()
        )
        return name or self.category

    def __str__(self):
        return f"{self.name} ({self.get_category_display()})"


class MainStock(models.Model):
    product = models.OneToOneField(Product, on_delete=models.CASCADE, related_name="main_stock")
    quantity = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Main stock"
        verbose_name_plural = "Main stock"
        ordering = ["product__name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=0),
                name="mainstock_quantity_gte_0",
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.quantity is not None and self.quantity < 0:
            raise ValidationError({"quantity": "Stock quantity cannot be negative."})

    def save(self, *args, **kwargs):
        if self.quantity is not None and self.quantity < 0:
            from django.core.exceptions import ValidationError

            raise ValidationError("Main stock quantity cannot be negative.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.product.name}: {self.quantity} {self.product.unit}"


class BranchStock(models.Model):
    branch = models.ForeignKey("branches.Branch", on_delete=models.CASCADE, related_name="product_stocks")
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="branch_stocks")
    quantity = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("branch", "product")
        ordering = ["branch__name", "product__name"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gte=0),
                name="branchstock_quantity_gte_0",
            ),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        if self.quantity is not None and self.quantity < 0:
            raise ValidationError({"quantity": "Stock quantity cannot be negative."})

    def save(self, *args, **kwargs):
        if self.quantity is not None and self.quantity < 0:
            from django.core.exceptions import ValidationError

            raise ValidationError("Branch stock quantity cannot be negative.")
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.branch.name} - {self.product.name}: {self.quantity} {self.product.unit}"


class StockTransfer(models.Model):
    """Legacy single-line transfer (e.g. main warehouse → branch). Prefer StockTransferNote for branch ↔ branch."""

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="transfers")
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    from_location = models.CharField(max_length=40, default="Main")
    to_branch = models.ForeignKey("branches.Branch", on_delete=models.PROTECT, related_name="incoming_transfers")
    date = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="stock_transfers",
    )

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"Transfer {self.product.name} -> {self.to_branch.name} ({self.quantity})"


class StockTransferNote(models.Model):
    """Multi-product transfer note between two branches."""

    transfer_number = models.CharField(max_length=32, unique=True, blank=True)
    from_branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="outgoing_transfer_notes",
    )
    to_branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="incoming_transfer_notes",
    )
    remarks = models.TextField(blank=True)
    date = models.DateField(default=timezone.now)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="stock_transfer_notes",
    )

    class Meta:
        ordering = ["-date", "-id"]

    def save(self, *args, **kwargs):
        if not self.transfer_number:
            from django.utils import timezone

            today = timezone.now().strftime("%Y%m%d")
            prefix = f"ST-{today}-"
            last = (
                StockTransferNote.objects.filter(transfer_number__startswith=prefix)
                .order_by("-transfer_number")
                .first()
            )
            seq = 1
            if last and last.transfer_number:
                try:
                    seq = int(last.transfer_number.split("-")[-1]) + 1
                except (TypeError, ValueError):
                    seq = 1
            self.transfer_number = f"ST-{today}-{seq:04d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.transfer_number or "Stock transfer"


class StockTransferLine(models.Model):
    note = models.ForeignKey(StockTransferNote, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="transfer_lines")
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    unit_price = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Selling/unit price from the related GRN at the source branch.",
    )

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.note.transfer_number} — {self.product.name}"

    @property
    def line_amount(self):
        return (self.quantity or Decimal("0")) * (self.unit_price or Decimal("0"))


class StockIssue(models.Model):
    class IssuedToType(models.TextChoices):
        FARMER = "farmer", "Farmer"
        COLLECTION_POINT = "collection_point", "Collection Point"
        STOCKTAKE = "stocktake", "Stocktake write-off"

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="issues")
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    branch = models.ForeignKey("branches.Branch", on_delete=models.PROTECT, related_name="stock_issues")
    issued_to_type = models.CharField(max_length=20, choices=IssuedToType.choices)
    issued_to_id = models.PositiveBigIntegerField()
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"Issue {self.product.name} from {self.branch.name} ({self.quantity})"


class StockCount(models.Model):
    """Physical stock verification (stocktake) for one branch."""

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        POSTED = "posted", "Posted"

    count_number = models.CharField(max_length=32, unique=True, blank=True)
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="stock_counts",
    )
    date = models.DateField(default=timezone.now)
    status = models.CharField(
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
    )
    remarks = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="stock_counts_created",
    )
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="stock_counts_posted",
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    correction_grn = models.ForeignKey(
        "suppliers.GRN",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="stock_counts",
        help_text="STOCK_CORRECTION GRN created for excess lines when posted.",
    )

    class Meta:
        ordering = ["-date", "-id"]
        permissions = [
            ("post_stockcount", "Can post stock count"),
        ]

    def save(self, *args, **kwargs):
        if not self.count_number:
            today = timezone.now().strftime("%Y%m%d")
            prefix = f"SC-{today}-"
            last = (
                StockCount.objects.filter(count_number__startswith=prefix)
                .order_by("-count_number")
                .first()
            )
            seq = 1
            if last and last.count_number:
                try:
                    seq = int(last.count_number.split("-")[-1]) + 1
                except (TypeError, ValueError):
                    seq = 1
            self.count_number = f"SC-{today}-{seq:04d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.count_number or "Stock count"

    @property
    def is_draft(self):
        return self.status == self.Status.DRAFT


class StockCountLine(models.Model):
    count = models.ForeignKey(StockCount, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="stock_count_lines")
    book_qty = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="System Available at snapshot / last refresh.",
    )
    physical_qty = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Physical counted quantity. Blank until counted.",
    )
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["product__name", "id"]
        unique_together = ("count", "product")

    def __str__(self):
        return f"{self.count.count_number} — {self.product.name}"

    @property
    def variance(self):
        if self.physical_qty is None:
            return None
        return (self.physical_qty - (self.book_qty or Decimal("0"))).quantize(Decimal("0.01"))


class StockLog(models.Model):
    class ActionType(models.TextChoices):
        GRN = "grn", "GRN"
        TRANSFER = "transfer", "TRANSFER"
        ISSUE = "issue", "ISSUE"

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="stock_logs")
    quantity = models.DecimalField(max_digits=14, decimal_places=2)
    action_type = models.CharField(max_length=20, choices=ActionType.choices)
    source = models.CharField(max_length=255)
    destination = models.CharField(max_length=255)
    # Stable link for undo/backfill (e.g. "fg-issue:123"). Blank for legacy rows.
    reference = models.CharField(max_length=64, blank=True, default="", db_index=True)
    date = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date"]

    def __str__(self):
        return f"{self.get_action_type_display()} {self.product.name} ({self.quantity})"