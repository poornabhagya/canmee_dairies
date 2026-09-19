from decimal import Decimal

from django import forms
from canmee_dairies.widgets import FlatpickrDateInput
from branches.models import Branch
from branches.utils import get_allowed_branches_qs, get_default_branch_id
from user_management.templatetags.user_tags import user_is_admin_or_superuser
from .models import Route, CollectionPoint, CollectionPointBankAccount, FarmerBankAccount, CompanyProfile, CompanyBankAccount, Buyer, Farmer, bank_name_choices
from .route_driver_access import generate_unique_driver_access_code

class RouteForm(forms.ModelForm):
    regenerate_access_code = forms.BooleanField(
        required=False,
        label="Generate new access code",
        help_text="Creates a new alphanumeric code for driver login.",
    )

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self._can_manage_access_code = user_is_admin_or_superuser(user)
        if user is not None:
            self.fields["branch"].queryset = get_allowed_branches_qs(user).order_by("code")
            if not self.instance.pk and not self.data:
                default_branch_id = get_default_branch_id(user)
                if default_branch_id:
                    self.initial.setdefault("branch", default_branch_id)
        if self._can_manage_access_code and self.instance.pk and self.instance.driver_access_code:
            self.fields["driver_access_code"] = forms.CharField(
                required=False,
                label="Driver access code",
                disabled=True,
                initial=self.instance.driver_access_code,
            )
        else:
            self.fields.pop("regenerate_access_code", None)
            self.fields.pop("driver_access_code", None)

    class Meta:
        model = Route
        fields = [
            "name",
            "branch",
            "vehicle_name",
            "driver_name",
            "driver_can_view_route_summary",
            "driver_can_view_point_summary",
            "driver_can_view_farmer_goods",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "branch": forms.Select(attrs={"class": "form-select"}),
            "vehicle_name": forms.TextInput(
                attrs={"class": "form-control", "autocomplete": "off", "placeholder": "Vehicle number or name"}
            ),
            "driver_name": forms.TextInput(
                attrs={"class": "form-control", "autocomplete": "off", "placeholder": "Driver name"}
            ),
            "driver_can_view_route_summary": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "driver_can_view_point_summary": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "driver_can_view_farmer_goods": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        labels = {
            "vehicle_name": "Vehicle",
            "driver_name": "Driver",
            "driver_can_view_route_summary": "Route milk summary",
            "driver_can_view_point_summary": "Point milk collection",
            "driver_can_view_farmer_goods": "Issued farmer goods",
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        is_new = not instance.pk
        if is_new and not instance.driver_access_code:
            instance.driver_access_code = generate_unique_driver_access_code()
        elif self._can_manage_access_code and self.cleaned_data.get("regenerate_access_code"):
            instance.driver_access_code = generate_unique_driver_access_code(
                exclude_route_id=instance.pk
            )
        if commit:
            instance.save()
            self.save_m2m()
        return instance

class CollectionPointForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        self._user = user
        super().__init__(*args, **kwargs)
        qs = Route.objects.order_by("code")
        if user is not None and not user.is_superuser:
            branch_ids = list(get_allowed_branches_qs(user).values_list("id", flat=True))
            qs = qs.filter(branch_id__in=branch_ids) if branch_ids else qs.none()
        self.fields["route"].queryset = qs

    def save(self, commit=True):
        point = super().save(commit=False)
        if self._user is not None:
            point._fee_changed_by = self._user
        if commit:
            point.save()
            self.save_m2m()
        return point

    class Meta:
        model = CollectionPoint
        fields = ['number', 'name', 'location', 'collector_fee', 'additional', 'route']
        widgets = {
            "route": forms.Select(attrs={"class": "form-select"}),
            "number": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "name": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "location": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "collector_fee": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0"}
            ),
            "additional": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01", "min": "0"}
            ),
        }


class CollectionPointCreateForm(CollectionPointForm):
    """Create flow: optional hidden target route for redirect after save."""

    return_to_route = forms.IntegerField(required=False, widget=forms.HiddenInput())

    class Meta(CollectionPointForm.Meta):
        fields = ['number', 'name', 'location', 'collector_fee', 'additional', 'route', 'return_to_route']

