from decimal import Decimal

from django import forms
from django.forms import formset_factory
from django.utils.text import slugify

from branches.models import Branch
from .models import Product, ProductCategory, StockIssue, StockTransfer, StockTransferNote


class ProductForm(forms.ModelForm):
    UNIT_CHOICES = (
        ("kg", "Kg"),
        ("bag", "Bag"),
        ("bucket", "Bucket"),
        ("unit", "Unit"),
        ("litre", "Litre"),
        ("gram", "Gram"),
        ("piece", "Piece"),
        ("packet", "Packet"),
        ("box", "Box"),
    )

    unit = forms.ChoiceField(choices=UNIT_CHOICES)
    category = forms.ChoiceField(choices=())

    class Meta:
        model = Product
        fields = ["name", "category", "unit", "description"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        category_choices = [(c.code, c.name) for c in ProductCategory.objects.order_by("name")]
        self.fields["category"].choices = category_choices
        self.fields["name"].widget.attrs.update({"class": "form-control"})
        self.fields["category"].widget.attrs.update({"class": "form-select modern-select-input"})
        self.fields["unit"].widget.attrs.update({"class": "form-select modern-select-input"})
        self.fields["description"].widget.attrs.update({"class": "form-control"})


class ProductCategoryForm(forms.ModelForm):
    class Meta:
        model = ProductCategory
        fields = ["name"]

    def clean_name(self):
        name = (self.cleaned_data.get("name") or "").strip()
        if not name:
            return name
        code = slugify(name).replace("-", "_")
        qs = ProductCategory.objects.filter(code=code)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("A category with similar name already exists.")
        return name

    def save(self, commit=True):
        obj = super().save(commit=False)
        obj.code = slugify(obj.name).replace("-", "_")
        if commit:
            obj.save()
        return obj


class StockTransferForm(forms.ModelForm):
    class Meta:
        model = StockTransfer
        fields = ["product", "quantity", "to_branch"]


class StockTransferNoteForm(forms.ModelForm):
    class Meta:
        model = StockTransferNote
        fields = ["from_branch", "to_branch", "date", "remarks"]
        widgets = {
            "date": forms.DateInput(attrs={"type": "date", "class": "form-control"}),
            "remarks": forms.Textarea(attrs={"rows": 2, "class": "form-control"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from branches.utils import get_allowed_branches_qs

        branches = get_allowed_branches_qs(user).order_by("name") if user else None
        if branches is not None:
            self.fields["from_branch"].queryset = branches
        self.fields["to_branch"].queryset = Branch.objects.order_by("name")
        self.fields["from_branch"].widget.attrs.setdefault("class", "form-select modern-select-input")
        self.fields["to_branch"].widget.attrs.setdefault("class", "form-select modern-select-input")
        self.fields["remarks"].widget.attrs.setdefault("class", "form-control")

    def clean(self):
        data = super().clean()
        fb = data.get("from_branch")
        tb = data.get("to_branch")
        if fb and tb and fb.pk == tb.pk:
            raise forms.ValidationError("From branch and to branch must be different.")
        return data


class StockTransferLineEntryForm(forms.Form):
    product = forms.ModelChoiceField(
        queryset=Product.objects.none(),
        required=False,
    )
    quantity = forms.DecimalField(
        max_digits=14,
        decimal_places=2,
        required=False,
        min_value=Decimal("0.01"),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["product"].queryset = Product.objects.filter(
            status=Product.Status.ACTIVE,
        ).order_by("name")
        self.fields["product"].widget.attrs.update(
            {"class": "form-select modern-select-input js-transfer-product-select"}
        )
        self.fields["quantity"].widget.attrs.update(
            {
                "class": "form-control js-transfer-qty text-end transfer-qty-input",
                "step": "0.01",
                "min": "0",
                "inputmode": "decimal",
            }
        )

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


def get_stock_transfer_line_formset():
    """One starter row; use Add line on the page for more (JS updates TOTAL_FORMS)."""
    return formset_factory(
        StockTransferLineEntryForm,
        extra=1,
        min_num=0,
        max_num=200,
        can_delete=False,
    )


class StockIssueForm(forms.ModelForm):
    class Meta:
        model = StockIssue
        fields = ["branch", "product", "quantity", "issued_to_type", "issued_to_id"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Hide internal stocktake write-offs from the manual issue form.
        self.fields["issued_to_type"].choices = [
            c
            for c in StockIssue.IssuedToType.choices
            if c[0] != StockIssue.IssuedToType.STOCKTAKE
        ]


class StockCountCreateForm(forms.Form):
    branch = forms.ModelChoiceField(
        queryset=Branch.objects.none(),
        label="Branch",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    date = forms.DateField(
        label="Count date",
        widget=forms.DateInput(
            attrs={"class": "form-control js-datepicker", "autocomplete": "off"}
        ),
    )
    remarks = forms.CharField(
        required=False,
        label="Remarks",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Optional"}),
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        from branches.utils import get_allowed_branches_qs
        from django.utils import timezone

        qs = Branch.objects.none()
        if user is not None:
            qs = get_allowed_branches_qs(user).order_by("name")
        self.fields["branch"].queryset = qs
        if not self.is_bound and not self.initial.get("date"):
            self.fields["date"].initial = timezone.localdate()
