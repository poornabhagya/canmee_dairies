from hrm.models import Employee

from attendance.middleware import AttendancePortalMiddleware


def attendance_nav(request):
    linked = False
    portal_only = False
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_authenticated", False):
        linked = Employee.objects.filter(user_account_id=user.pk).exists()
        portal_only = AttendancePortalMiddleware._is_attendance_only(user)
    return {
        "has_attendance_profile": linked and not portal_only,
        "is_attendance_portal_user": portal_only,
    }
