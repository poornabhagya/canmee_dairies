from decimal import Decimal, InvalidOperation

from django import forms
from django.core.exceptions import ValidationError
from django.conf import settings
from django.db.models import Sum
from django.db.models.functions import TruncMonth

from django.utils import timezone

from branches.utils import get_allowed_branches_qs
from canmee_dairies.widgets import FlatpickrDateInput
from masters.models import Farmer
from milk_collections.models import CollectionSource, MilkCollection

from django.forms import BaseModelFormSet, modelformset_factory

from .models import FarmerLoan, FarmerLoanRepaymentSchedule
from .schedule import default_first_repayment_date, installment_count_from_requested_amount


def compute_farmer_milk_stats(farmer):
    qs = (
        MilkCollection.objects.filter(source=CollectionSource.FARMER, farmer=farmer)
        .annotate(month=TruncMonth("date"))
        .values("month")
        .annotate(monthly_kg=Sum("kg"), monthly_liters=Sum("liters"))
    )
    rows = list(qs)
    if not rows:
        return None
    months_count = len(rows)
    total_kg = sum((row["monthly_kg"] or Decimal("0.00")) for row in rows)
    total_liters = sum((row["monthly_liters"] or Decimal("0.00")) for row in rows)
    avg_monthly_kg = (total_kg / months_count).quantize(Decimal("0.01"))
    avg_monthly_liters = (total_liters / months_count).quantize(Decimal("0.01"))

    income_per_liter = getattr(settings, "FARMER_MILK_INCOME_PER_LITER", None)
    if income_per_liter is not None:
        avg_monthly_income = (avg_monthly_liters * Decimal(str(income_per_liter))).quantize(Decimal("0.01"))
    elif (farmer.apply_rate_paid or "yes") == Farmer.ApplyRatePaid.YES and (farmer.rate or Decimal("0")) > 0:
        avg_monthly_income = (avg_monthly_liters * farmer.rate).quantize(Decimal("0.01"))
    else:
        avg_monthly_income = Decimal("0.00")

    return {
        "average_monthly_milk_production": avg_monthly_kg,
        "average_monthly_milk_income": avg_monthly_income,
        "farm_registration_number": (farmer.registration_number or "").strip(),
    }


def _format_money(value):
    from canmee_dairies.formatting import format_money

    return format_money(value, empty="")


def _parse_money(value, *, required=False, field_label="Amount"):
    if value in (None, ""):
        if required:
            raise ValidationError(f"{field_label} is required.")
        return None
    text = str(value).replace(",", "").strip()
    if not text:
        if required:
            raise ValidationError(f"{field_label} is required.")
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation as exc:
        raise ValidationError(f"Enter a valid {field_label.lower()}.") from exc
    return amount


