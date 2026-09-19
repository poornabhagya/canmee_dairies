from django import forms
from django.contrib.auth.models import Group
from django.forms import inlineformset_factory
from django.utils import timezone

from branches.models import Branch
from branches.utils import get_default_branch_id
from canmee_dairies.widgets import FlatpickrDateInput
from .models import Department, Employee, EmployeeDocument


class EmployeeForm(forms.ModelForm):
    full_name = forms.CharField(required=True, max_length=255, label="Full Name")
    create_user_account = forms.BooleanField(required=False, label="Create user account")
    user_username = forms.CharField(required=False, max_length=150, label="Username")
    user_email = forms.EmailField(required=False, label="User email")
    user_password = forms.CharField(
        required=False,
        label="Password",
        widget=forms.PasswordInput(render_value=True),
    )
    user_password2 = forms.CharField(
        required=False,
        label="Confirm Password",
        widget=forms.PasswordInput(render_value=True),
    )
    user_role = forms.ModelChoiceField(
        queryset=Group.objects.none(),
        required=False,
        empty_label="Select role (optional)",
        label="User role",
    )
    branches = forms.ModelMultipleChoiceField(
        queryset=Branch.objects.none(),
        required=False,
        label="Branches",
        widget=forms.SelectMultiple(attrs={"class": "form-select"}),
    )

    class Meta:
        model = Employee
        fields = [
            "common_name",
            "date_of_birth",
            "email",
            "phone",
            "whatsapp",
            "nic",
            "job_title",
            "department",
            "branches",
            "hire_date",
            "status",
            "notes",
        ]
        widgets = {
            "date_of_birth": FlatpickrDateInput(),
            "hire_date": FlatpickrDateInput(),
            "notes": forms.Textarea(attrs={"rows": 4}),
        }
        labels = {
            "common_name": "Common Name",
            "date_of_birth": "Date of birth",
            "phone": "Mobile",
            "whatsapp": "WhatsApp",
            "initial": "Initial",
        }

    def __init__(self, *args, **kwargs):
        self.user = kwargs.pop("user", None)
        self.is_edit = kwargs.pop("is_edit", False)
        super().__init__(*args, **kwargs)
        self.order_fields([
            "full_name",
            "common_name",
            "date_of_birth",
            "email",
            "phone",
            "whatsapp",
            "nic",
            "job_title",
            "department",
            "branches",
            "hire_date",
            "status",
            "notes",
            "create_user_account",
            "user_username",
            "user_email",
            "user_role",
            "user_password",
            "user_password2",
        ])
        self.fields["user_role"].queryset = Group.objects.order_by("name")
        self.fields["nic"].required = False
        self.fields["nic"].widget.attrs.setdefault("placeholder", "NIC for attendance login")
        self.fields["nic"].label = "NIC"
        if self.user and self.user.is_superuser and self.is_edit:
            self.fields["employee_number"] = forms.CharField(
                required=False,
                label="Employee number",
                max_length=50,
                initial=self.instance.employee_number,
            )
        self.fields["full_name"].initial = self.instance.get_full_name() if self.instance and self.instance.pk else ""
        self.fields["full_name"].widget.attrs.setdefault("placeholder", "Enter full legal name")
        self.fields["common_name"].required = False
        self.fields["date_of_birth"].required = False
        self.fields["whatsapp"].required = False
        self.fields["department"].queryset = Department.objects.order_by("name")
        self.fields["department"].empty_label = "Select department"
        self.fields["branches"].queryset = Branch.objects.order_by("code", "name")
        if self.instance and self.instance.pk:
            self.fields["branches"].initial = self.instance.assigned_branches.all()
        elif self.user and not self.data:
            default_branch_id = get_default_branch_id(self.user)
            if default_branch_id:
                self.initial.setdefault("branches", [default_branch_id])
        if not self.instance.pk and not self.data:
            self.initial.setdefault("hire_date", timezone.localdate())
        for field in self.fields.values():
            field.widget.attrs.setdefault("class", "")
            if isinstance(field.widget, forms.CheckboxInput):
                continue
            if "form-control" not in field.widget.attrs["class"]:
                field.widget.attrs["class"] = (field.widget.attrs["class"] + " form-control").strip()
        if self.is_edit and self.instance and self.instance.user_account:
            self.fields["create_user_account"].initial = True
            self.fields["user_username"].initial = self.instance.user_account.username
            self.fields["user_email"].initial = self.instance.user_account.email
            first_group = self.instance.user_account.groups.order_by("name").first()
            if first_group:
                self.fields["user_role"].initial = first_group
            self.fields.pop("user_password", None)
            self.fields.pop("user_password2", None)
        else:
            self.fields["user_username"].initial = (self.initial.get("first_name") or "").lower()

    def clean_employee_number(self):
        employee_number = (self.cleaned_data.get("employee_number") or "").strip()
        if not employee_number:
            return self.instance.employee_number
        duplicate = Employee.objects.filter(employee_number=employee_number)
        if self.instance and self.instance.pk:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise forms.ValidationError("Employee number already exists.")
        return employee_number

    def clean_nic(self):
        from attendance.utils import normalize_nic

        nic = normalize_nic(self.cleaned_data.get("nic"))
        if not nic:
            return None
        duplicate = Employee.objects.filter(nic=nic)
        if self.instance and self.instance.pk:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise forms.ValidationError("This NIC is already assigned to another employee.")
        return nic

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
        if not full_name:
            self.add_error("full_name", "Full name is required.")
        cleaned_data["initial"] = self._build_initial_from_full_name(full_name)

        create_user_account = cleaned_data.get("create_user_account")
        has_connected_user = bool(self.instance and self.instance.pk and self.instance.user_account)
        manage_user_account = create_user_account or (self.is_edit and has_connected_user)
        if not manage_user_account:
            return cleaned_data

        user_username = (cleaned_data.get("user_username") or "").strip()
        user_email = (cleaned_data.get("user_email") or "").strip()
        user_password = cleaned_data.get("user_password") or ""
        user_password2 = cleaned_data.get("user_password2") or ""
        existing_user = self.instance.user_account if self.instance and self.instance.pk else None

        if not user_username:
            self.add_error("user_username", "Username is required.")
        if not user_email:
            self.add_error("user_email", "User email is required.")
        if create_user_account and not user_password and not existing_user:
            self.add_error("user_password", "Password is required for a new user.")
        if user_password or user_password2:
            if user_password != user_password2:
                self.add_error("user_password2", "Password and confirm password do not match.")

        UserModel = self._meta.model._meta.get_field("user_account").remote_field.model
        if user_username:
            username_qs = UserModel.objects.filter(username=user_username)
            if existing_user:
                username_qs = username_qs.exclude(pk=existing_user.pk)
            if username_qs.exists():
                self.add_error("user_username", "Username already exists.")
        if user_email:
            email_qs = UserModel.objects.filter(email=user_email)
            if existing_user:
                email_qs = email_qs.exclude(pk=existing_user.pk)
            if email_qs.exists():
                self.add_error("user_email", "User email already exists.")
        return cleaned_data

    def save(self, commit=True):
        employee = super().save(commit=False)
        full_name = (self.cleaned_data.get("full_name") or "").strip()
        selected_branches = self.cleaned_data.get("branches", [])
        employee.first_name = full_name
        employee.middle_name = ""
        employee.last_name = ""
        employee.initial = self._build_initial_from_full_name(full_name)
        create_user_account = self.cleaned_data.get("create_user_account")
        user_username = (self.cleaned_data.get("user_username") or "").strip()
        user_email = (self.cleaned_data.get("user_email") or "").strip()
        user_password = self.cleaned_data.get("user_password") or ""
        user_role = self.cleaned_data.get("user_role")
        UserModel = self._meta.model._meta.get_field("user_account").remote_field.model

        def apply_user_role(user_obj, selected_role):
            user_obj.groups.clear()
            user_obj.user_permissions.clear()
            if selected_role:
                user_obj.groups.add(selected_role)
                user_obj.user_permissions.add(*selected_role.permissions.all())

        if create_user_account:
            user_obj = employee.user_account
            if not user_obj:
                user_obj = UserModel(username=user_username, email=user_email, is_active=True)
            else:
                user_obj.username = user_username
                user_obj.email = user_email
            user_obj.first_name = employee.first_name or ""
            user_obj.last_name = employee.last_name or ""
            if user_password:
                user_obj.set_password(user_password)
            elif not user_obj.pk:
                user_obj.set_password(UserModel.objects.make_random_password())
            user_obj.save()
            employee.user_account = user_obj
        elif self.instance and self.instance.pk and self.instance.user_account:
            user_obj = self.instance.user_account
            user_obj.username = user_username or user_obj.username
            user_obj.first_name = employee.first_name or ""
            user_obj.last_name = employee.last_name or ""
            user_obj.email = user_email or user_obj.email
            user_obj.save(update_fields=["username", "first_name", "last_name", "email"])
            apply_user_role(user_obj, user_role)

        if commit:
            employee.save()
            self.save_m2m()
            employee.assigned_branches.set(selected_branches)
            if employee.user_account_id:
                employee.user_account.assigned_branches.set(selected_branches)
            if create_user_account:
                apply_user_role(employee.user_account, user_role)
        return employee


