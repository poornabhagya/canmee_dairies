from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class AssetModuleSettings(models.Model):
    """Singleton-style settings. Depreciation is optional (admin/superuser)."""

    enable_depreciation = models.BooleanField(
        default=False,
        help_text="When enabled, depreciation policies and run schedules are available.",
    )
    default_label_prefix = models.CharField(max_length=20, default="AST", blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        verbose_name = "Asset module settings"
        verbose_name_plural = "Asset module settings"

    def __str__(self):
        return "Asset module settings"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class AssetType(models.Model):
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=40, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "name", "id"]
        verbose_name = "Asset type"
        verbose_name_plural = "Asset types"

    def __str__(self):
        return self.name


class AssetCategory(models.Model):
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=40, unique=True)
    asset_type = models.ForeignKey(
        AssetType,
        on_delete=models.PROTECT,
        related_name="categories",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["asset_type__sort_order", "name", "id"]
        verbose_name_plural = "Asset categories"

    def __str__(self):
        return self.name

    def clean(self):
        if self.parent_id and self.parent_id == self.pk:
            raise ValidationError({"parent": "Category cannot be its own parent."})
        if (
            self.parent_id
            and self.asset_type_id
            and self.parent.asset_type_id
            and self.parent.asset_type_id != self.asset_type_id
        ):
            raise ValidationError({"parent": "Parent category must belong to the same type."})


class AssetLocation(models.Model):
    """Physical location, optionally under a parent (sub-location) and branch."""

    name = models.CharField(max_length=150)
    code = models.CharField(max_length=40)
    branch = models.ForeignKey(
        "branches.Branch",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="asset_locations",
        help_text="Blank = shared / main (all branches).",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="sub_locations",
    )
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["branch__name", "name", "id"]
        unique_together = [("branch", "code")]
        verbose_name = "Asset location"
        verbose_name_plural = "Asset locations"

    def __str__(self):
        if self.branch_id:
            return f"{self.name} ({self.branch.name})"
        return f"{self.name} (Main)"

    def clean(self):
        if self.parent_id and self.parent_id == self.pk:
            raise ValidationError({"parent": "Location cannot be its own parent."})
        if self.parent_id and self.branch_id and self.parent.branch_id not in (None, self.branch_id):
            raise ValidationError(
                {"parent": "Parent location must be in the same branch or Main."}
            )


class DepreciationPolicy(models.Model):
    class Method(models.TextChoices):
        STRAIGHT_LINE = "straight_line", "Straight line"
        REDUCING_BALANCE = "reducing_balance", "Reducing balance"

    name = models.CharField(max_length=120)
    method = models.CharField(
        max_length=30, choices=Method.choices, default=Method.STRAIGHT_LINE
    )
    useful_life_months = models.PositiveIntegerField(
        default=60, help_text="Default useful life in months."
    )
    residual_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Residual / salvage as % of cost.",
    )
    annual_rate_percent = models.DecimalField(
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Used for reducing-balance method (annual %).",
    )
    is_active = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Depreciation policies"

    def __str__(self):
        return self.name


class Asset(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        IN_STORAGE = "in_storage", "In storage"
        UNDER_MAINTENANCE = "under_maintenance", "Under maintenance"
        DISPOSED = "disposed", "Disposed"
        LOST = "lost", "Lost"
        TRANSFERRED = "transferred", "Transferred"

    class Condition(models.TextChoices):
        NEW = "new", "New"
        GOOD = "good", "Good"
        FAIR = "fair", "Fair"
        POOR = "poor", "Poor"
        DAMAGED = "damaged", "Damaged"

    asset_tag = models.CharField(
        max_length=60,
        unique=True,
        db_index=True,
        help_text="Unique label / barcode tag.",
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    branch = models.ForeignKey(
        "branches.Branch",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="assets",
        help_text="Blank = Main (shared / HO).",
    )
    category = models.ForeignKey(
        AssetCategory,
        on_delete=models.PROTECT,
        related_name="assets",
    )
    asset_type = models.ForeignKey(
        AssetType,
        on_delete=models.PROTECT,
        related_name="assets",
        verbose_name="Type",
    )
    location = models.ForeignKey(
        AssetLocation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assets",
    )
    status = models.CharField(
        max_length=30, choices=Status.choices, default=Status.ACTIVE, db_index=True
    )
    condition = models.CharField(
        max_length=20, choices=Condition.choices, default=Condition.GOOD
    )
    serial_number = models.CharField(max_length=120, blank=True)
    manufacturer = models.CharField(max_length=120, blank=True)
    model_number = models.CharField(max_length=120, blank=True)
    purchase_date = models.DateField(null=True, blank=True)
    purchase_cost = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00")
    )
    current_value = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Book / market value after valuations & depreciation.",
    )
    supplier_name = models.CharField(max_length=200, blank=True)
    warranty_expiry = models.DateField(null=True, blank=True)
    depreciation_policy = models.ForeignKey(
        DepreciationPolicy,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assets",
    )
    useful_life_months = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Override policy useful life (months).",
    )
    salvage_value = models.DecimalField(
        max_digits=14, decimal_places=2, default=Decimal("0.00")
    )
    custodian = models.ForeignKey(
        "hrm.Employee",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="custodied_assets",
    )
    acquired_at = models.DateTimeField(default=timezone.now)
    disposed_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="assets_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        permissions = [
            ("manage_asset_settings", "Can manage asset module settings"),
            ("run_asset_depreciation", "Can run asset depreciation"),
            ("verify_assets", "Can verify and value assets"),
            ("print_asset_labels", "Can generate asset labels"),
        ]
        indexes = [
            models.Index(fields=["branch", "status"]),
            models.Index(fields=["category", "status"]),
            models.Index(fields=["asset_type", "status"]),
        ]

    def __str__(self):
        return f"{self.asset_tag} — {self.name}"

    @property
    def branch_label(self):
        return self.branch.name if self.branch_id else "Head Office"

    @property
    def written_down(self):
        cost = self.purchase_cost or Decimal("0.00")
        book = self.current_value or Decimal("0.00")
        return cost - book if cost > book else Decimal("0.00")

    def clean(self):
        if (
            self.category_id
            and self.asset_type_id
            and self.category.asset_type_id
            and self.category.asset_type_id != self.asset_type_id
        ):
            raise ValidationError(
                {"category": "Category must belong to the selected asset type."}
            )

    def save(self, *args, **kwargs):
        if self._state.adding and self.current_value == Decimal("0.00") and self.purchase_cost:
            self.current_value = self.purchase_cost
        super().save(*args, **kwargs)


