from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from hrm.models import Employee

from .models import AttendanceRecord
from .utils import get_client_ip


def latest_punch(employee):
    return (
        AttendanceRecord.objects.filter(employee=employee)
        .order_by("-punched_at", "-id")
        .first()
    )


def suggested_punch_type(employee):
    last = latest_punch(employee)
    if last is None or last.punch_type == AttendanceRecord.PunchType.CHECK_OUT:
        return AttendanceRecord.PunchType.CHECK_IN
    return AttendanceRecord.PunchType.CHECK_OUT


@transaction.atomic
def record_punch(*, employee, punch_type, request, form_data, actor=None):
    if employee.status != Employee.EmploymentStatus.ACTIVE:
        raise ValidationError("Only active employees can punch attendance.")

    suggested = suggested_punch_type(employee)
    if punch_type != suggested:
        raise ValidationError(
            f"Next action should be {dict(AttendanceRecord.PunchType.choices).get(suggested)}."
        )

    return AttendanceRecord.objects.create(
        employee=employee,
        punch_type=punch_type,
        punched_at=timezone.now(),
        latitude=form_data.get("latitude"),
        longitude=form_data.get("longitude"),
        accuracy_m=form_data.get("accuracy_m"),
        location_label=form_data.get("location_label") or "",
        ip_address=get_client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:2000],
        device_label=form_data.get("device_label") or "",
        app_label=form_data.get("app_label") or "Web",
        created_by=actor if getattr(actor, "pk", None) else None,
    )
