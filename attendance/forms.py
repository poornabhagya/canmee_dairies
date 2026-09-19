from decimal import Decimal, InvalidOperation

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from hrm.models import Employee

from .models import AttendanceRecord
from .utils import employee_needs_password_setup, normalize_nic


class AttendanceNICLoginForm(forms.Form):
    nic = forms.CharField(
        label="NIC",
        max_length=20,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "National Identity Card number",
                "autocomplete": "username",
                "autocapitalize": "characters",
            }
        ),
    )
    password = forms.CharField(
        label="Password",
        required=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Password (leave blank on first login)",
                "autocomplete": "current-password",
            }
        ),
    )

    def clean_nic(self):
        nic = normalize_nic(self.cleaned_data.get("nic"))
        if not nic:
            raise ValidationError("Enter your NIC number.")
        return nic

    def clean(self):
        cleaned = super().clean()
        nic = cleaned.get("nic")
        password = cleaned.get("password") or ""
        if not nic:
            return cleaned

        employee = (
            Employee.objects.filter(nic__iexact=nic)
            .select_related("user_account")
            .first()
        )
        if employee is None:
            raise ValidationError("No employee found for this NIC. Contact HR.")
        if employee.status != Employee.EmploymentStatus.ACTIVE:
            raise ValidationError("This employee account is not active.")

        cleaned["employee"] = employee
        cleaned["needs_password_setup"] = employee_needs_password_setup(employee)

        if cleaned["needs_password_setup"]:
            # First attendance login — password is set on the next step.
            # Blank password is OK here even if a staff account already exists.
            return cleaned

        if not password:
            raise ValidationError("Enter your password.")
        return cleaned


class AttendanceSetPasswordForm(forms.Form):
    password1 = forms.CharField(
        label="New password",
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "autocomplete": "new-password"}
        ),
    )
    password2 = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "autocomplete": "new-password"}
        ),
    )

    def __init__(self, *args, employee=None, **kwargs):
        self.employee = employee
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1") or ""
        p2 = cleaned.get("password2") or ""
        if p1 != p2:
            raise ValidationError("Passwords do not match.")
        if self.employee and self.employee.user_account_id:
            validate_password(p1, user=self.employee.user_account)
        else:
            validate_password(p1)
        cleaned["password"] = p1
        return cleaned


class AttendancePunchForm(forms.Form):
    punch_type = forms.ChoiceField(choices=AttendanceRecord.PunchType.choices)
    latitude = forms.CharField(required=False)
    longitude = forms.CharField(required=False)
    accuracy_m = forms.CharField(required=False)
    location_label = forms.CharField(required=False, max_length=255)
    device_label = forms.CharField(required=False, max_length=255)
    app_label = forms.CharField(required=False, max_length=100)

    def _dec(self, key):
        raw = (self.cleaned_data.get(key) or "").strip()
        if not raw:
            return None
        try:
            return Decimal(raw)
        except (InvalidOperation, TypeError, ValueError):
            raise ValidationError({key: "Invalid number."})

    def clean(self):
        cleaned = super().clean()
        cleaned["latitude"] = self._dec("latitude")
        cleaned["longitude"] = self._dec("longitude")
        cleaned["accuracy_m"] = self._dec("accuracy_m")
        cleaned["location_label"] = (cleaned.get("location_label") or "").strip()
        cleaned["device_label"] = (cleaned.get("device_label") or "").strip()[:255]
        cleaned["app_label"] = (cleaned.get("app_label") or "Web").strip()[:100] or "Web"
        lat, lng = cleaned["latitude"], cleaned["longitude"]
        if (lat is None) ^ (lng is None):
            raise ValidationError("Both latitude and longitude are required together.")
        return cleaned
