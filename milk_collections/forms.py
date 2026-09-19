import re
from decimal import Decimal, InvalidOperation
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from canmee_dairies.widgets import FlatpickrDateInput
from branches.utils import get_allowed_branches_qs
from masters.models import CollectionPoint, Farmer
from .models import CollectionSource, MilkCollection, MilkFactor


def parse_kg_expression(raw) -> Decimal:
    """Parse a quantity string: plain number, optional leading '=', additions/subtractions (e.g. 23+2, =10-3)."""
    s = str(raw or "").strip().replace(",", ".")
    if s.startswith("="):
        s = s[1:].strip()
    s = re.sub(r"\s+", "", s)
    if not s:
        raise ValueError("empty")
    if not re.fullmatch(r"[-0-9.+]+", s):
        raise ValueError("invalid")
    i = 0
    n = len(s)

    def read_number():
        nonlocal i
        start = i
        if i < n and s[i] == "-":
            i += 1
        saw_digit = False
        while i < n and (s[i].isdigit() or s[i] == "."):
            if s[i] != ".":
                saw_digit = True
            i += 1
        if not saw_digit:
            raise ValueError("num")
        try:
            return Decimal(s[start:i])
        except InvalidOperation as e:
            raise ValueError("decimal") from e

    total = read_number()
    while i < n:
        op = s[i]
        if op not in "+-":
            raise ValueError("op")
        i += 1
        num = read_number()
        total = total + num if op == "+" else total - num
    return total


