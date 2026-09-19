from django import forms

from branches.models import Branch
from branches.utils import get_allowed_branches_qs

from .models import (
    Asset,
    AssetCategory,
    AssetLocation,
    AssetModuleSettings,
    AssetType,
    AssetVerification,
    DepreciationPolicy,
)
from .services import next_asset_tag

_MONEY_INPUT_ATTRS = {
    "class": "form-control js-money-input",
    "inputmode": "decimal",
    "autocomplete": "off",
}


def _querydict_without_money_commas(data, field_names):
    if data is None:
        return data
    copied = data.copy()
    for name in field_names:
        raw = copied.get(name)
        if raw in (None, ""):
            continue
        copied[name] = str(raw).replace(",", "").strip()
    return copied


class AssetTypeForm(forms.ModelForm):
    class Meta:
        model = AssetType
        fields = ["name", "code", "sort_order", "is_active"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sort_order"].required = False
        self.fields["sort_order"].initial = self.fields["sort_order"].initial or 0


class AssetTypeDeleteForm(forms.Form):
    replacement = forms.ModelChoiceField(
        queryset=AssetType.objects.none(),
        required=False,
        help_text="Existing assets and categories will be moved to this type.",
    )

    def __init__(self, *args, old_type=None, require_replacement=False, **kwargs):
        super().__init__(*args, **kwargs)
        qs = AssetType.objects.all()
        if old_type is not None:
            qs = qs.exclude(pk=old_type.pk)
        self.fields["replacement"].queryset = qs.order_by("sort_order", "name")
        self.fields["replacement"].required = require_replacement
        self.fields["replacement"].error_messages["required"] = (
            "Choose another type for existing assets and categories."
        )
        if require_replacement:
            self.fields["replacement"].empty_label = "Choose a type"


class AssetLocationDeleteForm(forms.Form):
    replacement = forms.ModelChoiceField(
        queryset=AssetLocation.objects.none(),
        required=False,
        help_text="Existing assets and sub-locations will be moved to this location.",
    )

    def __init__(self, *args, old_location=None, require_replacement=False, exclude_ids=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = AssetLocation.objects.all()
        if old_location is not None:
            qs = qs.exclude(pk=old_location.pk)
        if exclude_ids:
            qs = qs.exclude(pk__in=list(exclude_ids))
        self.fields["replacement"].queryset = qs.select_related("branch").order_by("name")
        self.fields["replacement"].required = require_replacement
        self.fields["replacement"].error_messages["required"] = (
            "Choose another location for existing assets and sub-locations."
        )
        if require_replacement:
            self.fields["replacement"].empty_label = "Choose a location"


class AssetCategoryForm(forms.ModelForm):
    class Meta:
        model = AssetCategory
        fields = ["asset_type", "name", "code", "parent", "description", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["asset_type"].queryset = AssetType.objects.filter(is_active=True).order_by(
            "sort_order", "name"
        )
        if self.instance.pk and self.instance.asset_type_id:
            self.fields["asset_type"].queryset = (
                AssetType.objects.filter(is_active=True)
                | AssetType.objects.filter(pk=self.instance.asset_type_id)
            ).distinct().order_by("sort_order", "name")
        qs = AssetCategory.objects.filter(is_active=True)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        type_id = None
        if self.data.get("asset_type"):
            type_id = self.data.get("asset_type")
        elif self.instance.pk:
            type_id = self.instance.asset_type_id
        if type_id:
            qs = qs.filter(asset_type_id=type_id)
        self.fields["parent"].queryset = qs
        self.fields["parent"].required = False


class AssetLocationForm(forms.ModelForm):
    class Meta:
        model = AssetLocation
        fields = ["name", "code", "branch", "parent", "description", "is_active"]
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["branch"].queryset = get_allowed_branches_qs(user).order_by("name")
        self.fields["branch"].required = False
        self.fields["branch"].empty_label = "Head Office"
        qs = AssetLocation.objects.filter(is_active=True)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        self.fields["parent"].queryset = qs
        self.fields["parent"].required = False


class AssetForm(forms.ModelForm):
    class Meta:
        model = Asset
        fields = [
            "asset_tag",
            "name",
            "description",
            "asset_type",
            "category",
            "branch",
            "location",
            "status",
            "condition",
            "serial_number",
            "manufacturer",
            "model_number",
            "purchase_date",
            "purchase_cost",
            "current_value",
            "supplier_name",
            "warranty_expiry",
            "depreciation_policy",
            "useful_life_months",
            "salvage_value",
            "custodian",
            "notes",
        ]
        widgets = {
            "purchase_date": forms.DateInput(
                format="%Y-%m-%d",
                attrs={
                    "type": "text",
                    "class": "form-control js-datepicker",
                    "placeholder": "Select date",
                    "autocomplete": "off",
                },
            ),
            "warranty_expiry": forms.DateInput(
                format="%Y-%m-%d",
                attrs={
                    "type": "text",
                    "class": "form-control js-datepicker",
                    "placeholder": "Select date",
                    "autocomplete": "off",
                },
            ),
            "description": forms.Textarea(attrs={"rows": 2}),
            "notes": forms.Textarea(attrs={"rows": 2}),
            "purchase_cost": forms.TextInput(attrs=_MONEY_INPUT_ATTRS),
            "current_value": forms.TextInput(attrs=_MONEY_INPUT_ATTRS),
            "salvage_value": forms.TextInput(attrs=_MONEY_INPUT_ATTRS),
        }

    def __init__(self, *args, user=None, **kwargs):
        money_fields = ("purchase_cost", "current_value", "salvage_value")
        if args:
            args = (_querydict_without_money_commas(args[0], money_fields),) + args[1:]
        elif kwargs.get("data") is not None:
            kwargs["data"] = _querydict_without_money_commas(kwargs["data"], money_fields)
        super().__init__(*args, **kwargs)
        type_qs = AssetType.objects.filter(is_active=True)
        if self.instance.pk and self.instance.asset_type_id:
            type_qs = AssetType.objects.filter(is_active=True) | AssetType.objects.filter(
                pk=self.instance.asset_type_id
            )
        self.fields["asset_type"].queryset = type_qs.distinct().order_by("sort_order", "name")
        if user is not None:
            self.fields["branch"].queryset = get_allowed_branches_qs(user).order_by("name")
        self.fields["branch"].required = False
        self.fields["branch"].empty_label = "Head Office"
        self.fields["location"].queryset = AssetLocation.objects.filter(is_active=True)
        self.fields["location"].required = False
        cat_qs = AssetCategory.objects.filter(is_active=True)
        type_id = None
        if self.data.get("asset_type"):
            type_id = self.data.get("asset_type")
        elif self.instance.pk:
            type_id = self.instance.asset_type_id
        if type_id:
            cat_qs = cat_qs.filter(asset_type_id=type_id)
        self.fields["category"].queryset = cat_qs.select_related("asset_type")
        self.fields["depreciation_policy"].queryset = DepreciationPolicy.objects.filter(
            is_active=True
        )
        self.fields["depreciation_policy"].required = False
        self.fields["custodian"].required = False
        if not self.instance.pk and not (self.data.get("asset_tag") if self.data else None):
            self.fields["asset_tag"].initial = next_asset_tag()
        if not self.instance.pk and not self.data:
            default_type = AssetType.objects.filter(code="fixed", is_active=True).first()
            if default_type:
                self.fields["asset_type"].initial = default_type.pk

    def clean(self):
        cleaned = super().clean()
        asset_type = cleaned.get("asset_type")
        category = cleaned.get("category")
        if asset_type and category and category.asset_type_id != asset_type.pk:
            self.add_error("category", "Category must belong to the selected type.")
        return cleaned


class AssetVerificationForm(forms.Form):
    result = forms.ChoiceField(choices=AssetVerification.Result.choices)
    condition = forms.ChoiceField(choices=Asset.Condition.choices, required=False)
    location = forms.ModelChoiceField(
        queryset=AssetLocation.objects.filter(is_active=True), required=False
    )
    assessed_value = forms.DecimalField(
        required=False,
        min_value=0,
        decimal_places=2,
        widget=forms.TextInput(attrs={**_MONEY_INPUT_ATTRS, "class": "form-control form-control-sm js-money-input"}),
    )
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        if args:
            args = (_querydict_without_money_commas(args[0], ("assessed_value",)),) + args[1:]
        elif kwargs.get("data") is not None:
            kwargs["data"] = _querydict_without_money_commas(kwargs["data"], ("assessed_value",))
        super().__init__(*args, **kwargs)


class AssetValuationForm(forms.Form):
    new_value = forms.DecimalField(
        min_value=0,
        decimal_places=2,
        widget=forms.TextInput(attrs={**_MONEY_INPUT_ATTRS, "class": "form-control form-control-sm js-money-input"}),
    )
    reason = forms.CharField(required=False, max_length=255)

    def __init__(self, *args, **kwargs):
        if args:
            args = (_querydict_without_money_commas(args[0], ("new_value",)),) + args[1:]
        elif kwargs.get("data") is not None:
            kwargs["data"] = _querydict_without_money_commas(kwargs["data"], ("new_value",))
        super().__init__(*args, **kwargs)


class AssetTransferForm(forms.Form):
    to_branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(), required=False, empty_label="Head Office"
    )
    to_location = forms.ModelChoiceField(
        queryset=AssetLocation.objects.filter(is_active=True), required=False
    )
    note = forms.CharField(required=False, max_length=255)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["to_branch"].queryset = get_allowed_branches_qs(user).order_by(
                "name"
            )


class DepreciationPolicyForm(forms.ModelForm):
    class Meta:
        model = DepreciationPolicy
        fields = [
            "name",
            "method",
            "useful_life_months",
            "residual_percent",
            "annual_rate_percent",
            "is_active",
            "notes",
        ]
        widgets = {"notes": forms.Textarea(attrs={"rows": 2})}


class AssetModuleSettingsForm(forms.ModelForm):
    class Meta:
        model = AssetModuleSettings
        fields = ["enable_depreciation", "default_label_prefix"]


class DepreciationRunForm(forms.Form):
    period_month = forms.CharField(
        help_text="Month to run depreciation for (YYYY-MM).",
    )

    def clean_period_month(self):
        from datetime import date

        raw = (self.cleaned_data["period_month"] or "").strip()
        try:
            year, month = raw.split("-")[:2]
            return date(int(year), int(month), 1)
        except (ValueError, TypeError) as exc:
            raise forms.ValidationError("Enter a valid month (YYYY-MM).") from exc


class LabelBatchForm(forms.Form):
    asset_ids = forms.CharField(
        widget=forms.HiddenInput(),
        required=False,
        help_text="Comma-separated asset IDs",
    )
    note = forms.CharField(required=False, max_length=255)

    def clean_asset_ids(self):
        raw = (self.cleaned_data.get("asset_ids") or "").strip()
        if not raw:
            return []
        ids = []
        for part in raw.split(","):
            part = part.strip()
            if part.isdigit():
                ids.append(int(part))
        return ids
