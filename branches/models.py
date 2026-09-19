from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Branch(models.Model):
    class CollectionUnit(models.TextChoices):
        KG = "kg", "Kg"
        LITERS = "liters", "Liters"

    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120, unique=True)
    collection_unit = models.CharField(
        max_length=10,
        choices=CollectionUnit.choices,
        default=CollectionUnit.KG,
        verbose_name="Collection unit",
        help_text="Default unit for entering milk quantity in Milk collection.",
    )
    users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="assigned_branches",
    )
    employees = models.ManyToManyField(
        "hrm.Employee",
        blank=True,
        related_name="assigned_branches",
    )
    maximum_milk_storage = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        verbose_name="Maximum milk storage",
        help_text="Maximum milk storage capacity for this branch (kg).",
    )
    branch_manager = models.ForeignKey(
        "hrm.Employee",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="managed_branches",
        verbose_name="Branch manager",
    )
    manager_contact = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Manager contact",
    )
    branch_contact = models.CharField(
        max_length=50,
        blank=True,
        verbose_name="Branch contact",
    )
    location = models.CharField(
        max_length=255,
        blank=True,
        verbose_name="Location",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["code", "name"]

    def _generate_code(self):
        existing_codes = Branch.objects.values_list("code", flat=True)
        max_num = 0
        for code in existing_codes:
            if str(code).isdigit():
                max_num = max(max_num, int(code))
        return f"{max_num + 1:04d}"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self._generate_code()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.code} - {self.name}"


class BranchMilkStock(models.Model):
    branch = models.OneToOneField(
        Branch,
        on_delete=models.CASCADE,
        related_name="milk_stock",
    )
    opening_kg = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    collected_kg = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    dispatched_kg = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    current_kg = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["branch__code"]

    def __str__(self):
        return f"{self.branch} stock: {self.current_kg} kg"


class BranchMilkStockAdjustment(models.Model):
    class AdjustmentType(models.TextChoices):
        SHORTAGE = "shortage", "Shortage"
        EXCESS = "excess", "Excess"

    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="milk_stock_adjustments",
    )
    date = models.DateField(default=timezone.localdate)
    adjustment_type = models.CharField(max_length=20, choices=AdjustmentType.choices)
    kg = models.DecimalField(max_digits=10, decimal_places=2)
    liters = models.DecimalField(max_digits=10, decimal_places=2)
    remarks = models.TextField(blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="branch_milk_stock_adjustments_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "-id"]

    def clean(self):
        if self.kg is None or self.kg <= Decimal("0"):
            raise ValidationError({"kg": "Quantity must be greater than zero."})
        if self.liters is None or self.liters < Decimal("0"):
            raise ValidationError({"liters": "Liters cannot be negative."})

    @property
    def signed_kg(self):
        amount = self.kg or Decimal("0.00")
        if self.adjustment_type == self.AdjustmentType.SHORTAGE:
            return -amount
        return amount

    @property
    def signed_liters(self):
        amount = self.liters or Decimal("0.00")
        if self.adjustment_type == self.AdjustmentType.SHORTAGE:
            return -amount
        return amount

    def __str__(self):
        return f"{self.date} {self.branch} {self.get_adjustment_type_display()} {self.kg} kg"