class BuyerForm(forms.ModelForm):
    branches = forms.ModelMultipleChoiceField(
        queryset=Branch.objects.none(),
        required=True,
        widget=forms.SelectMultiple(
            attrs={
                "class": "form-select js-buyer-branches-select",
                "data-placeholder": "Select one or more branches",
            }
        ),
        label="Branches",
    )

    class Meta:
        model = Buyer
        fields = ["name", "contact_person", "phone", "email", "address", "branches"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "contact_person": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "phone": forms.TextInput(attrs={"class": "form-control", "autocomplete": "tel"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "autocomplete": "email"}),
            "address": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
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
            raise forms.ValidationError("Select at least one branch.")
        if self.user and not self.user.is_superuser:
            allowed = set(get_allowed_branches_qs(self.user).values_list("id", flat=True))
            for branch in branches:
                if branch.id not in allowed:
                    raise forms.ValidationError("You can only assign buyers to your branches.")
        return branches

    def save(self, commit=True):
        instance = super().save(commit=commit)
        if commit:
            instance.branches.set(self.cleaned_data.get("branches", []))
        return instance

class FarmerForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        cp = self.fields["collection_point"]
        cp_qs = CollectionPoint.objects.select_related("route").order_by("route__code", "number")
        branch_qs = get_allowed_branches_qs(user) if user is not None else None
        selected_branch_id = None
        if user is not None:
            self.fields["branch"].queryset = branch_qs.order_by("code")
            selected_branch_id = self.data.get("branch") or self.initial.get("branch")
            if not selected_branch_id and self.instance and self.instance.pk and self.instance.branch_id:
                selected_branch_id = self.instance.branch_id
            if not self.instance.pk and not self.data:
                default_branch_id = get_default_branch_id(user)
                if default_branch_id:
                    self.initial.setdefault("branch", default_branch_id)
                    selected_branch_id = default_branch_id
        if user is not None and not user.is_superuser:
            branch_ids = list(branch_qs.values_list("id", flat=True))
            cp_qs = cp_qs.filter(route__branch_id__in=branch_ids) if branch_ids else cp_qs.none()
            # Single-branch users don't need manual branch selection.
            if len(branch_ids) <= 1:
                self.fields["branch"].widget.attrs["disabled"] = "disabled"
            else:
                self.fields["branch"].widget.attrs.pop("disabled", None)
        else:
            # Superusers/admin can choose branch explicitly.
            self.fields["branch"].widget.attrs.pop("disabled", None)
        if selected_branch_id and str(selected_branch_id).isdigit():
            cp_qs = cp_qs.filter(route__branch_id=int(selected_branch_id))
        cp.queryset = cp_qs
        cp.required = False
        cp.label_from_instance = lambda obj: f"{obj.number} - {obj.name}"
        self.fields["branch"].label_from_instance = lambda obj: obj.name
        self.fields["registration_number"].required = False
        self.fields["initial"].required = False
        self.fields["full_name"].required = True
        self.fields["common_name"].required = True
        self.fields["rate"].required = True
        self.fields["apply_rate_paid"].required = True
        # Branch is auto-derived from the selected collection point in clean().
        # Keeping this non-required allows quick-add flows that only post collection_point.
        self.fields["branch"].required = False
        self.fields["collection_point"].required = True
        self.fields["route"].required = False
        self.fields["registration_number"].widget = forms.HiddenInput()
        self.fields["initial"].widget = forms.HiddenInput()
        self.fields["route"].widget.attrs["disabled"] = "disabled"

    class Meta:
        model = Farmer
        fields = [
            "registration_number",
            "branch",
            "route",
            "collection_point",
            "full_name",
            "initial",
            "common_name",
            "rate",
            "apply_rate_paid",
            "nic",
            "date_of_birth",
            "mobile",
            "whatsapp",
            "email",
            "address",
        ]
        help_texts = {
            "collection_point": "Route is set automatically from the collection point you choose.",
            "registration_number": "Auto-generated after save.",
            "branch": "Auto-set from selected collection point.",
            "route": "Auto-set from selected collection point.",
        }
        widgets = {
            "registration_number": forms.HiddenInput(),
            "branch": forms.Select(attrs={"class": "form-select"}),
            "route": forms.Select(attrs={"class": "form-select"}),
            "collection_point": forms.Select(attrs={"class": "form-select"}),
            "full_name": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "initial": forms.HiddenInput(),
            "common_name": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "rate": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0.01"}),
            "apply_rate_paid": forms.Select(attrs={"class": "form-select"}),
            "nic": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "National Identity Card number",
                    "autocomplete": "off",
                }
            ),
            "date_of_birth": FlatpickrDateInput(),
            "mobile": forms.TextInput(
                attrs={"class": "form-control", "type": "tel", "autocomplete": "tel"}
            ),
            "whatsapp": forms.TextInput(
                attrs={"class": "form-control", "type": "tel", "autocomplete": "tel"}
            ),
            "email": forms.EmailInput(attrs={"class": "form-control", "autocomplete": "email"}),
            "address": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    @staticmethod
    def _build_initial_from_full_name(full_name):
        parts = [p.strip() for p in (full_name or "").split() if p and str(p).strip()]
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0]
        prefix = ".".join(p[0].upper() for p in parts[:-1])
        return f"{prefix}.{parts[-1]}"

    def clean(self):
        cleaned_data = super().clean()
        full_name = (cleaned_data.get("full_name") or "").strip()
        common_name = (cleaned_data.get("common_name") or "").strip()
        point = cleaned_data.get("collection_point")
        branch = cleaned_data.get("branch")
        point_route = None
        if point and point.route_id:
            try:
                point_route = point.route
            except Exception:
                point_route = None
                self.add_error("collection_point", "Selected collection point has an invalid route.")
            if point_route:
                cleaned_data["route"] = point_route
                cleaned_data["branch"] = point_route.branch
                self.cleaned_data["route"] = point_route
                self.cleaned_data["branch"] = point_route.branch
                branch = point_route.branch
        if full_name:
            cleaned_data["full_name"] = full_name
            self.cleaned_data["full_name"] = full_name
        if full_name and not (cleaned_data.get("initial") or "").strip():
            cleaned_data["initial"] = self._build_initial_from_full_name(full_name)
            self.cleaned_data["initial"] = cleaned_data["initial"]
        if not full_name:
            self.add_error("full_name", "Full name is required.")
        if not common_name:
            self.add_error("common_name", "Common name is required.")
        else:
            cleaned_data["common_name"] = common_name
            self.cleaned_data["common_name"] = common_name
        rate = cleaned_data.get("rate")
        if rate is None or rate <= 0:
            self.add_error("rate", "Rate must be greater than zero.")
        if not cleaned_data.get("apply_rate_paid"):
            self.add_error("apply_rate_paid", "Select Apply Rate Paid option.")
        if not point:
            self.add_error("collection_point", "Collection point is required.")
        # Branch is optional in posted payload; it is enforced indirectly via collection point.
        if not branch and point_route:
            cleaned_data["branch"] = point_route.branch
            self.cleaned_data["branch"] = point_route.branch
            branch = point_route.branch
        if not branch:
            self.add_error("branch", "Branch is required.")
        if branch and point and point_route and point_route.branch_id != branch.id:
            self.add_error("collection_point", "Selected collection point does not belong to selected branch.")
        return cleaned_data


