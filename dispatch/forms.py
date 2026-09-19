from decimal import Decimal

from django import forms
from django.utils import timezone
from canmee_dairies.constants import MILK_LITER_FACTOR
from canmee_dairies.widgets import FlatpickrDateInput
from branches.models import Branch
from branches.utils import (
    filter_m2m_by_branch,
    filter_m2m_by_user_branches,
    get_allowed_branches_qs,
    get_default_branch_id,
    queryset_with_required_pk,
    resolve_form_branch_id,
)
from masters.buyer_rates import buyer_rate_at_date
from masters.models import Buyer
from .models import MilkDistribution


def _qty_to_kg(qty, unit, dispatched_kg, dispatched_liters=None):
    """Convert entered qty to kg without rounding above the dispatched kg."""
    qty = (qty or Decimal("0")).quantize(Decimal("0.01"))
    dispatched_kg = (dispatched_kg or Decimal("0")).quantize(Decimal("0.01"))
    if unit == "kg":
        return qty
    if dispatched_liters is None:
        dispatched_liters = (dispatched_kg * MILK_LITER_FACTOR).quantize(Decimal("0.01"))
    else:
        dispatched_liters = dispatched_liters.quantize(Decimal("0.01"))
    if qty == dispatched_liters:
        return dispatched_kg
    kg = (qty / MILK_LITER_FACTOR).quantize(Decimal("0.01"))
    if kg > dispatched_kg and (kg - dispatched_kg) <= Decimal("0.05"):
        return dispatched_kg
    return kg


def _next_distribution_dispatch_no():
    max_dispatch = 0
    for dispatch_no in MilkDistribution.objects.values_list("dispatch_no", flat=True):
        value = (dispatch_no or "").strip()
        if value.isdigit():
            max_dispatch = max(max_dispatch, int(value))
    return f"{max_dispatch + 1:05d}"


