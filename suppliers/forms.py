from decimal import Decimal

from django import forms
from django.core.exceptions import ValidationError
from django.forms import BaseModelFormSet, formset_factory, modelformset_factory
from django.utils import timezone

from canmee_dairies.widgets import FlatpickrDateInput
from branches.utils import (
    filter_m2m_by_branch,
    filter_m2m_by_user_branches,
    get_allowed_branches_qs,
    get_default_branch_id,
    queryset_with_required_pk,
    resolve_form_branch_id,
)
from branches.models import Branch
from masters.models import CollectionPoint, Farmer
from stock_management.models import BranchStock, Product

from .models import (
    ConsumptionSettlement,
    FarmerGoodsIssue,
    FarmerGoodsModuleSettings,
    GRN,
    GRNItem,
    InventoryUsage,
    RawMilkSupplierCollection,
    Supplier,
)


class SupplierForm(forms.ModelForm):
    branches = forms.ModelMultipleChoiceField(
        queryset=Branch.objects.none(),
        required=True,
        widget=forms.SelectMultiple(
            attrs={
                "class": "form-select js-supplier-branches-select",
                "data-placeholder": "Select one or more branches",
            }
        ),
        label="Branches",
    )

    class Meta:
        model = Supplier
        fields = ["name", "category", "contact_number", "address", "email", "status", "branches"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control"}),
            "category": forms.Select(attrs={"class": "form-select"}),
            "contact_number": forms.TextInput(attrs={"class": "form-control"}),
            "address": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "status": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        branch_qs = get_allowed_branches_qs(user).order_by("code", "name") if user else Branch.objects.order_by("code", "name")
        self.fields["branches"].queryset = branch_qs
        self.fields["branches"].label_from_instance = lambda obj: obj.name
        if self.instance.pk:
            self.fields["branches"].initial = self.instance.branches.all()
        elif user and not self.data:
            default_branch_id = get_default_branch_id(user)
            if default_branch_id:
                self.initial.setdefault("branches", [default_branch_id])
            elif not user.is_superuser:
                branch_ids = list(branch_qs.values_list("id", flat=True))
                if branch_ids:
                    self.initial.setdefault("branches", branch_ids)

    def clean_branches(self):
        branches = self.cleaned_data.get("branches")
        if not branches:
            raise ValidationError("Select at least one branch.")
        if self.user and not self.user.is_superuser:
            allowed = set(get_allowed_branches_qs(self.user).values_list("id", flat=True))
            for branch in branches:
                if branch.id not in allowed:
                    raise ValidationError("You can only assign suppliers to your branches.")
        return branches

    def save(self, commit=True):
        instance = super().save(commit=commit)
        if commit:
            instance.branches.set(self.cleaned_data.get("branches", []))
        return instance


class GRNForm(forms.ModelForm):
    class Meta:
        model = GRN
        fields = [
            "supplier",
            "branch",
            "grn_type",
            "date",
            "discount_scope",
            "document_discount_percent",
            "document_discount_amount",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "supplier": forms.Select(attrs={"class": "form-select"}),
            "branch": forms.Select(attrs={"class": "form-select no-global-select2"}),
            "grn_type": forms.Select(attrs={"class": "form-select"}),
            "discount_scope": forms.Select(attrs={"class": "form-select"}),
            "document_discount_percent": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0", "max": "100"}
            ),
            "document_discount_amount": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0"}
            ),
        }

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        branch_id = resolve_form_branch_id(self)
        qs = filter_m2m_by_user_branches(
            Supplier.objects.filter(status=Supplier.Status.ACTIVE)
            .exclude(category=Supplier.Category.RAW_MILK_SUPPLIER),
            user,
        ).order_by("name")
        if branch_id:
            qs = filter_m2m_by_branch(qs, branch_id)
        required_supplier_id = self.instance.supplier_id if self.instance.pk else None
        if required_supplier_id:
            qs = queryset_with_required_pk(qs, required_supplier_id)
        self.fields["supplier"].queryset = qs.order_by("name")
        from branches.utils import get_allowed_branches_qs

        branches = get_allowed_branches_qs(user).order_by("name") if user else None
        if branches is not None:
            self.fields["branch"].queryset = branches
        self.fields["branch"].required = True
        self.fields["branch"].label = "Branch"
        self.fields["document_discount_percent"].label = "Total discount (%)"
        self.fields["document_discount_amount"].label = "Total discount (amount)"
        self.fields["discount_scope"].label = "Discount applies to"
        # Stock corrections are created only via Correct negative stock.
        self.fields["grn_type"].choices = [
            choice
            for choice in GRN.GRNType.choices
            if choice[0] != GRN.GRNType.STOCK_CORRECTION
        ]

    def clean_supplier(self):
        supplier = self.cleaned_data.get("supplier")
        if supplier and supplier.category == Supplier.Category.RAW_MILK_SUPPLIER:
            raise ValidationError(
                "GRN is not allowed for raw milk suppliers; use a corporate or farmer goods supplier."
            )
        return supplier

    def clean(self):
        data = super().clean()
        if not data.get("branch"):
            self.add_error("branch", "Please select a branch.")
        supplier = data.get("supplier")
        branch = data.get("branch")
        if supplier and branch and not supplier.branches.filter(pk=branch.pk).exists():
            self.add_error("supplier", "This supplier is not linked to the selected branch.")
        scope = data.get("discount_scope")
        pct = data.get("document_discount_percent") or Decimal("0")
        amt = data.get("document_discount_amount") or Decimal("0")
        if scope == GRN.DiscountScope.DOCUMENT:
            if pct > Decimal("0") and amt > Decimal("0"):
                raise ValidationError(
                    "For a total-level discount, use either a percentage or a flat amount, not both."
                )
        return data


