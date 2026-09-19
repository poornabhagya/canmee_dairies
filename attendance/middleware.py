from django.shortcuts import redirect

from attendance.utils import employee_for_user


class AttendancePortalMiddleware:
    """
    Keep *attendance-only* employees inside the attendance portal
    (no staff sidebar apps).

    Attendance-only means: linked Employee, not staff/superuser, and no
    staff-app roles/permissions. Operators who also have an Employee profile
    (for punching) keep normal dashboard access.
    """

    ALLOWED_PREFIXES = (
        "/attendance/",
        "/static/",
        "/media/",
        "/favicon.ico",
        "/deploy/",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            if self._is_attendance_only(user):
                path = request.path or "/"
                if path.startswith("/attendance/admin"):
                    return redirect("attendance:punch")
                if not self._is_allowed(path):
                    return redirect("attendance:punch")
        return self.get_response(request)

    @staticmethod
    def _is_attendance_only(user):
        if user.is_superuser or user.is_staff:
            return False
        # Staff operators usually have groups/permissions even when is_staff=False.
        if user.groups.exists() or user.user_permissions.exists():
            return False
        if user.has_perm("hrm.view_employee"):
            return False
        if user.has_perm("attendance.view_attendance_admin"):
            return False
        if user.has_perm("attendance.view_attendancerecord"):
            return False
        return employee_for_user(user) is not None

    def _is_allowed(self, path):
        if path in ("/", ""):
            return False
        for prefix in self.ALLOWED_PREFIXES:
            if path.startswith(prefix):
                return True
        return False