class MilkDistributionForm(forms.ModelForm):
    DISPATCH_TO_BUYER = "buyer"
    DISPATCH_TO_BRANCH = "branch"

    dispatch_to = forms.ChoiceField(
        label="Dispatch to",
        choices=[
            (DISPATCH_TO_BUYER, "Buyer"),
            (DISPATCH_TO_BRANCH, "Branch"),
        ],
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
        initial=DISPATCH_TO_BUYER,
    )
    alcohol_result = forms.ChoiceField(
        label="Alcohol",
        choices=(("negative", "Negative"), ("positive", "Positive")),
        widget=forms.Select(attrs={"class": "form-select", "id": "id_alcohol_result"}),
        initial="negative",
    )

    class Meta:
        model = MilkDistribution
        fields = [
            "date",
            "branch",
            "dispatch_to",
            "buyer",
            "destination_branch",
            "kg",
            "dispatch_no",
            "browser_number",
            "driver",
            "in_time",
            "out_time",
            "sealing_numbers",
            "temperature",
            "fat",
            "lr",
            "snf",
            "alcohol",
            "kq",
            "remarks",
        ]
        widgets = {
            "date": FlatpickrDateInput(),
            "branch": forms.Select(attrs={"class": "form-select", "id": "id_branch"}),
            "buyer": forms.Select(attrs={"class": "form-select", "id": "id_buyer"}),
            "destination_branch": forms.Select(attrs={"class": "form-select", "id": "id_destination_branch"}),
            "dispatch_no": forms.TextInput(attrs={"class": "form-control", "id": "id_dispatch_no"}),
            "browser_number": forms.TextInput(attrs={"class": "form-control"}),
            "driver": forms.TextInput(attrs={"class": "form-control"}),
            "temperature": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "inputmode": "decimal"}),
            "kq": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "inputmode": "decimal"}),
            "sealing_numbers": forms.TextInput(attrs={"class": "form-control"}),
            "in_time": forms.TimeInput(attrs={"class": "form-control", "type": "time"}),
            "out_time": forms.TimeInput(attrs={"class": "form-control", "type": "time"}),
            "kg": forms.NumberInput(
                attrs={
                    "class": "form-control",
                    "step": "0.01",
                    "min": "0",
                    "id": "id_kg",
                    "inputmode": "decimal",
                }
            ),
            "fat": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "inputmode": "decimal", "id": "id_fat"}
            ),
            "snf": forms.NumberInput(
                attrs={
                    "class": "form-control dispatch-snf-auto",
                    "step": "0.01",
                    "inputmode": "decimal",
                    "id": "id_snf",
                    "readonly": "readonly",
                }
            ),
            "lr": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "inputmode": "decimal", "id": "id_lr"}
            ),
            "alcohol": forms.HiddenInput(),
            "remarks": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["branch"].required = True
        branch_qs = get_allowed_branches_qs(user).order_by("code") if user else Branch.objects.none()
        if user is not None:
            self.fields["branch"].queryset = branch_qs
            self.fields["destination_branch"].queryset = Branch.objects.all().order_by("name")
            if not self.instance.pk and not self.data:
                default_branch_id = get_default_branch_id(user)
                if default_branch_id:
                    self.initial.setdefault("branch", default_branch_id)
        else:
            self.fields["destination_branch"].queryset = Branch.objects.all().order_by("name")
        self.fields["destination_branch"].required = False
        self.fields["buyer"].required = False

        for name in ("fat", "snf", "lr", "kq"):
            if name in self.fields:
                self.fields[name].label = name.upper()

        if not self.instance.pk and not self.data:
            self.initial.setdefault("dispatch_no", _next_distribution_dispatch_no())
            self.initial.setdefault("date", timezone.localdate())
            self.fields["alcohol_result"].initial = "negative"
        else:
            current_alcohol = self.initial.get("alcohol", getattr(self.instance, "alcohol", Decimal("0")))
            try:
                current_alcohol = Decimal(str(current_alcohol))
            except Exception:
                current_alcohol = Decimal("0")
            self.fields["alcohol_result"].initial = "positive" if current_alcohol > 0 else "negative"

        if self.instance.pk and self.instance.destination_branch_id:
            self.initial.setdefault("dispatch_to", self.DISPATCH_TO_BRANCH)

        branch_id = resolve_form_branch_id(self)
        buyer_qs = filter_m2m_by_user_branches(Buyer.objects.all(), user).order_by("name")
        if branch_id:
            buyer_qs = filter_m2m_by_branch(buyer_qs, branch_id)
        required_buyer_id = self.instance.buyer_id if self.instance.pk else None
        self.fields["buyer"].queryset = queryset_with_required_pk(buyer_qs, required_buyer_id).order_by("name")
        self.fields["branch"].label_from_instance = lambda b: b.name
        self.fields["destination_branch"].label_from_instance = lambda b: b.name

    def clean(self):
        data = super().clean()
        dispatch_to = data.get("dispatch_to")
        branch = data.get("branch")
        buyer = data.get("buyer")
        destination_branch = data.get("destination_branch")

        if dispatch_to == self.DISPATCH_TO_BRANCH:
            if not destination_branch:
                self.add_error("destination_branch", "Select the destination branch.")
            data["buyer"] = None
        else:
            if not buyer:
                self.add_error("buyer", "Select a buyer.")
            elif branch and not buyer.branches.filter(pk=branch.pk).exists():
                self.add_error("buyer", "This buyer is not linked to the selected branch.")
            data["destination_branch"] = None

        if branch and destination_branch and branch.pk == destination_branch.pk:
            self.add_error("destination_branch", "Destination branch must differ from the source branch.")

        fat = data.get("fat")
        lr = data.get("lr")
        if fat is not None and lr is not None:
            data["snf"] = ((lr / Decimal("4")) + (Decimal("0.2") * fat) + Decimal("0.36")).quantize(
                Decimal("0.01")
            )

        data["alcohol"] = Decimal("1.00") if data.get("alcohol_result") == "positive" else Decimal("0.00")

        return data

    def clean_dispatch_no(self):
        value = (self.cleaned_data.get("dispatch_no") or "").strip()
        if value:
            return value
        return _next_distribution_dispatch_no()

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.acidity = Decimal("0")
        if commit:
            instance.save()
        return instance