class FarmerLoanForm(forms.ModelForm):
    INSTALLMENT_MODE_COUNT = "count"
    INSTALLMENT_MODE_REQUEST = "request"

    installment_mode = forms.ChoiceField(
        label="Instalment setup",
        choices=(
            (INSTALLMENT_MODE_COUNT, "Number of instalments"),
            (INSTALLMENT_MODE_REQUEST, "Request instalment"),
        ),
        initial=INSTALLMENT_MODE_COUNT,
        widget=forms.RadioSelect(attrs={"class": "loan-installment-mode-input"}),
        required=True,
    )
    requested_installment_amount = forms.CharField(
        label="Request instalment",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control js-money-input js-requested-installment",
                "inputmode": "decimal",
                "autocomplete": "off",
                "placeholder": "Amount per instalment",
            }
        ),
    )
    prior_paid_amount = forms.CharField(
        label="Already paid amount",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control js-money-input js-prior-paid-amount",
                "inputmode": "decimal",
                "autocomplete": "off",
                "placeholder": "Total paid before system entry",
            }
        ),
    )

    class Meta:
        model = FarmerLoan
        fields = [
            "branch",
            "farmer",
            "farm_registration_number",
            "average_monthly_milk_production",
            "average_monthly_milk_income",
            "loan_amount",
            "loan_date",
            "first_repayment_date",
            "installment_method",
            "installment_count",
            "is_existing_loan",
            "reason",
        ]
        widgets = {
            "branch": forms.Select(attrs={"class": "form-select"}),
            "farmer": forms.Select(attrs={"class": "form-select"}),
            "farm_registration_number": forms.TextInput(attrs={"class": "form-control"}),
            "average_monthly_milk_production": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0"}
            ),
            "loan_amount": forms.TextInput(
                attrs={
                    "class": "form-control js-money-input",
                    "inputmode": "decimal",
                    "autocomplete": "off",
                }
            ),
            "loan_date": FlatpickrDateInput(),
            "first_repayment_date": FlatpickrDateInput(),
            "installment_method": forms.RadioSelect(attrs={"class": "loan-installment-method-input"}),
            "installment_count": forms.NumberInput(
                attrs={"class": "form-control js-installment-count", "step": "1", "min": "1"}
            ),
            "reason": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "is_existing_loan": forms.CheckboxInput(attrs={"class": "form-check-input js-existing-loan"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        branches_qs = get_allowed_branches_qs(user).order_by("name") if user else None
        if branches_qs is not None:
            self.fields["branch"].queryset = branches_qs
            allowed_branch_ids = list(branches_qs.values_list("id", flat=True))
            farmer_qs = Farmer.objects.filter(branch_id__in=allowed_branch_ids) if allowed_branch_ids else Farmer.objects.none()
        else:
            farmer_qs = Farmer.objects.all()
        self.fields["farmer"].queryset = farmer_qs.order_by("common_name", "full_name")
        self.fields["farmer"].label_from_instance = (
            lambda obj: f"{obj.registration_number or '-'} - {obj.common_name or obj.full_name}"
        )
        self.fields["farm_registration_number"].required = False
        self.fields["average_monthly_milk_production"].required = False
        self.fields["average_monthly_milk_income"] = forms.CharField(
            label=self.fields["average_monthly_milk_income"].label,
            required=False,
            widget=forms.TextInput(
                attrs={
                    "class": "form-control js-money-input",
                    "inputmode": "decimal",
                    "autocomplete": "off",
                }
            ),
        )
        self.fields["loan_amount"] = forms.CharField(
            label=self.fields["loan_amount"].label,
            required=True,
            widget=forms.TextInput(
                attrs={
                    "class": "form-control js-money-input",
                    "inputmode": "decimal",
                    "autocomplete": "off",
                }
            ),
        )
        self.fields["installment_count"].label = "Number of instalments"
        self.fields["installment_count"].required = False
        self.fields["installment_method"].label = "Instalment method"

        if not self.instance.pk and not self.data:
            today = timezone.localdate()
            self.initial.setdefault("loan_date", today)
            self.initial.setdefault("first_repayment_date", default_first_repayment_date(today))
            self.initial.setdefault("installment_mode", self.INSTALLMENT_MODE_COUNT)
            self.initial.setdefault("installment_method", FarmerLoan.InstallmentMethod.SEMI_MONTHLY)

        if self.instance.pk:
            if self.instance.loan_amount is not None:
                self.initial.setdefault("loan_amount", _format_money(self.instance.loan_amount))
            if self.instance.average_monthly_milk_income is not None:
                self.initial.setdefault(
                    "average_monthly_milk_income",
                    _format_money(self.instance.average_monthly_milk_income),
                )
            if self.instance.requested_installment_amount:
                self.initial.setdefault("installment_mode", self.INSTALLMENT_MODE_REQUEST)
                self.initial.setdefault(
                    "requested_installment_amount",
                    _format_money(self.instance.requested_installment_amount),
                )
            else:
                self.initial.setdefault("installment_mode", self.INSTALLMENT_MODE_COUNT)
            if self.instance.prior_paid_amount:
                self.initial.setdefault("prior_paid_amount", _format_money(self.instance.prior_paid_amount))

        auto_stats = None
        farmer = self.instance.farmer if self.instance.pk and self.instance.farmer_id else None
        if farmer is None:
            raw_farmer_id = self.data.get("farmer")
            if raw_farmer_id and str(raw_farmer_id).isdigit():
                farmer = self.fields["farmer"].queryset.filter(pk=int(raw_farmer_id)).first()
        if farmer is not None:
            auto_stats = compute_farmer_milk_stats(farmer)
            if not auto_stats:
                reg_no = (farmer.registration_number or "").strip()
                if reg_no:
                    self.initial["farm_registration_number"] = reg_no
                    self.fields["farm_registration_number"].widget.attrs["readonly"] = "readonly"

        self.auto_metrics_locked = bool(auto_stats)
        if auto_stats:
            self.initial["average_monthly_milk_production"] = auto_stats["average_monthly_milk_production"]
            self.initial["average_monthly_milk_income"] = _format_money(auto_stats["average_monthly_milk_income"])
            self.initial["farm_registration_number"] = auto_stats["farm_registration_number"]
            self.fields["average_monthly_milk_production"].widget.attrs["readonly"] = "readonly"
            self.fields["average_monthly_milk_income"].widget.attrs["readonly"] = "readonly"
            self.fields["farm_registration_number"].widget.attrs["readonly"] = "readonly"

    def clean_loan_amount(self):
        raw = self.data.get("loan_amount") if self.data else self.cleaned_data.get("loan_amount")
        amount = _parse_money(raw, required=True, field_label="Loan amount")
        if amount <= Decimal("0"):
            raise ValidationError("Loan amount must be greater than zero.")
        return amount

    def clean_average_monthly_milk_income(self):
        raw = self.data.get("average_monthly_milk_income") if self.data else self.cleaned_data.get(
            "average_monthly_milk_income"
        )
        return _parse_money(raw, required=False, field_label="Average monthly milk income")

    def clean_requested_installment_amount(self):
        raw = self.data.get("requested_installment_amount") if self.data else self.cleaned_data.get(
            "requested_installment_amount"
        )
        return _parse_money(raw, required=False, field_label="Request instalment")

    def clean_prior_paid_amount(self):
        raw = self.data.get("prior_paid_amount") if self.data else self.cleaned_data.get("prior_paid_amount")
        amount = _parse_money(raw, required=False, field_label="Already paid amount")
        return amount or Decimal("0.00")

    def clean_installment_count(self):
        value = self.cleaned_data.get("installment_count")
        if value in (None, ""):
            return None
        return value

    def clean(self):
        cleaned = super().clean()
        farmer = cleaned.get("farmer")
        branch = cleaned.get("branch")
        if farmer and branch and farmer.branch_id != branch.id:
            self.add_error("farmer", "Selected farmer does not belong to selected branch.")
        loan_date = cleaned.get("loan_date")
        first_repayment_date = cleaned.get("first_repayment_date")
        if loan_date and first_repayment_date and first_repayment_date < loan_date:
            self.add_error("first_repayment_date", "First repayment date cannot be before loan date.")
        if farmer:
            stats = compute_farmer_milk_stats(farmer)
            if stats:
                cleaned["average_monthly_milk_production"] = stats["average_monthly_milk_production"]
                cleaned["average_monthly_milk_income"] = stats["average_monthly_milk_income"]
                cleaned["farm_registration_number"] = stats["farm_registration_number"]
            elif not (cleaned.get("farm_registration_number") or "").strip():
                cleaned["farm_registration_number"] = (farmer.registration_number or "").strip()

        mode = cleaned.get("installment_mode") or self.INSTALLMENT_MODE_COUNT
        installment_count = cleaned.get("installment_count")
        requested_installment = cleaned.get("requested_installment_amount")
        loan_amount = cleaned.get("loan_amount")
        is_existing = cleaned.get("is_existing_loan")
        prior_paid = cleaned.get("prior_paid_amount") or Decimal("0.00")

        if not is_existing:
            prior_paid = Decimal("0.00")
        elif loan_amount and prior_paid > loan_amount:
            self.add_error("prior_paid_amount", "Already paid amount cannot exceed the loan amount.")
        cleaned["prior_paid_amount"] = prior_paid

        if mode == self.INSTALLMENT_MODE_COUNT:
            cleaned["_requested_installment_amount"] = None
            if installment_count is None:
                self.add_error("installment_count", "Number of instalments is required.")
            elif installment_count < 1:
                self.add_error("installment_count", "Number of instalments must be at least 1.")
        elif mode == self.INSTALLMENT_MODE_REQUEST:
            if requested_installment is None:
                self.add_error("requested_installment_amount", "Request instalment is required.")
            elif requested_installment <= Decimal("0"):
                self.add_error("requested_installment_amount", "Request instalment must be greater than zero.")
            elif loan_amount:
                if requested_installment > loan_amount:
                    self.add_error(
                        "requested_installment_amount",
                        "Request instalment cannot be greater than the loan amount.",
                    )
                else:
                    cleaned["installment_count"] = installment_count_from_requested_amount(
                        loan_amount, requested_installment
                    )
                    cleaned["_requested_installment_amount"] = requested_installment.quantize(Decimal("0.01"))
        else:
            self.add_error("installment_mode", "Select how instalments should be calculated.")

        return cleaned

    def save(self, commit=True):
        instance = super().save(commit=False)
        requested = self.cleaned_data.get("_requested_installment_amount")
        instance.requested_installment_amount = requested
        instance.prior_paid_amount = self.cleaned_data.get("prior_paid_amount") or Decimal("0.00")
        if not instance.is_existing_loan:
            instance.prior_paid_amount = Decimal("0.00")
        if commit:
            instance.save()
            self.save_m2m()
        return instance


class FarmerLoanSchedulePaidForm(forms.ModelForm):
    paid_amount = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control form-control-sm js-money-input js-schedule-paid",
                "inputmode": "decimal",
                "autocomplete": "off",
            }
        ),
    )

    class Meta:
        model = FarmerLoanRepaymentSchedule
        fields = ("paid_amount",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk and self.instance.paid_amount is not None:
            self.initial["paid_amount"] = _format_money(self.instance.paid_amount)

    def clean_paid_amount(self):
        raw = self.data.get(self.add_prefix("paid_amount")) if self.data else self.cleaned_data.get("paid_amount")
        amount = _parse_money(raw, required=False, field_label="Paid amount")
        return amount or Decimal("0.00")

    def save(self, commit=True):
        instance = self.instance
        instance.paid_amount = (self.cleaned_data.get("paid_amount") or Decimal("0.00")).quantize(Decimal("0.01"))
        instance.sync_paid_status()
        if commit:
            instance.save()
        return instance


class BaseFarmerLoanSchedulePaidFormSet(BaseModelFormSet):
    def clean(self):
        super().clean()
        if any(self.errors):
            return
        total_paid = Decimal("0.00")
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            total_paid += form.cleaned_data.get("paid_amount") or Decimal("0.00")
        loan = self.forms[0].instance.loan if self.forms else None
        if loan and total_paid > loan.loan_amount:
            raise ValidationError("Total paid across instalments cannot exceed the loan amount.")


FarmerLoanSchedulePaidFormSet = modelformset_factory(
    FarmerLoanRepaymentSchedule,
    form=FarmerLoanSchedulePaidForm,
    formset=BaseFarmerLoanSchedulePaidFormSet,
    extra=0,
    can_delete=False,
)
