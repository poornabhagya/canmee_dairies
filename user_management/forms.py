from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group, Permission
from django.core.files.uploadedfile import UploadedFile

from branches.models import Branch
from branches.utils import get_default_branch_id
from hrm.models import Employee
from .models import UserProfile

User = get_user_model()


class StaffUserCreationForm(UserCreationForm):
    employee = forms.ModelChoiceField(
        queryset=Employee.objects.none(),
        required=True,
        empty_label="Select employee",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.order_by("name"),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "list-unstyled"}),
    )
    branches = forms.ModelMultipleChoiceField(
        queryset=Branch.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select"}),
        label="Branches",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = (
            "employee",
            "username",
            "email",
            "is_staff",
            "is_active",
        )

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = Employee.objects.filter(user_account__isnull=True).order_by(
            "first_name", "last_name"
        )
        self.fields["branches"].queryset = Branch.objects.order_by("code", "name")
        if not self.data and user is not None:
            default_branch_id = get_default_branch_id(user)
            if default_branch_id:
                self.initial.setdefault("branches", [default_branch_id])
        for name in ("username", "email"):
            if name in self.fields:
                self.fields[name].widget.attrs.setdefault("class", "form-control")
        for name in ("password1", "password2"):
            if name in self.fields:
                self.fields[name].widget.attrs.setdefault("class", "form-control")
        self.fields["is_staff"].widget.attrs.setdefault("class", "form-check-input")
        self.fields["is_active"].widget.attrs.setdefault("class", "form-check-input")

    def save(self, commit=True):
        user = super().save(commit=False)
        employee = self.cleaned_data.get("employee")
        if employee:
            user.first_name = employee.first_name or ""
            user.last_name = employee.last_name or ""
        groups = self.cleaned_data.get("groups", [])
        branches = self.cleaned_data.get("branches", [])
        if commit:
            user.save()
            user.groups.set(groups)
            user.assigned_branches.set(branches)
            if employee:
                employee.user_account = user
                employee.save(update_fields=["user_account"])
        return user


class StaffUserChangeForm(forms.ModelForm):
    employee = forms.ModelChoiceField(
        queryset=Employee.objects.none(),
        required=True,
        empty_label="Select employee",
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    branches = forms.ModelMultipleChoiceField(
        queryset=Branch.objects.none(),
        required=False,
        widget=forms.SelectMultiple(attrs={"class": "form-select"}),
        label="Branches",
    )

    class Meta:
        model = User
        fields = (
            "employee",
            "username",
            "email",
            "is_staff",
            "is_active",
            "groups",
        )
        widgets = {
            "groups": forms.CheckboxSelectMultiple(attrs={"class": "list-unstyled"}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        qs = Employee.objects.filter(user_account__isnull=True)
        if self.instance and self.instance.pk:
            qs = qs | Employee.objects.filter(user_account=self.instance)
            current_employee = Employee.objects.filter(user_account=self.instance).first()
            if current_employee:
                self.fields["employee"].initial = current_employee
        self.fields["employee"].queryset = qs.order_by("first_name", "last_name")
        self.fields["branches"].queryset = Branch.objects.order_by("code", "name")
        if self.instance and self.instance.pk:
            self.fields["branches"].initial = self.instance.assigned_branches.all()
        elif not self.data and user is not None:
            default_branch_id = get_default_branch_id(user)
            if default_branch_id:
                self.initial.setdefault("branches", [default_branch_id])
        for name in ("username", "email"):
            if name in self.fields:
                self.fields[name].widget.attrs.setdefault("class", "form-control")
        self.fields["is_staff"].widget.attrs.setdefault("class", "form-check-input")
        self.fields["is_active"].widget.attrs.setdefault("class", "form-check-input")

    def save(self, commit=True):
        user = super().save(commit=False)
        selected_employee = self.cleaned_data.get("employee")
        if selected_employee:
            user.first_name = selected_employee.first_name or ""
            user.last_name = selected_employee.last_name or ""
        if commit:
            user.save()
            self.save_m2m()
            user.assigned_branches.set(self.cleaned_data.get("branches", []))
            Employee.objects.filter(user_account=user).exclude(
                pk=selected_employee.pk if selected_employee else None
            ).update(user_account=None)
            if selected_employee:
                selected_employee.user_account = user
                selected_employee.save(update_fields=["user_account"])
        return user


class AdminSetPasswordForm(forms.Form):
    new_password1 = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
        label="New password",
    )
    new_password2 = forms.CharField(
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
        label="Confirm password",
    )

    def clean_new_password2(self):
        p1 = self.cleaned_data.get("new_password1")
        p2 = self.cleaned_data.get("new_password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("The two password fields do not match.")
        return p2


class GroupForm(forms.ModelForm):
    class Meta:
        model = Group
        fields = ("name",)
        widgets = {"name": forms.TextInput(attrs={"class": "form-control"})}


class GroupPermissionsForm(forms.Form):
    permissions = forms.ModelMultipleChoiceField(
        label="Permissions",
        queryset=Permission.objects.select_related("content_type").order_by(
            "content_type__app_label",
            "content_type__model",
            "codename",
        ),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "permission-checkboxes"}),
    )


class AssignRolesForm(forms.Form):
    user = forms.ModelChoiceField(queryset=User.objects.none(), label="User")
    groups = forms.ModelMultipleChoiceField(
        queryset=Group.objects.order_by("name"),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "list-unstyled"}),
        label="Roles (groups)",
    )

    def __init__(self, *args, user_queryset=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user_queryset is not None:
            self.fields["user"].queryset = user_queryset
        self.fields["user"].widget.attrs.setdefault("class", "form-select")


class ProfileForm(forms.ModelForm):
    class Meta:
        model = UserProfile
        fields = ("avatar", "phone", "notes")
        widgets = {
            "avatar": forms.FileInput(
                attrs={
                    "class": "profile-avatar-file-input js-profile-avatar-input",
                    "accept": "image/jpeg,image/png,image/webp,image/gif",
                }
            ),
            "phone": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "e.g. 077 123 4567"}
            ),
            "notes": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 3,
                    "placeholder": "Optional internal note (visible only to you)",
                }
            ),
        }

    def clean_avatar(self):
        f = self.cleaned_data.get("avatar")
        if f in (None, False):
            return f
        if isinstance(f, UploadedFile) and f.size > 2 * 1024 * 1024:
            raise forms.ValidationError("Image must be 2 MB or smaller.")
        return f


class SelfUserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ("username", "first_name", "last_name", "email")
        widgets = {
            "username": forms.TextInput(attrs={"class": "form-control"}),
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
        }