class FarmerRateUpdateForm(forms.Form):
    rate = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=0.01,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0.01"}),
    )
    effective_from = forms.DateField(
        widget=FlatpickrDateInput(
            attrs={
                "class": "form-control js-datepicker",
                "placeholder": "Effective date",
                "autocomplete": "off",
            }
        ),
        label="Effective date",
    )
    # Kept as a hidden default ("yes"); not shown in Rate management UI.
    apply_rate_paid = forms.ChoiceField(
        choices=Farmer.ApplyRatePaid.choices,
        initial=Farmer.ApplyRatePaid.YES,
        required=False,
        widget=forms.HiddenInput(),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.utils import timezone

        if not self.initial.get("effective_from") and not self.data:
            self.initial["effective_from"] = timezone.localdate()

    def clean_apply_rate_paid(self):
        return Farmer.ApplyRatePaid.YES


class CollectionPointFeeUpdateForm(forms.Form):
    collector_fee = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
        label="Collector fee",
    )
    additional = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=0,
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
        label="Additional",
    )
    effective_from = forms.DateField(
        widget=FlatpickrDateInput(
            attrs={
                "class": "form-control js-datepicker",
                "placeholder": "Effective date",
                "autocomplete": "off",
            }
        ),
        label="Effective date",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.utils import timezone

        if not self.initial.get("effective_from") and not self.data:
            self.initial["effective_from"] = timezone.localdate()


class BuyerRateUpdateForm(forms.Form):
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
        choices=Buyer.RateUnit.choices,
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
        from django.utils import timezone

        if not self.initial.get("effective_from") and not self.data:
            self.initial["effective_from"] = timezone.localdate()


class BuyerPaymentReceiveForm(forms.Form):
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
        from django.utils import timezone

        if not self.initial.get("date") and not self.data:
            self.initial["date"] = timezone.localdate()


class FarmerResignationForm(forms.Form):
    resigned_at = forms.DateField(
        widget=FlatpickrDateInput(
            attrs={
                "class": "form-control js-datepicker",
                "placeholder": "Select date",
                "autocomplete": "off",
            }
        ),
    )
    resignation_note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 2, "placeholder": "Optional note"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.utils import timezone

        if not self.initial.get("resigned_at") and not self.data:
            self.initial["resigned_at"] = timezone.localdate()


class CollectionPointBankAccountForm(forms.ModelForm):
    class Meta:
        model = CollectionPointBankAccount
        fields = (
            "account_name",
            "account_number",
            "bank_name",
            "bank_code",
            "bank_branch",
            "branch_code",
            "is_primary",
        )
        widgets = {
            "account_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Account holder name", "autocomplete": "off"}
            ),
            "account_number": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Account number", "autocomplete": "off"}
            ),
            "bank_name": forms.Select(attrs={"class": "form-select"}),
            "bank_code": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. 7010",
                    "autocomplete": "off",
                    "inputmode": "numeric",
                }
            ),
            "bank_branch": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Branch", "autocomplete": "off"}
            ),
            "branch_code": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. 048",
                    "autocomplete": "off",
                    "inputmode": "numeric",
                }
            ),
            "is_primary": forms.CheckboxInput(
                attrs={"class": "form-check-input custom-check", "title": "Primary account"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current_bank = ""
        if self.is_bound:
            current_bank = self.data.get(self.add_prefix("bank_name"), "")
        elif self.instance and self.instance.pk:
            current_bank = self.instance.bank_name
        choices = bank_name_choices(current_bank)
        self.fields["bank_name"].choices = choices
        self.fields["bank_name"].widget.choices = choices
        self.fields["is_primary"].required = False
        self.fields["bank_code"].required = False
        self.fields["branch_code"].required = False

    def clean(self):
        cleaned = super().clean()
        values = {
            name: (cleaned.get(name) or "").strip()
            for name in (
                "account_name",
                "account_number",
                "bank_name",
                "bank_code",
                "bank_branch",
                "branch_code",
            )
        }
        for name in ("account_name", "account_number", "bank_name", "bank_branch"):
            if not values[name]:
                self.add_error(name, "This field is required.")
        values["bank_code"] = "".join(ch for ch in values["bank_code"] if ch.isalnum())
        values["branch_code"] = "".join(ch for ch in values["branch_code"] if ch.isalnum())
        if not values["bank_code"] and values["bank_name"]:
            from .models import Bank

            bank = (
                Bank.objects.filter(name__iexact=values["bank_name"], is_deleted=False)
                .exclude(code="")
                .first()
            )
            if bank and bank.code:
                values["bank_code"] = bank.code.strip()
        cleaned.update(values)
        return cleaned


class FarmerBankAccountForm(forms.ModelForm):
    class Meta:
        model = FarmerBankAccount
        fields = (
            "account_name",
            "account_number",
            "bank_name",
            "bank_code",
            "bank_branch",
            "branch_code",
            "is_primary",
        )
        widgets = {
            "account_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Account holder name", "autocomplete": "off"}
            ),
            "account_number": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Account number", "autocomplete": "off"}
            ),
            "bank_name": forms.Select(attrs={"class": "form-select"}),
            "bank_code": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. 7010",
                    "autocomplete": "off",
                    "inputmode": "numeric",
                }
            ),
            "bank_branch": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Branch", "autocomplete": "off"}
            ),
            "branch_code": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. 048",
                    "autocomplete": "off",
                    "inputmode": "numeric",
                }
            ),
            "is_primary": forms.CheckboxInput(
                attrs={"class": "form-check-input custom-check", "title": "Primary account"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current_bank = ""
        if self.is_bound:
            current_bank = self.data.get(self.add_prefix("bank_name"), "")
        elif self.instance and self.instance.pk:
            current_bank = self.instance.bank_name
        choices = bank_name_choices(current_bank)
        self.fields["bank_name"].choices = choices
        self.fields["bank_name"].widget.choices = choices
        self.fields["is_primary"].required = False
        self.fields["bank_code"].required = False
        self.fields["branch_code"].required = False

    def clean(self):
        cleaned = super().clean()
        values = {
            name: (cleaned.get(name) or "").strip()
            for name in (
                "account_name",
                "account_number",
                "bank_name",
                "bank_code",
                "bank_branch",
                "branch_code",
            )
        }
        for name in ("account_name", "account_number", "bank_name", "bank_branch"):
            if not values[name]:
                self.add_error(name, "This field is required.")
        values["bank_code"] = "".join(ch for ch in values["bank_code"] if ch.isalnum())
        values["branch_code"] = "".join(ch for ch in values["branch_code"] if ch.isalnum())
        if not values["bank_code"] and values["bank_name"]:
            from .models import Bank

            bank = (
                Bank.objects.filter(name__iexact=values["bank_name"], is_deleted=False)
                .exclude(code="")
                .first()
            )
            if bank and bank.code:
                values["bank_code"] = bank.code.strip()
        cleaned.update(values)
        return cleaned


class CompanyProfileForm(forms.ModelForm):
    class Meta:
        model = CompanyProfile
        fields = ("name", "legal_name", "address", "phone", "email", "tax_id", "website")
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "autocomplete": "organization"}),
            "legal_name": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "address": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "phone": forms.TextInput(attrs={"class": "form-control", "autocomplete": "tel"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "autocomplete": "email"}),
            "tax_id": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "website": forms.TextInput(attrs={"class": "form-control", "autocomplete": "url"}),
        }

    def clean_name(self):
        value = " ".join((self.cleaned_data.get("name") or "").split())
        if not value:
            raise forms.ValidationError("Company name is required.")
        return value


