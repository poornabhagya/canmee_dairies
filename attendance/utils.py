import re

from django.contrib.auth import get_user_model

from hrm.models import Employee


User = get_user_model()


def normalize_nic(value):
    """Uppercase, strip spaces/dashes — Sri Lanka NIC friendly."""
    text = str(value or "").strip().upper()
    text = re.sub(r"[\s\-]", "", text)
    return text


def get_client_ip(request):
    forwarded = (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
    if forwarded:
        return forwarded[:45]
    return (request.META.get("REMOTE_ADDR") or "")[:45] or None


def employee_for_user(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return (
        Employee.objects.filter(user_account=user)
        .select_related("department", "user_account")
        .first()
    )


def employee_needs_password_setup(employee):
    """First attendance login until they set a password via the NIC portal."""
    if employee is None:
        return True
    if not employee.attendance_password_set:
        return True
    user = employee.user_account
    if user is None:
        return True
    return not user.has_usable_password()


def ensure_attendance_user(employee, password):
    """Create or update the linked User so NIC login works."""
    nic = normalize_nic(employee.nic)
    if not nic:
        raise ValueError("Employee NIC is required.")

    user = employee.user_account
    if user is None:
        # Prefer username = NIC; fall back if taken by another account.
        username = nic
        if User.objects.filter(username=username).exists():
            username = f"emp-{employee.pk}-{nic[-6:]}"
        user = User(
            username=username,
            first_name=(employee.common_name or employee.first_name or "")[:150],
            last_name=(employee.last_name or "")[:150],
            email=employee.email or "",
            is_active=True,
            is_staff=False,
            is_superuser=False,
        )
        user.set_password(password)
        user.save()
        employee.user_account = user
        employee.attendance_password_set = True
        employee.nic = nic
        employee.save(
            update_fields=["user_account", "attendance_password_set", "nic", "last_updated"]
        )
    else:
        user.set_password(password)
        if not user.is_active:
            user.is_active = True
        user.save()
        employee.attendance_password_set = True
        employee.nic = nic
        employee.save(update_fields=["attendance_password_set", "nic", "last_updated"])
    return user