class MilkDistributionBuyerResultForm(forms.ModelForm):
    buyer_result_liters = forms.DecimalField(
        label="Returned quantity",
        required=False,
        min_value=Decimal("0"),
        decimal_places=2,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
    )
    return_qty_unit = forms.ChoiceField(
        required=False,
        choices=[("liters", "Liters"), ("kg", "Kg")],
        initial="liters",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    delivered_qty = forms.DecimalField(
        label="Delivered quantity",
        required=False,
        min_value=Decimal("0"),
        decimal_places=2,
        max_digits=10,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
    )
    delivered_qty_unit = forms.ChoiceField(
        required=False,
        choices=[("liters", "Liters"), ("kg", "Kg")],
        initial="liters",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    delivered_ts = forms.DecimalField(
        label="TS",
        required=False,
        min_value=Decimal("0"),
        decimal_places=2,
        max_digits=6,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
    )
    result_kind = forms.ChoiceField(
        required=False,
        choices=[("delivered", "Delivered"), ("returned", "Returned")],
        initial="delivered",
        widget=forms.HiddenInput(),
    )

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        self.notify_actor = kwargs.pop("notify_actor", None)
        super().__init__(*args, **kwargs)
        self.fields["returned_branch"].queryset = (
            get_allowed_branches_qs(user).order_by("name") if user else self.fields["returned_branch"].queryset.none()
        )
        self.fields["returned_branch"].required = False
        if self.instance.pk:
            if self.instance.buyer_result_quantity is not None:
                self.initial["buyer_result_liters"] = (
                    self.instance.buyer_result_quantity * MILK_LITER_FACTOR
                ).quantize(Decimal("0.01"))
                self.initial["return_qty_unit"] = "liters"
            if self.instance.delivered_quantity is not None:
                self.initial["delivered_qty"] = (
                    self.instance.delivered_quantity * MILK_LITER_FACTOR
                ).quantize(Decimal("0.01"))
                self.initial["delivered_qty_unit"] = "liters"
            if self.instance.delivered_ts is not None:
                self.initial["delivered_ts"] = self.instance.delivered_ts
            elif self.instance.ts:
                self.initial["delivered_ts"] = self.instance.ts
            if self.instance.buyer_result_quantity:
                self.initial["result_kind"] = "returned"
            else:
                self.initial["result_kind"] = "delivered"

    class Meta:
        model = MilkDistribution
        fields = ["status", "returned_branch"]
        widgets = {
            "status": forms.Select(attrs={"class": "form-select", "id": "id_status"}),
            "returned_branch": forms.Select(attrs={"class": "form-select", "id": "id_returned_branch"}),
        }

    def clean(self):
        data = super().clean()
        dispatched_kg = self.instance.kg or Decimal("0")
        dispatched_liters = self.instance.liters_equivalent

        result_kind = data.get("result_kind") or "delivered"
        delivered_qty = data.get("delivered_qty")
        delivered_unit = data.get("delivered_qty_unit") or "liters"
        return_qty = data.get("buyer_result_liters")
        return_unit = data.get("return_qty_unit") or "liters"
        delivered_ts = data.get("delivered_ts")
        if result_kind == "delivered":
            return_qty = None
            data["buyer_result_liters"] = None
            data["returned_branch"] = None
        else:
            delivered_qty = None
            data["delivered_qty"] = None
            data["delivered_ts"] = None
            delivered_ts = None

        if (self.instance.paid_amount or Decimal("0")) > 0:
            if result_kind != "delivered":
                raise forms.ValidationError("Cannot record a return after a payment has been received.")
            delivered_qty = None
            if self.instance.delivered_quantity:
                delivered_kg_locked = self.instance.delivered_quantity
            else:
                delivered_kg_locked = None
        else:
            delivered_kg_locked = None

        delivered_kg = None
        entered_delivered = False
        if delivered_qty is not None:
            delivered_kg = _qty_to_kg(
                delivered_qty, delivered_unit, dispatched_kg, dispatched_liters
            )
            if delivered_kg <= 0:
                delivered_kg = None
            else:
                entered_delivered = True
        if delivered_kg_locked is not None:
            delivered_kg = delivered_kg_locked

        return_kg = None
        if return_qty is not None:
            if return_unit == "kg":
                if return_qty > dispatched_kg:
                    self.add_error(
                        "buyer_result_liters",
                        f"Returned quantity cannot exceed dispatched quantity ({dispatched_kg} kg).",
                    )
                return_kg = return_qty.quantize(Decimal("0.01"))
            else:
                if return_qty.quantize(Decimal("0.01")) > dispatched_liters:
                    self.add_error(
                        "buyer_result_liters",
                        f"Returned quantity cannot exceed dispatched quantity ({dispatched_liters} L).",
                    )
                return_kg = _qty_to_kg(return_qty, "liters", dispatched_kg, dispatched_liters)
            if return_kg <= 0:
                return_kg = None
                data["buyer_result_liters"] = None

        if delivered_kg is None and return_kg:
            remainder = (dispatched_kg - return_kg).quantize(Decimal("0.01"))
            if remainder > 0:
                delivered_kg = remainder

        if delivered_kg is None and return_kg is None:
            raise forms.ValidationError("Enter a delivered quantity or a returned quantity.")

        combined = (delivered_kg or Decimal("0")) + (return_kg or Decimal("0"))
        if combined > dispatched_kg:
            raise forms.ValidationError("Delivered plus returned cannot exceed dispatched quantity.")

        if delivered_kg is not None:
            if delivered_ts is None:
                if entered_delivered:
                    self.add_error("delivered_ts", "Enter TS as measured at the buyer.")
                else:
                    delivered_ts = self.instance.ts or None
                    if delivered_ts is None:
                        self.add_error("delivered_ts", "Enter TS as measured at the buyer.")
            data["delivered_ts"] = delivered_ts
        else:
            data["delivered_ts"] = None

        if return_kg:
            if not data.get("returned_branch"):
                self.add_error("returned_branch", "Select the branch receiving the return.")
            data["status"] = (
                MilkDistribution.DistributionStatus.RETURN
                if delivered_kg is None
                else MilkDistribution.DistributionStatus.COMPLETED
            )
        else:
            data["returned_branch"] = None
            data["status"] = MilkDistribution.DistributionStatus.COMPLETED

        data["delivered_qty_kg"] = delivered_kg
        data["return_qty_kg"] = return_kg
        if not self.errors:
            self._apply_quantities(data)
        return data

    def _apply_quantities(self, data):
        self.instance.status = data.get("status") or self.instance.status
        self.instance.delivered_quantity = data.get("delivered_qty_kg")
        self.instance.buyer_result_quantity = data.get("return_qty_kg")
        self.instance.returned_branch = data.get("returned_branch")
        self.instance.delivered_ts = data.get("delivered_ts")

    def save(self, commit=True):
        prev = None
        if self.instance.pk:
            prev = MilkDistribution.objects.filter(pk=self.instance.pk).values(
                "status", "returned_branch_id", "buyer_result_quantity"
            ).first()
        if self.cleaned_data:
            self._apply_quantities(self.cleaned_data)
        instance = super().save(commit=False)
        if commit:
            instance.save()
            if self.notify_actor is not None:
                from django.db import transaction

                from dispatch.inbox import maybe_notify_buyer_return_to_branch

                dispatch_id = instance.pk
                captured_prev = prev
                actor = self.notify_actor
                transaction.on_commit(
                    lambda: maybe_notify_buyer_return_to_branch(
                        dispatch_id,
                        prev=captured_prev,
                        actor=actor,
                    )
                )
        return instance


class MilkDistributionStatusForm(forms.ModelForm):
    delivered_qty = forms.DecimalField(
        required=False,
        min_value=Decimal("0.01"),
        decimal_places=2,
        max_digits=10,
    )
    delivered_qty_unit = forms.ChoiceField(
        required=False,
        choices=[("liters", "Liters"), ("kg", "Kg")],
        initial="liters",
    )
    delivered_ts = forms.DecimalField(
        required=False,
        min_value=Decimal("0"),
        decimal_places=2,
        max_digits=6,
    )

    class Meta:
        model = MilkDistribution
        fields = ["status"]
        widgets = {
            "status": forms.Select(attrs={"class": "form-select form-select-sm"}),
        }

    def clean(self):
        data = super().clean()
        status = data.get("status")
        inst = self.instance
        if status == MilkDistribution.DistributionStatus.RETURN:
            if (
                not inst.returned_branch_id
                or inst.buyer_result_quantity is None
                or inst.buyer_result_quantity <= Decimal("0")
            ):
                raise forms.ValidationError(
                    "Return requires returned branch and quantity. Use Buyer result to complete return details."
                )
        if status == MilkDistribution.DistributionStatus.COMPLETED:
            qty = data.get("delivered_qty")
            unit = data.get("delivered_qty_unit") or "liters"
            paid = inst.paid_amount or Decimal("0")
            if paid > 0:
                data["delivered_qty_kg"] = None
            elif qty is not None:
                dispatched = inst.kg or Decimal("0")
                qty_kg = _qty_to_kg(qty, unit, dispatched, inst.liters_equivalent)
                if qty_kg > dispatched.quantize(Decimal("0.01")):
                    self.add_error(
                        "delivered_qty",
                        "Delivered quantity cannot exceed dispatched quantity.",
                    )
                data["delivered_qty_kg"] = qty_kg
            ts = data.get("delivered_ts")
            if paid <= 0 and qty is not None and ts is None:
                self.add_error("delivered_ts", "Enter TS as measured at the buyer.")
            elif ts is not None:
                data["delivered_ts"] = ts.quantize(Decimal("0.01"))
        return data

    def save(self, commit=True):
        instance = super().save(commit=False)
        if instance.status == MilkDistribution.DistributionStatus.COMPLETED:
            qty_kg = self.cleaned_data.get("delivered_qty_kg")
            if qty_kg is not None:
                instance.delivered_quantity = qty_kg
            elif instance.delivered_quantity is None:
                instance.delivered_quantity = instance.kg
            ts = self.cleaned_data.get("delivered_ts")
            if ts is not None:
                instance.delivered_ts = ts
        if commit:
            instance.save()
        return instance


class MilkDistributionBillingRateForm(forms.Form):
    use_period_rate = forms.BooleanField(required=False)
    unit_rate = forms.DecimalField(
        required=False,
        min_value=Decimal("0"),
        decimal_places=2,
        max_digits=10,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "step": "0.01",
                "min": "0",
                "inputmode": "decimal",
                "id": "dispatchRateQty",
            }
        ),
    )
    rate_unit = forms.ChoiceField(
        required=False,
        choices=Buyer.RateUnit.choices,
        widget=forms.Select(attrs={"class": "form-select", "id": "dispatchRateUnit"}),
    )

    def clean(self):
        data = super().clean()
        if data.get("use_period_rate"):
            return data
        if data.get("unit_rate") is None:
            self.add_error("unit_rate", "Enter the rate, or use the buyer period rate.")
        if not data.get("rate_unit"):
            self.add_error("rate_unit", "Select a rate unit.")
        return data