class GRNItemForm(forms.ModelForm):
    class Meta:
        model = GRNItem
        fields = [
            "product",
            "quantity",
            "free_quantity",
            "unit_price",
            "issuing_price",
            "line_discount_percent",
            "line_discount_amount",
        ]
        widgets = {
            "product": forms.Select(
                attrs={
                    "class": "form-select form-select-sm grn-product-select js-grn-product-select modern-select-input"
                }
            ),
            "quantity": forms.NumberInput(
                attrs={"class": "form-control form-control-sm grn-qty text-end", "step": "0.01", "min": "0"}
            ),
            "free_quantity": forms.NumberInput(
                attrs={"class": "form-control form-control-sm grn-free-qty text-end", "step": "0.01", "min": "0"}
            ),
            "unit_price": forms.NumberInput(
                attrs={"class": "form-control form-control-sm grn-rate text-end", "step": "0.01", "min": "0"}
            ),
            "issuing_price": forms.NumberInput(
                attrs={"class": "form-control form-control-sm grn-issue-price text-end", "step": "0.01", "min": "0"}
            ),
            "line_discount_percent": forms.NumberInput(
                attrs={
                    "class": "form-control form-control-sm grn-line-pct text-end",
                    "step": "0.01",
                    "min": "0",
                    "max": "100",
                }
            ),
            "line_discount_amount": forms.NumberInput(
                attrs={"class": "form-control form-control-sm grn-line-amt text-end", "step": "0.01", "min": "0"}
            ),
        }
        labels = {
            "unit_price": "Rate",
            "issuing_price": "Price",
            "free_quantity": "Free qty",
            "line_discount_percent": "Line disc. %",
            "line_discount_amount": "Line disc. amt",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(status=Product.Status.ACTIVE).order_by("name")
        self.fields["product"].required = False
        self.fields["quantity"].required = False
        self.fields["free_quantity"].required = False
        self.fields["issuing_price"].required = False
        self.fields["line_discount_percent"].required = False
        self.fields["line_discount_amount"].required = False

    def clean(self):
        data = super().clean()
        if data.get("DELETE"):
            return data
        product = data.get("product")
        if not product:
            has_values = False
            for key in ("quantity", "unit_price", "free_quantity", "issuing_price", "line_discount_percent", "line_discount_amount"):
                v = data.get(key)
                if v is not None and v != "" and v != 0:
                    has_values = True
                    break
            if has_values:
                raise ValidationError("Select a product, or clear quantities and rates on unused rows.")
            return data
        qty = data.get("quantity")
        if qty is None or qty <= 0:
            raise ValidationError({"quantity": "Enter a quantity greater than zero."})
        return data


class BaseGRNItemFormSet(BaseModelFormSet):
    def _construct_form(self, i, **kwargs):
        # Existing DB rows in edit mode must be validated even if untouched.
        # Only extra (new blank) rows should be allowed to stay empty.
        kwargs.setdefault("empty_permitted", i >= self.initial_form_count())
        return super()._construct_form(i, **kwargs)

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        filled = 0
        for form in self.forms:
            if not form.cleaned_data or form.cleaned_data.get("DELETE"):
                continue
            if form.cleaned_data.get("product"):
                filled += 1
        if filled < 1:
            raise ValidationError("Add at least one product line.")


GRNItemFormSet = modelformset_factory(
    GRNItem,
    form=GRNItemForm,
    formset=BaseGRNItemFormSet,
    extra=1,
    min_num=0,
    validate_min=False,
    can_delete=True,
)


class FarmerGoodsIssueForm(forms.ModelForm):
    issue_to_branch = forms.ModelChoiceField(queryset=Branch.objects.none(), required=False)
    issue_to_collection_point = forms.ModelChoiceField(queryset=CollectionPoint.objects.none(), required=False)
    issue_to_farmer = forms.ModelChoiceField(queryset=Farmer.objects.none(), required=False)

    class Meta:
        model = FarmerGoodsIssue
        fields = ["product", "quantity", "issue_to_type", "issue_to_id"]

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].widget.attrs.update({"class": "form-select modern-select-input"})
        self.fields["quantity"].widget.attrs.update(
            {"class": "form-control", "step": "0.01", "min": "0.01", "inputmode": "decimal"}
        )
        self.fields["quantity"].min_value = Decimal("0.01")
        self.fields["issue_to_type"].widget.attrs.update({"class": "form-select"})
        self.fields["issue_to_id"].widget.attrs.update({"class": "form-control", "inputmode": "numeric"})
        self.fields["issue_to_id"].help_text = "Use branch / point / farmer record ID based on issue type."
        allowed_branches = get_allowed_branches_qs(user).order_by("name") if user else Branch.objects.all().order_by("name")
        self.fields["issue_to_branch"].queryset = allowed_branches
        self.fields["issue_to_branch"].widget.attrs.update({"class": "form-select modern-select-input"})
        points_qs = CollectionPoint.objects.select_related("route", "route__branch")
        farmers_qs = Farmer.objects.select_related("branch", "collection_point")
        if user and not user.is_superuser:
            branch_ids = list(allowed_branches.values_list("id", flat=True))
            points_qs = points_qs.filter(route__branch_id__in=branch_ids)
            farmers_qs = farmers_qs.filter(branch_id__in=branch_ids)
        self.fields["issue_to_collection_point"].queryset = points_qs.order_by("route__code", "number")
        self.fields["issue_to_collection_point"].widget.attrs.update({"class": "form-select modern-select-input"})
        self.fields["issue_to_farmer"].queryset = farmers_qs.order_by("common_name", "full_name")
        self.fields["issue_to_farmer"].widget.attrs.update({"class": "form-select modern-select-input"})

    def clean_quantity(self):
        qty = self.cleaned_data.get("quantity")
        if qty is None or qty <= Decimal("0"):
            raise ValidationError("Quantity must be greater than zero. Negative stock is not allowed.")
        return qty

    def clean(self):
        cleaned = super().clean()
        issue_to_type = cleaned.get("issue_to_type")
        target_field = None
        if issue_to_type == "branch":
            target_field = "issue_to_branch"
        elif issue_to_type == "collection_point":
            target_field = "issue_to_collection_point"
        elif issue_to_type == "farmer":
            target_field = "issue_to_farmer"
        if not target_field:
            self.add_error("issue_to_type", "Select where to issue.")
            return cleaned
        target_obj = cleaned.get(target_field)
        if not target_obj:
            self.add_error(target_field, "Select a destination.")
            return cleaned
        cleaned["issue_to_id"] = target_obj.pk
        return cleaned