class AssetTransfer(models.Model):
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="transfers")
    from_branch = models.ForeignKey(
        "branches.Branch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    to_branch = models.ForeignKey(
        "branches.Branch",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    from_location = models.ForeignKey(
        AssetLocation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    to_location = models.ForeignKey(
        AssetLocation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    transferred_at = models.DateTimeField(default=timezone.now)
    note = models.CharField(max_length=255, blank=True)
    transferred_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["-transferred_at", "-id"]

    def __str__(self):
        return f"Transfer {self.asset_id} @ {self.transferred_at}"


class AssetVerification(models.Model):
    class Result(models.TextChoices):
        FOUND = "found", "Found"
        MISSING = "missing", "Missing"
        DAMAGED = "damaged", "Damaged"
        RELOCATED = "relocated", "Relocated"

    asset = models.ForeignKey(
        Asset, on_delete=models.CASCADE, related_name="verifications"
    )
    verified_at = models.DateTimeField(default=timezone.now, db_index=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="asset_verifications",
    )
    result = models.CharField(
        max_length=20, choices=Result.choices, default=Result.FOUND
    )
    condition = models.CharField(
        max_length=20, choices=Asset.Condition.choices, blank=True
    )
    location = models.ForeignKey(
        AssetLocation,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    assessed_value = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["-verified_at", "-id"]

    def __str__(self):
        return f"Verify {self.asset_id} — {self.result}"


class AssetValuation(models.Model):
    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="valuations")
    valued_at = models.DateField(default=timezone.localdate)
    previous_value = models.DecimalField(max_digits=14, decimal_places=2)
    new_value = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.CharField(max_length=255, blank=True)
    valued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-valued_at", "-id"]

    def __str__(self):
        return f"Valuation {self.asset_id} → {self.new_value}"


class AssetDepreciationEntry(models.Model):
    asset = models.ForeignKey(
        Asset, on_delete=models.CASCADE, related_name="depreciation_entries"
    )
    period_start = models.DateField()
    period_end = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    book_value_before = models.DecimalField(max_digits=14, decimal_places=2)
    book_value_after = models.DecimalField(max_digits=14, decimal_places=2)
    policy = models.ForeignKey(
        DepreciationPolicy,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="entries",
    )
    run_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-period_end", "-id"]
        verbose_name_plural = "Asset depreciation entries"
        constraints = [
            models.UniqueConstraint(
                fields=["asset", "period_start", "period_end"],
                name="uniq_asset_depr_period",
            )
        ]

    def __str__(self):
        return f"Depr {self.asset_id} {self.period_start}–{self.period_end}"


class AssetLabelBatch(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    label_count = models.PositiveIntegerField(default=0)
    note = models.CharField(max_length=255, blank=True)
    assets = models.ManyToManyField(Asset, blank=True, related_name="label_batches")

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name_plural = "Asset label batches"

    def __str__(self):
        return f"Label batch #{self.pk} ({self.label_count})"


def assets_for_user(user):
    """Branch-scoped asset queryset."""
    from branches.utils import get_user_branch_ids

    qs = Asset.objects.select_related(
        "branch", "asset_type", "category", "location", "depreciation_policy", "custodian"
    )
    if user.is_superuser:
        return qs
    branch_ids = get_user_branch_ids(user)
    if branch_ids is None:
        return qs
    if not branch_ids:
        return qs.filter(branch__isnull=True)
    return qs.filter(Q(branch_id__in=branch_ids) | Q(branch__isnull=True))
