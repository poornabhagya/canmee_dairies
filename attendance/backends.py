"""Authenticate employees by NIC → linked User account."""

from django.contrib.auth.backends import ModelBackend
from django.contrib.auth import get_user_model

from hrm.models import Employee

from .utils import normalize_nic


User = get_user_model()


class EmployeeNICBackend(ModelBackend):
    def authenticate(self, request, username=None, password=None, **kwargs):
        nic_raw = kwargs.get("nic") or username
        nic = normalize_nic(nic_raw)
        if not nic or password is None:
            return None

        employee = (
            Employee.objects.filter(nic__iexact=nic, status=Employee.EmploymentStatus.ACTIVE)
            .select_related("user_account")
            .first()
        )
        if employee is None or employee.user_account_id is None:
            return None

        user = employee.user_account
        if not employee.attendance_password_set or not user.has_usable_password():
            return None
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None

    def get_user(self, user_id):
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
        return user if self.user_can_authenticate(user) else None