class FarmerGoodsIssueHeaderForm(forms.Form):
    date = forms.DateField(
        label="Date",
        widget=FlatpickrDateInput(),
        required=True,
    )
    from_branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(),
        required=False,
        label="From branch",
    )
    issue_to_type = forms.ChoiceField(choices=[], required=False, widget=forms.HiddenInput())
    issue_to_id = forms.IntegerField(required=False, widget=forms.HiddenInput())
    issue_to_branch = forms.ModelChoiceField(queryset=Branch.objects.none(), required=False)
    issue_to_collection_point = forms.ModelChoiceField(queryset=CollectionPoint.objects.none(), required=False)
    issue_to_farmer = forms.ModelChoiceField(queryset=Farmer.objects.none(), required=False)

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.data:
            self.initial.setdefault("date", timezone.localdate())
        allowed_branches = get_allowed_branches_qs(user).order_by("name") if user else Branch.objects.all().order_by("name")
        self.fields["from_branch"].queryset = allowed_branches
        branch_count = allowed_branches.count()
        if branch_count == 0:
            self.fields["from_branch"].required = False
        elif branch_count == 1:
            sole = allowed_branches.first()
            self.fields["from_branch"].widget = forms.HiddenInput()
            if sole:
                self.fields["from_branch"].initial = sole.pk
        else:
            self.fields["from_branch"].widget.attrs.update({"class": "form-select"})
        self.fields["issue_to_branch"].widget.attrs.update({"class": "form-select modern-select-input"})
        points_qs = CollectionPoint.objects.select_related("route", "route__branch")
        farmers_qs = Farmer.objects.select_related("branch", "collection_point")
        if user and not user.is_superuser:
            branch_ids = list(allowed_branches.values_list("id", flat=True))
            points_qs = points_qs.filter(route__branch_id__in=branch_ids)
            farmers_qs = farmers_qs.filter(branch_id__in=branch_ids)
        scope_pk = self._scope_branch_pk()
        if scope_pk is not None:
            points_qs = points_qs.filter(route__branch_id=scope_pk)
            farmers_qs = farmers_qs.filter(branch_id=scope_pk)
            # Same idea as StockTransferNoteForm: any allowed branch except the source.
            self.fields["issue_to_branch"].queryset = allowed_branches.exclude(pk=scope_pk)
        else:
            points_qs = points_qs.none()
            farmers_qs = farmers_qs.none()
            self.fields["issue_to_branch"].queryset = Branch.objects.none()
        self.fields["issue_to_collection_point"].queryset = points_qs.order_by("route__code", "number")
        self.fields["issue_to_collection_point"].empty_label = "Select collection point"
        self.fields["issue_to_collection_point"].widget.attrs.update({"class": "form-select modern-select-input"})
        self.fields["issue_to_farmer"].queryset = farmers_qs.order_by("common_name", "full_name")
        self.fields["issue_to_farmer"].empty_label = "Select farmer"
        self.fields["issue_to_farmer"].widget.attrs.update(
            {"class": "form-select modern-select-input js-issue-farmer-select"}
        )
        self.fields["issue_to_branch"].empty_label = "Select branch"
        self.fields["issue_to_type"].choices = [
            ("", "---------"),
        ] + list(FarmerGoodsIssue.IssueToType.choices)

    def _scope_branch_pk(self):
        """Resolved from-branch pk from POST or initial; must be in allowed queryset."""
        allowed = self.fields["from_branch"].queryset
        pk = None
        if self.data:
            raw = self.data.get(self.add_prefix("from_branch"))
            if raw not in (None, ""):
                try:
                    pk = int(raw)
                except (TypeError, ValueError):
                    pk = None
        if pk is None:
            init_fb = self.initial.get("from_branch")
            if init_fb is not None:
                pk = init_fb.pk if hasattr(init_fb, "pk") else int(init_fb)
        if pk is not None and not allowed.filter(pk=pk).exists():
            return None
        return pk

    def clean(self):
        cleaned = super().clean()
        allowed_qs = self.fields["from_branch"].queryset
        if not allowed_qs.exists():
            raise ValidationError("No branch is available for your account.")
        if cleaned.get("from_branch") is None:
            self.add_error("from_branch", "Select a branch.")
            return cleaned
        fb = cleaned["from_branch"]
        issue_to_type = (cleaned.get("issue_to_type") or "").strip()
        if not issue_to_type or issue_to_type not in (
            "branch",
            "collection_point",
            "farmer",
        ):
            self.add_error("issue_to_type", "Select a destination: Branch, Collection Point, or Farmer.")
            return cleaned
        target_field = None
        if issue_to_type == "branch":
            target_field = "issue_to_branch"
        elif issue_to_type == "collection_point":
            target_field = "issue_to_collection_point"
        elif issue_to_type == "farmer":
            target_field = "issue_to_farmer"
        target_obj = cleaned.get(target_field)
        if not target_obj:
            self.add_error(target_field, "Select a destination.")
            return cleaned
        if issue_to_type == "collection_point":
            rid = getattr(getattr(target_obj, "route", None), "branch_id", None)
            if rid != fb.pk:
                self.add_error(
                    "issue_to_collection_point",
                    "Choose a collection point that belongs to the selected branch.",
                )
                return cleaned
        elif issue_to_type == "farmer":
            if getattr(target_obj, "branch_id", None) != fb.pk:
                self.add_error(
                    "issue_to_farmer",
                    "Choose a farmer that belongs to the selected branch.",
                )
                return cleaned
        elif issue_to_type == "branch":
            if target_obj.pk == fb.pk:
                self.add_error(
                    "issue_to_branch",
                    "Issue-to branch must be different from the from branch.",
                )
                return cleaned
        cleaned["issue_to_id"] = target_obj.pk
        return cleaned


class FarmerGoodsIssueLineEntryForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.none(), required=False)
    quantity = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        required=False,
        min_value=Decimal("0.01"),
    )

    def __init__(self, *args, **kwargs):
        from_branch_id = kwargs.pop("from_branch_id", None)
        super().__init__(*args, **kwargs)

        # Limit product options to “available products” for the selected from-branch.
        # Availability is based on stock_management.BranchStock.quantity > 0.
        if from_branch_id is None and self.data:
            from_branch_id = self.data.get("from_branch") or None

        try:
            from_branch_id = int(from_branch_id) if from_branch_id not in (None, "") else None
        except (TypeError, ValueError):
            from_branch_id = None

        selected_product_id = None
        if self.data:
            raw_selected = self.data.get(self.add_prefix("product")) or self.data.get("product") or None
            try:
                selected_product_id = int(raw_selected) if raw_selected not in (None, "") else None
            except (TypeError, ValueError):
                selected_product_id = None
        if selected_product_id is None and self.initial:
            raw_init = self.initial.get("product")
            try:
                selected_product_id = int(raw_init) if raw_init not in (None, "") else None
            except (TypeError, ValueError):
                selected_product_id = None

        if from_branch_id is None:
            self.fields["product"].queryset = Product.objects.none()
        else:
            available_ids = set(
                BranchStock.objects.filter(
                    branch_id=from_branch_id,
                    quantity__gt=0,
                    product__status=Product.Status.ACTIVE,
                ).values_list("product_id", flat=True)
            )
            if selected_product_id:
                available_ids.add(selected_product_id)
            self.fields["product"].queryset = Product.objects.filter(id__in=available_ids).order_by("name")

        self.fields["product"].widget.attrs.update({"class": "form-select modern-select-input js-transfer-product-select"})
        self.fields["quantity"].widget.attrs.update(
            {
                "class": "form-control js-transfer-qty text-end transfer-qty-input",
                "step": "0.01",
                "min": "0.01",
                "inputmode": "decimal",
            }
        )

    def clean_quantity(self):
        qty = self.cleaned_data.get("quantity")
        if qty is not None and qty <= Decimal("0"):
            raise ValidationError("Quantity must be greater than zero. Negative stock is not allowed.")
        return qty

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        qty = cleaned.get("quantity")
        if not product and qty is None:
            return cleaned
        if product and qty is None:
            self.add_error("quantity", "Enter quantity.")
        if qty is not None and not product:
            self.add_error("product", "Select product.")
        return cleaned


