from decimal import Decimal

from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.utils import timezone

from .utils import MILK_LITER_FACTOR

from hrm.models import Employee
from .models import Branch, BranchMilkStockAdjustment

User = get_user_model()


def _employee_label(obj):
    return (
        (obj.common_name or "").strip()
        or (obj.get_full_name() or "").strip()
        or (obj.first_name or "").strip()
        or f"Employee {obj.pk}"
    )


class BranchForm(forms.ModelForm):
    class Meta:
        model = Branch
        fields = [
            "name",
            "maximum_milk_storage",
            "branch_manager",
            "manager_contact",
            "branch_contact",
            "location",
            "collection_unit",
            "users",
            "employees",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "Branch name"}),
            "maximum_milk_storage": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0", "placeholder": "e.g. 5000"}
            ),
            "branch_manager": forms.Select(attrs={"class": "form-select"}),
            "manager_contact": forms.TextInput(attrs={"class": "form-control", "placeholder": "Manager phone or email"}),
            "branch_contact": forms.TextInput(attrs={"class": "form-control", "placeholder": "Branch phone or email"}),
            "location": forms.TextInput(attrs={"class": "form-control", "placeholder": "Town or area"}),
            "collection_unit": forms.Select(attrs={"class": "form-select"}),
            "users": forms.SelectMultiple(attrs={"class": "form-select branch-team-select"}),
            "employees": forms.SelectMultiple(attrs={"class": "form-select branch-team-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["maximum_milk_storage"].label = "Maximum milk storage (kg)"
        self.fields["branch_manager"].empty_label = "Select branch manager"
        employee_qs = Employee.objects.filter(status=Employee.EmploymentStatus.ACTIVE).order_by(
            "common_name", "first_name", "last_name"
        )
        self.fields["branch_manager"].queryset = employee_qs
        self.fields["branch_manager"].label_from_instance = _employee_label
        self.fields["users"].queryset = User.objects.order_by("username")
        self.fields["employees"].queryset = employee_qs
        self.fields["employees"].label_from_instance = _employee_label


class BranchOpeningStockForm(forms.Form):
    opening_quantity = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
        label="Opening stock",
    )


class BranchStockAdjustmentForm(forms.ModelForm):
    quantity = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=0.01,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "step": "0.01",
                "min": "0.01",
                "id": "id_branch_stock_quantity",
            }
        ),
        label="Quantity",
    )

    class Meta:
        model = BranchMilkStockAdjustment
        fields = ["date", "adjustment_type", "remarks"]
        widgets = {
            "date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "adjustment_type": forms.Select(attrs={"class": "form-select"}),
            "remarks": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }

    def __init__(self, *args, branch=None, **kwargs):
        self.branch = branch
        super().__init__(*args, **kwargs)
        self.fields["remarks"].required = False
        if not self.instance.pk:
            self.fields["date"].initial = timezone.localdate()
        elif branch:
            if branch.collection_unit == Branch.CollectionUnit.LITERS:
                self.initial.setdefault("quantity", self.instance.liters)
            else:
                self.initial.setdefault("quantity", self.instance.kg)

    def clean(self):
        cleaned = super().clean()
        branch = self.branch
        if branch is None:
            raise ValidationError("Branch is required.")
        quantity = cleaned.get("quantity")
        if quantity is not None:
            quantity_unit = (self.data.get("quantity_unit") or "").strip().lower()
            if quantity_unit not in ("kg", "liters"):
                quantity_unit = branch.collection_unit
            if quantity_unit == Branch.CollectionUnit.LITERS:
                kg = (quantity / MILK_LITER_FACTOR).quantize(Decimal("0.01"))
            else:
                kg = quantity.quantize(Decimal("0.01"))
            self.instance.branch = branch
            self.instance.kg = kg
            self.instance.liters = (kg * MILK_LITER_FACTOR).quantize(Decimal("0.01"))
        return cleaned

    def save(self, commit=True, *, created_by=None, branch=None):
        branch = branch or self.branch
        if branch is None:
            raise ValueError("branch is required")

        instance = super().save(commit=False)
        instance.branch = branch
        if created_by is not None:
            instance.created_by = created_by
        if commit:
            instance.save()
        return instance