class MilkCollectionForm(forms.ModelForm):
    kg = forms.CharField(
        label=MilkCollection._meta.get_field("kg").verbose_name,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "inputmode": "decimal",
                "autocomplete": "off",
            }
        ),
    )

    class Meta:
        model = MilkCollection
        fields = ["source", "date", "route", "collection_point", "farmer", "kg"]
        widgets = {
            "date": FlatpickrDateInput(),
            "route": forms.Select(attrs={"class": "form-select"}),
            "source": forms.RadioSelect(attrs={"class": "form-check-input"}),
            "collection_point": forms.Select(attrs={"class": "form-select milk-collection-point-select"}),
            "farmer": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["route"].required = False
        self.fields["collection_point"].required = False
        self.fields["farmer"].required = False
        farmer_qs = Farmer.objects.select_related("route").order_by("common_name", "full_name")
        point_qs = CollectionPoint.objects.select_related("route").order_by(
            "route__code", "number"
        )
        route_qs = self.fields["route"].queryset.order_by("code")
        if user is not None and not user.is_superuser:
            allowed_ids = list(get_allowed_branches_qs(user).values_list("id", flat=True))
            route_qs = route_qs.filter(branch_id__in=allowed_ids) if allowed_ids else route_qs.none()
            point_qs = point_qs.filter(route__branch_id__in=allowed_ids) if allowed_ids else point_qs.none()
            farmer_qs = farmer_qs.filter(branch_id__in=allowed_ids) if allowed_ids else farmer_qs.none()
        self.fields["route"].queryset = route_qs
        self.fields["farmer"].queryset = farmer_qs
        self.fields["farmer"].label_from_instance = (
            lambda obj: (obj.common_name or obj.full_name or "").strip() or "—"
        )
        self.fields["collection_point"].queryset = point_qs
        if not self.instance.pk and not self.data:
            self.initial.setdefault("date", timezone.localdate())

    def clean_kg(self):
        raw = self.cleaned_data.get("kg")
        try:
            val = parse_kg_expression(raw)
        except ValueError:
            raise ValidationError(
                "Enter a valid quantity (e.g. 12.5, 10+2, or =15-3).",
                code="invalid",
            ) from None
        if val < 0:
            raise ValidationError("Quantity cannot be negative.", code="negative")
        return val.quantize(Decimal("0.01"))

    def clean(self):
        data = super().clean()
        src = data.get("source") or CollectionSource.POINT
        pt = data.get("collection_point")
        fr = data.get("farmer")

        if src == CollectionSource.POINT:
            if not pt:
                self.add_error("collection_point", "Select a collection point.")
            else:
                data["route"] = pt.route
            data["farmer"] = None
        else:
            data["collection_point"] = None
            if not fr:
                self.add_error("farmer", "Select a farmer.")
                if not data.get("route"):
                    self.add_error("route", "Select a route.")
            else:
                if fr.route_id:
                    data["route"] = fr.route
                elif not data.get("route"):
                    self.add_error(
                        "route",
                        "Select a route (this farmer has no route assigned on their record).",
                    )

        # Let CreateView handle duplicate date+route+point / date+route+farmer via 409 + confirm;
        # ModelForm would otherwise fail is_valid() here with validate_constraints() before form_valid runs.
        if (
            not self.instance.pk
            and src == CollectionSource.POINT
            and data.get("collection_point")
            and not self.errors
        ):
            self._validate_constraints = False
        if (
            not self.instance.pk
            and src == CollectionSource.FARMER
            and data.get("farmer")
            and data.get("route")
            and not self.errors
        ):
            self._validate_constraints = False

        return data


class MilkFactorForm(forms.ModelForm):
    alcohol_result = forms.ChoiceField(
        label="Alcohol",
        choices=(("negative", "Negative"), ("positive", "Positive")),
        widget=forms.Select(attrs={"class": "form-select"}),
        required=True,
    )

    class Meta:
        model = MilkFactor
        fields = [
            "source",
            "date",
            "route",
            "collection_point",
            "farmer",
            "fat",
            "snf",
            "lr",
            "alcohol",
            "acidity",
            "kq",
        ]
        widgets = {
            "date": FlatpickrDateInput(),
            "route": forms.Select(attrs={"class": "form-select"}),
            "source": forms.RadioSelect(attrs={"class": "form-check-input"}),
            "collection_point": forms.Select(attrs={"class": "form-select milk-collection-point-select"}),
            "farmer": forms.Select(attrs={"class": "form-select"}),
            "fat": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "snf": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "lr": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "alcohol": forms.HiddenInput(),
            "acidity": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "kq": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["route"].required = False
        self.fields["collection_point"].required = False
        self.fields["farmer"].required = False
        farmer_qs = Farmer.objects.select_related("route").order_by("common_name", "full_name")
        point_qs = CollectionPoint.objects.select_related("route").order_by(
            "route__code", "number"
        )
        route_qs = self.fields["route"].queryset.order_by("code")
        if user is not None and not user.is_superuser:
            allowed_ids = list(get_allowed_branches_qs(user).values_list("id", flat=True))
            route_qs = route_qs.filter(branch_id__in=allowed_ids) if allowed_ids else route_qs.none()
            point_qs = point_qs.filter(route__branch_id__in=allowed_ids) if allowed_ids else point_qs.none()
            farmer_qs = farmer_qs.filter(branch_id__in=allowed_ids) if allowed_ids else farmer_qs.none()
        self.fields["route"].queryset = route_qs
        self.fields["farmer"].queryset = farmer_qs
        self.fields["farmer"].label_from_instance = (
            lambda obj: (obj.common_name or obj.full_name or "").strip() or "—"
        )
        self.fields["collection_point"].queryset = point_qs
        if not self.instance.pk and not self.data:
            self.initial.setdefault("date", timezone.localdate())
            self.initial.setdefault("source", CollectionSource.POINT)
        # Persist alcohol in DB as numeric, expose pass/fail in UI.
        current_alcohol = self.initial.get("alcohol", getattr(self.instance, "alcohol", Decimal("0")))
        try:
            current_alcohol = Decimal(str(current_alcohol))
        except Exception:
            current_alcohol = Decimal("0")
        self.fields["alcohol_result"].initial = "positive" if current_alcohol > 0 else "negative"

    def clean(self):
        data = super().clean()
        src = data.get("source") or CollectionSource.POINT
        pt = data.get("collection_point")
        fr = data.get("farmer")

        if src == CollectionSource.POINT:
            if not pt:
                self.add_error("collection_point", "Select a collection point.")
            else:
                data["route"] = pt.route
            data["farmer"] = None
        else:
            data["collection_point"] = None
            if not fr:
                self.add_error("farmer", "Select a farmer.")
                if not data.get("route"):
                    self.add_error("route", "Select a route.")
            else:
                if fr.route_id:
                    data["route"] = fr.route
                elif not data.get("route"):
                    self.add_error(
                        "route",
                        "Select a route (this farmer has no route assigned on their record).",
                    )

        data["alcohol"] = Decimal("1.00") if data.get("alcohol_result") == "positive" else Decimal("0.00")
        return data
