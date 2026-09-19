from django import forms
from canmee_dairies.widgets import FlatpickrDateInput
from branches.utils import get_allowed_branches_qs, get_default_branch_id
from .models import Route, CollectionPoint, Buyer, Farmer

class RouteForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["branch"].queryset = get_allowed_branches_qs(user).order_by("code")
            if not self.instance.pk and not self.data:
                default_branch_id = get_default_branch_id(user)
                if default_branch_id:
                    self.initial.setdefault("branch", default_branch_id)

    class Meta:
        model = Route
        fields = ["name", "branch"]

class CollectionPointForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        qs = Route.objects.order_by("code")
        if user is not None and not user.is_superuser:
            branch_ids = list(get_allowed_branches_qs(user).values_list("id", flat=True))
            qs = qs.filter(branch_id__in=branch_ids) if branch_ids else qs.none()
        self.fields["route"].queryset = qs

    class Meta:
        model = CollectionPoint
        fields = ['number', 'name', 'location', 'route']
        widgets = {
            "route": forms.Select(attrs={"class": "form-select"}),
            "number": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "name": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "location": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
        }


class CollectionPointCreateForm(CollectionPointForm):
    """Create flow: optional hidden target route for redirect after save."""

    return_to_route = forms.IntegerField(required=False, widget=forms.HiddenInput())

    class Meta(CollectionPointForm.Meta):
        fields = ['number', 'name', 'location', 'route', 'return_to_route']

class BuyerForm(forms.ModelForm):
    class Meta:
        model = Buyer
        fields = ['name', 'contact_person', 'phone', 'email', 'address']
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "contact_person": forms.TextInput(attrs={"class": "form-control", "autocomplete": "off"}),
            "phone": forms.TextInput(attrs={"class": "form-control", "autocomplete": "tel"}),
            "email": forms.EmailInput(attrs={"class": "form-control", "autocomplete": "email"}),
            "address": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

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
        self.fields["branch"].required = True
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
        if point and point.route_id:
            cleaned_data["route"] = point.route
            cleaned_data["branch"] = point.route.branch
            self.cleaned_data["route"] = point.route
            self.cleaned_data["branch"] = point.route.branch
            branch = point.route.branch
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
        if not point:
            self.add_error("collection_point", "Collection point is required.")
        if not branch:
            self.add_error("branch", "Branch is required.")
        if branch and point and point.route and point.route.branch_id != branch.id:
            self.add_error("collection_point", "Selected collection point does not belong to selected branch.")
        return cleaned_data
