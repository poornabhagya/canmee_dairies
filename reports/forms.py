from decimal import Decimal, InvalidOperation

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from canmee_dairies.formatting import format_money
from canmee_dairies.widgets import FlatpickrDateInput
from masters.models import (
    AdvancePaymentType,
    CollectionPoint,
    CollectionPointAdvancePayment,
    Farmer,
    FarmerAdvancePayment,
)


class FarmerAdvancePaymentForm(forms.ModelForm):
    class Meta:
        model = FarmerAdvancePayment
        fields = ["farmer", "date", "advance_type", "amount", "note"]
        widgets = {
            "farmer": forms.Select(attrs={"class": "form-select advance-farmer-select"}),
            "date": FlatpickrDateInput(),
            "advance_type": forms.Select(attrs={"class": "form-select"}),
            "note": forms.TextInput(attrs={"class": "form-control", "placeholder": "Optional note"}),
        }

    def __init__(self, *args, farmers_qs=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = farmers_qs if farmers_qs is not None else Farmer.objects.all()
        self.fields["farmer"].queryset = qs.order_by("common_name", "full_name")
        self.fields["farmer"].label_from_instance = (
            lambda obj: f"{obj.registration_number or '-'} - {obj.common_name or obj.full_name}"
        )
        self.fields["date"].label = "Advance date"
        self.fields["advance_type"].label = "Type"
        self.fields["advance_type"].choices = AdvancePaymentType.choices
        self.fields["amount"] = forms.CharField(
            label="Amount",
            required=True,
            widget=forms.TextInput(
                attrs={
                    "class": "form-control js-money-input advance-amount-input",
                    "inputmode": "decimal",
                    "autocomplete": "off",
                }
            ),
        )
        if (
            not self.is_bound
            and getattr(self.instance, "pk", None)
            and self.instance.amount is not None
        ):
            self.initial["amount"] = format_money(self.instance.amount, empty="")

    def clean_amount(self):
        raw = self.data.get("amount") if self.data else self.cleaned_data.get("amount")
        text = str(raw or "").replace(",", "").strip()
        if not text:
            raise ValidationError("Amount is required.")
        try:
            amount = Decimal(text)
        except InvalidOperation as exc:
            raise ValidationError("Enter a valid amount.") from exc
        if amount <= 0:
            raise ValidationError("Advance amount must be greater than zero.")
        return amount


class CollectionPointAdvancePaymentForm(forms.ModelForm):
    class Meta:
        model = CollectionPointAdvancePayment
        fields = ["collection_point", "date", "advance_type", "amount", "note"]
        widgets = {
            "collection_point": forms.Select(attrs={"class": "form-select advance-point-select"}),
            "date": FlatpickrDateInput(),
            "advance_type": forms.Select(attrs={"class": "form-select"}),
            "note": forms.TextInput(attrs={"class": "form-control", "placeholder": "Optional note"}),
        }

    def __init__(self, *args, points_qs=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = points_qs if points_qs is not None else CollectionPoint.objects.all()
        self.fields["collection_point"].queryset = qs.select_related("route", "route__branch").order_by(
            "number", "name"
        )
        self.fields["collection_point"].label = "Collection point"
        self.fields["collection_point"].label_from_instance = (
            lambda obj: f"{obj.number} — {obj.name}"
            + (f" ({obj.route.name})" if getattr(obj, "route_id", None) and obj.route_id else "")
        )
        self.fields["date"].label = "Advance date"
        self.fields["advance_type"].label = "Type"
        self.fields["advance_type"].choices = AdvancePaymentType.choices
        self.fields["amount"] = forms.CharField(
            label="Amount",
            required=True,
            widget=forms.TextInput(
                attrs={
                    "class": "form-control js-money-input advance-amount-input",
                    "inputmode": "decimal",
                    "autocomplete": "off",
                }
            ),
        )
        if (
            not self.is_bound
            and getattr(self.instance, "pk", None)
            and self.instance.amount is not None
        ):
            self.initial["amount"] = format_money(self.instance.amount, empty="")

    def clean_amount(self):
        raw = self.data.get("amount") if self.data else self.cleaned_data.get("amount")
        text = str(raw or "").replace(",", "").strip()
        if not text:
            raise ValidationError("Amount is required.")
        try:
            amount = Decimal(text)
        except InvalidOperation as exc:
            raise ValidationError("Enter a valid amount.") from exc
        if amount <= 0:
            raise ValidationError("Advance amount must be greater than zero.")
        return amount


class PeriodPaymentRecordForm(forms.Form):
    amount = forms.CharField(
        label="Payment amount",
        widget=forms.TextInput(
            attrs={
                "class": "form-control js-money-input",
                "inputmode": "decimal",
                "autocomplete": "off",
            }
        ),
    )
    payment_date = forms.DateField(
        label="Payment date",
        required=False,
        widget=FlatpickrDateInput(),
    )
    note = forms.CharField(
        label="Note",
        required=False,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "Optional note"}
        ),
    )
    start = forms.DateField(widget=forms.HiddenInput())
    end = forms.DateField(widget=forms.HiddenInput())
    return_url = forms.CharField(widget=forms.HiddenInput())
    payment_method = forms.ChoiceField(
        required=False,
        choices=[
            ("cash", "Cash"),
            ("bank_transfer", "Bank transfer"),
        ],
    )
    bank_account_id = forms.IntegerField(required=False)

    def clean_amount(self):
        raw = self.data.get("amount") if self.data else self.cleaned_data.get("amount")
        text = str(raw or "").replace(",", "").strip()
        if not text:
            raise ValidationError("Amount is required.")
        try:
            amount = Decimal(text)
        except InvalidOperation as exc:
            raise ValidationError("Enter a valid amount.") from exc
        if amount < 0:
            raise ValidationError("Payment amount cannot be negative.")
        return amount

    def clean_payment_date(self):
        return self.cleaned_data.get("payment_date") or timezone.localdate()