class EmployeeResignationForm(forms.Form):
    resignation_date = forms.DateField(
        label="Resignation date",
        widget=FlatpickrDateInput(),
    )
    resignation_reason = forms.CharField(
        max_length=255,
        label="Reason",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "e.g. Personal reasons, relocation"}
        ),
    )
    resignation_notes = forms.CharField(
        required=False,
        label="Notes",
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3, "placeholder": "Optional details"}
        ),
    )
    deactivate_user = forms.BooleanField(
        required=False,
        initial=True,
        label="Deactivate linked user account",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.initial.get("resignation_date") and not self.data:
            self.initial["resignation_date"] = timezone.localdate()


class EmployeeTerminationForm(forms.Form):
    termination_date = forms.DateField(
        label="Termination date",
        widget=FlatpickrDateInput(),
    )
    termination_reason = forms.CharField(
        max_length=255,
        label="Reason",
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "e.g. Contract end, dismissal"}
        ),
    )
    termination_notes = forms.CharField(
        required=False,
        label="Notes",
        widget=forms.Textarea(
            attrs={"class": "form-control", "rows": 3, "placeholder": "Optional details"}
        ),
    )
    deactivate_user = forms.BooleanField(
        required=False,
        initial=True,
        label="Deactivate linked user account",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input", "id": "id_terminate_deactivate_user"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.initial.get("termination_date") and not self.data:
            self.initial["termination_date"] = timezone.localdate()


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ["name"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["name"].widget.attrs.setdefault("class", "form-control")


class EmployeeDocumentForm(forms.ModelForm):
    class Meta:
        model = EmployeeDocument
        fields = ("document_type", "document_file")
        widgets = {
            "document_type": forms.Select(attrs={"class": "form-select"}),
            "document_file": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }


EmployeeDocumentFormSet = inlineformset_factory(
    Employee,
    EmployeeDocument,
    form=EmployeeDocumentForm,
    extra=1,
    can_delete=True,
)