def get_farmer_goods_issue_line_formset():
    return formset_factory(
        FarmerGoodsIssueLineEntryForm,
        extra=1,
        min_num=0,
        max_num=200,
        can_delete=False,
    )


class InventoryUsageForm(forms.ModelForm):
    class Meta:
        model = InventoryUsage
        fields = ["product", "quantity", "usage_reason"]


class ConsumptionSettlementForm(forms.ModelForm):
    class Meta:
        model = ConsumptionSettlement
        fields = ["product", "quantity", "source_type", "source_id"]


class SupplierPaymentForm(forms.Form):
    supplier = forms.ModelChoiceField(queryset=Supplier.objects.none())
    amount = forms.DecimalField(max_digits=14, decimal_places=2, min_value=0.01)
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = filter_m2m_by_user_branches(
            Supplier.objects.filter(status=Supplier.Status.ACTIVE),
            user,
        ).order_by("name")
        self.fields["supplier"].queryset = qs
        self.fields["supplier"].widget.attrs["class"] = "form-select"


class SupplierRateUpdateForm(forms.Form):
    rate = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=0,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control js-focus-next",
                "step": "0.01",
                "min": "0",
                "inputmode": "decimal",
            }
        ),
    )
    rate_unit = forms.ChoiceField(
        choices=Supplier.RateUnit.choices,
        widget=forms.Select(attrs={"class": "form-select js-focus-next"}),
        label="Unit",
    )
    effective_from = forms.DateField(
        widget=FlatpickrDateInput(
            attrs={
                "class": "form-control js-datepicker js-focus-next",
                "placeholder": "Effective from",
                "autocomplete": "off",
            }
        ),
        label="Effective from",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.initial.get("effective_from") and not self.data:
            self.initial["effective_from"] = timezone.localdate()


class RawMilkSupplierPaymentReceiveForm(forms.Form):
    date = forms.DateField(
        widget=FlatpickrDateInput(
            attrs={
                "class": "form-control js-datepicker js-focus-next",
                "placeholder": "Payment date",
                "autocomplete": "off",
            }
        ),
    )
    amount = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.TextInput(
            attrs={
                "class": "form-control js-money-input js-focus-next",
                "inputmode": "decimal",
                "autocomplete": "off",
                "placeholder": "0.00",
            }
        ),
    )
    reference = forms.CharField(
        required=False,
        max_length=80,
        widget=forms.TextInput(
            attrs={
                "class": "form-control js-focus-next",
                "placeholder": "Receipt / ref (optional)",
            }
        ),
    )
    note = forms.CharField(
        required=False,
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "class": "form-control js-focus-next",
                "placeholder": "Note (optional)",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        # Strip thousand separators before DecimalField validation.
        if args and hasattr(args[0], "get"):
            data = args[0].copy()
            if "amount" in data and data.get("amount") not in (None, ""):
                data["amount"] = str(data.get("amount")).replace(",", "").strip()
            args = (data,) + args[1:]
        super().__init__(*args, **kwargs)
        if not self.initial.get("date") and not self.data:
            self.initial["date"] = timezone.localdate()


def _next_raw_milk_dispatch_no():
    max_dispatch = 0
    for dispatch_no in RawMilkSupplierCollection.objects.values_list("dispatch_no", flat=True):
        value = (dispatch_no or "").strip()
        if value.isdigit():
            max_dispatch = max(max_dispatch, int(value))
    return f"{max_dispatch + 1:05d}"


class RawMilkSupplierCollectionForm(forms.ModelForm):
    class Meta:
        model = RawMilkSupplierCollection
        fields = [
            "date",
            "branch",
            "supplier",
            "dispatch_no",
            "browser_number",
            "driver",
            "in_time",
            "out_time",
            "sealing_numbers",
            "liters",
            "temperature",
            "fat",
            "snf",
            "lr",
            "alcohol",
            "acidity",
            "kq",
            "remarks",
        ]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "branch": forms.Select(
                attrs={"class": "form-select no-global-select2", "id": "id_branch"}
            ),
            "supplier": forms.Select(attrs={"class": "form-select", "id": "id_supplier"}),
            "dispatch_no": forms.TextInput(attrs={"class": "form-control"}),
            "browser_number": forms.TextInput(attrs={"class": "form-control"}),
            "driver": forms.TextInput(attrs={"class": "form-control"}),
            "in_time": forms.TimeInput(attrs={"class": "form-control", "type": "time"}),
            "out_time": forms.TimeInput(attrs={"class": "form-control", "type": "time"}),
            "sealing_numbers": forms.TextInput(attrs={"class": "form-control"}),
            "liters": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0", "id": "id_liters"}
            ),
            "temperature": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "fat": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "snf": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "lr": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "alcohol": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "acidity": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "kq": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "remarks": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["liters"].label = "Quantity (liters)"
        self.fields["liters"].help_text = ""
        self.fields["dispatch_no"].help_text = ""
        branch_qs = get_allowed_branches_qs(user).order_by("name") if user else Branch.objects.none()
        self.fields["branch"].queryset = branch_qs
        self.fields["branch"].label_from_instance = lambda b: b.name
        branch_id = resolve_form_branch_id(self)
        supplier_qs = filter_m2m_by_user_branches(
            Supplier.objects.filter(
                status=Supplier.Status.ACTIVE,
                category=Supplier.Category.RAW_MILK_SUPPLIER,
            ),
            user,
        ).order_by("name")
        if branch_id:
            supplier_qs = filter_m2m_by_branch(supplier_qs, branch_id)
        required_supplier_id = self.instance.supplier_id if self.instance.pk else None
        self.fields["supplier"].queryset = queryset_with_required_pk(
            supplier_qs, required_supplier_id
        ).order_by("name")
        if not self.instance.pk and not self.data:
            self.initial.setdefault("dispatch_no", _next_raw_milk_dispatch_no())
            if branch_qs.count() == 1:
                self.initial.setdefault("branch", branch_qs.first().pk)

    def clean(self):
        data = super().clean()
        supplier = data.get("supplier")
        branch = data.get("branch")
        if supplier and branch and not supplier.branches.filter(pk=branch.pk).exists():
            self.add_error("supplier", "This supplier is not linked to the selected branch.")
        return data

    def clean_dispatch_no(self):
        value = (self.cleaned_data.get("dispatch_no") or "").strip()
        if value:
            return value
        return _next_raw_milk_dispatch_no()


class RawMilkSupplierCollectionStatusForm(forms.ModelForm):
    class Meta:
        model = RawMilkSupplierCollection
        fields = ["status"]
        widgets = {
            "status": forms.Select(attrs={"class": "form-select form-select-sm"}),
        }


class RawMilkSupplierCollectionResultForm(forms.ModelForm):
    class Meta:
        model = RawMilkSupplierCollection
        fields = ["supplier_result_quantity", "status"]
        widgets = {
            "supplier_result_quantity": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "status": forms.Select(attrs={"class": "form-select"}),
        }


class RawMilkSupplierCollectionPaymentForm(forms.ModelForm):
    class Meta:
        model = RawMilkSupplierCollection
        fields = ["payment_status"]
        widgets = {
            "payment_status": forms.Select(attrs={"class": "form-select"}),
        }


class FarmerGoodsModuleSettingsForm(forms.ModelForm):
    class Meta:
        model = FarmerGoodsModuleSettings
        fields = ["enable_accept_notifications"]
        labels = {
            "enable_accept_notifications": "Enable Farmer Goods accept notifications",
        }
