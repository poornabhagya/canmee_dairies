from django import forms
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.models import Group


class RoleAssignForm(forms.Form):
    user_id = forms.IntegerField(widget=forms.HiddenInput)
    groups = forms.ModelMultipleChoiceField(queryset=Group.objects.all(), required=False)


class StyledPasswordChangeForm(PasswordChangeForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        password_attrs = {
            "class": "form-control js-password-input",
            "autocomplete": "current-password",
        }
        self.fields["old_password"].widget = forms.PasswordInput(
            attrs={**password_attrs, "autocomplete": "current-password", "placeholder": "Enter current password"}
        )
        self.fields["new_password1"].widget = forms.PasswordInput(
            attrs={**password_attrs, "autocomplete": "new-password", "placeholder": "Enter new password"}
        )
        self.fields["new_password2"].widget = forms.PasswordInput(
            attrs={**password_attrs, "autocomplete": "new-password", "placeholder": "Confirm new password"}
        )
        self.fields["new_password1"].help_text = ""
        self.fields["new_password2"].help_text = ""