class MilkDistributionPaymentForm(forms.ModelForm):
    class Meta:
        model = MilkDistribution
        fields = ["payment_status"]
        widgets = {
            "payment_status": forms.Select(attrs={"class": "form-select"}),
        }


class BranchDispatchRespondForm(forms.Form):
    ACTION_COLLECT = "collect"
    ACTION_RETURN = "return"
    ACTION_DIVERT_BRANCH = "divert_branch"
    ACTION_DIVERT_BUYER = "divert_buyer"

    response_action = forms.ChoiceField(
        choices=[
            (ACTION_COLLECT, "Collect"),
            (ACTION_RETURN, "Return to source branch"),
            (ACTION_DIVERT_BRANCH, "Divert to another branch"),
            (ACTION_DIVERT_BUYER, "Divert to buyer"),
        ],
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Optional notes"}),
    )
    return_quantity = forms.DecimalField(
        required=False,
        min_value=Decimal("0.01"),
        decimal_places=2,
        max_digits=10,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "step": "0.01",
                "min": "0.01",
                "id": "id_return_quantity",
            }
        ),
    )
    return_quantity_unit = forms.ChoiceField(
        required=False,
        choices=[("kg", "Kg"), ("liters", "Liters")],
        initial="liters",
        widget=forms.HiddenInput(attrs={"id": "id_return_quantity_unit"}),
    )
    divert_branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    divert_buyer = forms.ModelChoiceField(
        queryset=Buyer.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, dispatch=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.dispatch = dispatch
        divert_qs = Branch.objects.all().order_by("name")
        if dispatch:
            if dispatch.branch_id:
                divert_qs = divert_qs.exclude(pk=dispatch.branch_id)
            if dispatch.destination_branch_id:
                divert_qs = divert_qs.exclude(pk=dispatch.destination_branch_id)
            # Default the visible quantity to full dispatch in liters.
            self.fields["return_quantity"].initial = dispatch.liters_equivalent
            self.fields["return_quantity_unit"].initial = "liters"
        self.fields["divert_branch"].queryset = divert_qs
        self.fields["divert_branch"].label_from_instance = lambda branch: branch.name
        if user:
            buyer_qs = filter_m2m_by_user_branches(Buyer.objects.all(), user).order_by("name")
            self.fields["divert_buyer"].queryset = buyer_qs

    def clean(self):
        data = super().clean()
        action = data.get("response_action")
        qty_actions = (
            self.ACTION_COLLECT,
            self.ACTION_RETURN,
            self.ACTION_DIVERT_BRANCH,
            self.ACTION_DIVERT_BUYER,
        )
        if action in qty_actions:
            qty = data.get("return_quantity")
            unit = data.get("return_quantity_unit") or "liters"
            labels = {
                self.ACTION_COLLECT: "collect",
                self.ACTION_RETURN: "return",
                self.ACTION_DIVERT_BRANCH: "divert",
                self.ACTION_DIVERT_BUYER: "divert",
            }
            if qty is None or qty <= Decimal("0"):
                self.add_error(
                    "return_quantity",
                    f"Enter the quantity to {labels.get(action, 'respond')}.",
                )
            elif self.dispatch:
                if unit == "liters":
                    qty_kg = (qty / MILK_LITER_FACTOR).quantize(Decimal("0.01"))
                    max_qty = self.dispatch.liters_equivalent
                    unit_label = "L"
                else:
                    qty_kg = qty.quantize(Decimal("0.01"))
                    max_qty = self.dispatch.kg
                    unit_label = "kg"
                if qty > max_qty:
                    self.add_error(
                        "return_quantity",
                        f"Quantity cannot exceed dispatched quantity ({max_qty} {unit_label}).",
                    )
                else:
                    data["return_quantity_kg"] = qty_kg

        if action == self.ACTION_DIVERT_BRANCH:
            branch = data.get("divert_branch")
            if not branch:
                self.add_error("divert_branch", "Select the branch to divert to.")
            elif self.dispatch:
                if self.dispatch.branch_id == branch.pk:
                    self.add_error("divert_branch", "Cannot divert to the source branch.")
                elif self.dispatch.destination_branch_id == branch.pk:
                    self.add_error("divert_branch", "Cannot divert to the current destination branch.")
        elif action == self.ACTION_DIVERT_BUYER:
            if not data.get("divert_buyer"):
                self.add_error("divert_buyer", "Select the buyer to divert to.")
        return data


class BranchDispatchReturnResolveForm(forms.Form):
    decision = forms.ChoiceField(
        choices=[
            ("accept", "Accept return and add stock to source branch"),
            ("reject", "Reject return"),
        ],
        widget=forms.RadioSelect(attrs={"class": "form-check-input"}),
    )
    notes = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Optional notes"}),
    )