class CompanyBankAccountForm(forms.ModelForm):
    class Meta:
        model = CompanyBankAccount
        fields = (
            "account_name",
            "account_number",
            "bank_name",
            "bank_code",
            "bank_branch",
            "branch_code",
            "is_primary",
            "is_active",
        )
        widgets = {
            "account_name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Account holder name", "autocomplete": "off"}
            ),
            "account_number": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Debit account number", "autocomplete": "off"}
            ),
            "bank_name": forms.Select(attrs={"class": "form-select"}),
            "bank_code": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. 7083",
                    "autocomplete": "off",
                    "inputmode": "numeric",
                }
            ),
            "bank_branch": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "Branch", "autocomplete": "off"}
            ),
            "branch_code": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "e.g. 230",
                    "autocomplete": "off",
                    "inputmode": "numeric",
                }
            ),
            "is_primary": forms.CheckboxInput(
                attrs={"class": "form-check-input custom-check", "title": "Primary debit account"}
            ),
            "is_active": forms.CheckboxInput(
                attrs={"class": "form-check-input custom-check", "title": "Active account"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current_bank = ""
        if self.is_bound:
            current_bank = self.data.get(self.add_prefix("bank_name"), "")
        elif self.instance and self.instance.pk:
            current_bank = self.instance.bank_name
        choices = bank_name_choices(current_bank)
        self.fields["bank_name"].choices = choices
        self.fields["bank_name"].widget.choices = choices
        self.fields["is_primary"].required = False
        self.fields["is_active"].required = False
        self.fields["bank_code"].required = False
        self.fields["branch_code"].required = False
        self.fields["bank_branch"].required = False

    def clean(self):
        cleaned = super().clean()
        values = {
            name: (cleaned.get(name) or "").strip()
            for name in (
                "account_name",
                "account_number",
                "bank_name",
                "bank_code",
                "bank_branch",
                "branch_code",
            )
        }
        for name in ("account_name", "account_number", "bank_name"):
            if not values[name]:
                self.add_error(name, "This field is required.")
        values["bank_code"] = "".join(ch for ch in values["bank_code"] if ch.isalnum())
        values["branch_code"] = "".join(ch for ch in values["branch_code"] if ch.isalnum())
        values["account_number"] = "".join(ch for ch in values["account_number"] if ch.isalnum())
        if not values["bank_code"] and values["bank_name"]:
            from .models import Bank

            bank = (
                Bank.objects.filter(name__iexact=values["bank_name"], is_deleted=False)
                .exclude(code="")
                .first()
            )
            if bank and bank.code:
                values["bank_code"] = bank.code.strip()
        cleaned.update(values)
        return cleaned
