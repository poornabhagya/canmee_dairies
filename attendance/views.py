from datetime import datetime, time, timedelta
from itertools import groupby

from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView, TemplateView

from branches.utils import get_user_branch_ids
from hrm.models import Employee

from .forms import AttendanceNICLoginForm, AttendancePunchForm, AttendanceSetPasswordForm
from .models import AttendanceRecord
from .services import latest_punch, record_punch, suggested_punch_type
from .utils import employee_for_user, ensure_attendance_user, normalize_nic


SESSION_PENDING_NIC = "attendance_pending_nic"


def _employee_initials(employee):
    parts = [
        (employee.first_name or "").strip(),
        (employee.common_name or employee.last_name or "").strip(),
    ]
    letters = "".join(p[0] for p in parts if p)[:2].upper()
    if letters:
        return letters
    name = (employee.get_full_name() or employee.employee_number or "?").strip()
    bits = [b for b in name.split() if b]
    if len(bits) >= 2:
        return (bits[0][0] + bits[1][0]).upper()
    return name[:2].upper()


def _day_start(day):
    return timezone.make_aware(datetime.combine(day, time.min))


def _day_end_exclusive(day):
    return timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min))


def _wants_json(request):
    accept = (request.headers.get("Accept") or "").lower()
    return (
        request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or "application/json" in accept
    )


class AttendanceLoginView(View):
    template_name = "attendance/login.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and employee_for_user(request.user):
            return redirect("attendance:punch")
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return render(request, self.template_name, {"form": AttendanceNICLoginForm()})

    def post(self, request):
        form = AttendanceNICLoginForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form}, status=400)

        employee = form.cleaned_data["employee"]
        if form.cleaned_data["needs_password_setup"]:
            request.session[SESSION_PENDING_NIC] = employee.nic
            messages.info(request, "First login — set a password to continue.")
            return redirect("attendance:set-password")

        user = authenticate(
            request,
            username=employee.nic,
            password=form.cleaned_data.get("password") or "",
        )
        if user is None:
            form.add_error(None, "Incorrect NIC or password.")
            return render(request, self.template_name, {"form": form}, status=400)

        login(request, user)
        next_url = request.GET.get("next") or reverse("attendance:punch")
        return redirect(next_url)


class AttendanceSetPasswordView(View):
    template_name = "attendance/set_password.html"

    def _pending_employee(self, request):
        nic = normalize_nic(request.session.get(SESSION_PENDING_NIC))
        if not nic:
            return None
        return (
            Employee.objects.filter(nic__iexact=nic, status=Employee.EmploymentStatus.ACTIVE)
            .select_related("user_account")
            .first()
        )

    def get(self, request):
        employee = self._pending_employee(request)
        if employee is None:
            messages.error(request, "Start again with your NIC.")
            return redirect("attendance:login")
        return render(
            request,
            self.template_name,
            {
                "form": AttendanceSetPasswordForm(employee=employee),
                "employee": employee,
            },
        )

    def post(self, request):
        employee = self._pending_employee(request)
        if employee is None:
            messages.error(request, "Start again with your NIC.")
            return redirect("attendance:login")
        form = AttendanceSetPasswordForm(request.POST, employee=employee)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"form": form, "employee": employee},
                status=400,
            )
        user = ensure_attendance_user(employee, form.cleaned_data["password"])
        request.session.pop(SESSION_PENDING_NIC, None)
        login(request, user, backend="attendance.backends.EmployeeNICBackend")
        messages.success(request, "Password saved. You are signed in.")
        return redirect("attendance:punch")


class AttendanceLogoutView(View):
    def post(self, request):
        logout(request)
        return redirect("attendance:login")

    def get(self, request):
        logout(request)
        return redirect("attendance:login")


class EmployeeAttendanceMixin(LoginRequiredMixin):
    login_url = "attendance:login"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        self.employee = employee_for_user(request.user)
        if self.employee is None or self.employee.status != Employee.EmploymentStatus.ACTIVE:
            messages.error(
                request,
                "No active employee profile is linked to this account. "
                "Use attendance login with your NIC.",
            )
            logout(request)
            return redirect("attendance:login")
        return super().dispatch(request, *args, **kwargs)


class AttendancePunchView(EmployeeAttendanceMixin, TemplateView):
    template_name = "attendance/punch.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        employee = self.employee
        last = latest_punch(employee)
        suggested = suggested_punch_type(employee)
        today = timezone.localdate()
        today_records = list(
            AttendanceRecord.objects.filter(
                employee=employee,
                punched_at__gte=_day_start(today),
                punched_at__lt=_day_end_exclusive(today),
            ).order_by("punched_at", "id")
        )
        is_checked_in = bool(
            last and last.punch_type == AttendanceRecord.PunchType.CHECK_IN
        )
        ctx.update(
            {
                "employee": employee,
                "employee_initials": _employee_initials(employee),
                "last_punch": last,
                "suggested_punch_type": suggested,
                "suggested_label": dict(AttendanceRecord.PunchType.choices).get(suggested),
                "today_records": today_records,
                "today": today,
                "is_checked_in": is_checked_in,
            }
        )
        return ctx

    def post(self, request):
        form = AttendancePunchForm(request.POST)
        if not form.is_valid():
            if _wants_json(request):
                return JsonResponse({"ok": False, "error": form.errors.as_text()}, status=400)
            messages.error(request, form.errors.as_text())
            return redirect("attendance:punch")

        try:
            record = record_punch(
                employee=self.employee,
                punch_type=form.cleaned_data["punch_type"],
                request=request,
                form_data=form.cleaned_data,
                actor=request.user,
            )
        except ValidationError as exc:
            msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            if _wants_json(request):
                return JsonResponse({"ok": False, "error": msg}, status=400)
            messages.error(request, msg)
            return redirect("attendance:punch")

        label = record.get_punch_type_display()
        if _wants_json(request):
            return JsonResponse(
                {
                    "ok": True,
                    "punch_type": record.punch_type,
                    "label": label,
                    "punched_at": timezone.localtime(record.punched_at).strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                    "redirect_url": reverse("attendance:punch"),
                }
            )
        messages.success(request, f"{label} recorded.")
        return redirect("attendance:punch")


class AttendanceMyHistoryView(EmployeeAttendanceMixin, ListView):
    template_name = "attendance/my_history.html"
    context_object_name = "records"
    paginate_by = 30

    def get_queryset(self):
        return AttendanceRecord.objects.filter(employee=self.employee).order_by(
            "-punched_at", "-id"
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["employee"] = self.employee
        records = list(ctx["records"])
        history_days = []
        for day, items in groupby(
            records, key=lambda r: timezone.localtime(r.punched_at).date()
        ):
            history_days.append({"date": day, "records": list(items)})
        ctx["history_days"] = history_days
        return ctx


class AttendanceProfileView(EmployeeAttendanceMixin, TemplateView):
    template_name = "attendance/profile.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["employee"] = self.employee
        ctx["employee_initials"] = _employee_initials(self.employee)
        return ctx


class AttendancePasswordChangeView(EmployeeAttendanceMixin, View):
    template_name = "attendance/profile_password.html"

    def _form(self, request, data=None):
        form = PasswordChangeForm(user=request.user, data=data)
        for name in form.fields:
            form.fields[name].widget.attrs.setdefault("class", "form-control")
        return form

    def get(self, request):
        return render(
            request,
            self.template_name,
            {"form": self._form(request), "employee": self.employee},
        )

    def post(self, request):
        form = self._form(request, data=request.POST)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"form": form, "employee": self.employee},
                status=400,
            )
        form.save()
        update_session_auth_hash(request, form.user)
        messages.success(request, "Password updated.")
        return redirect("attendance:profile")


class AttendanceAdminAccessMixin(LoginRequiredMixin, UserPassesTestMixin):
    def test_func(self):
        user = self.request.user
        return bool(
            user.is_superuser
            or user.has_perm("attendance.view_attendance_admin")
            or user.has_perm("attendance.view_attendancerecord")
            or user.has_perm("hrm.view_employee")
        )


class AttendanceAdminListView(AttendanceAdminAccessMixin, ListView):
    model = AttendanceRecord
    template_name = "attendance/admin_list.html"
    context_object_name = "records"
    paginate_by = 50

    def get_queryset(self):
        qs = AttendanceRecord.objects.select_related(
            "employee", "employee__department", "created_by"
        )
        branch_ids = get_user_branch_ids(self.request.user)
        if branch_ids is not None:
            qs = qs.filter(employee__assigned_branches__in=branch_ids).distinct()

        employee_id = (self.request.GET.get("employee") or "").strip()
        if employee_id.isdigit():
            qs = qs.filter(employee_id=int(employee_id))

        punch_type = (self.request.GET.get("punch_type") or "").strip()
        if punch_type in dict(AttendanceRecord.PunchType.choices):
            qs = qs.filter(punch_type=punch_type)

        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(employee__nic__icontains=q)
                | Q(employee__employee_number__icontains=q)
                | Q(employee__first_name__icontains=q)
                | Q(employee__common_name__icontains=q)
                | Q(ip_address__icontains=q)
                | Q(device_label__icontains=q)
            )
        return qs.order_by("-punched_at", "-id")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        base = self.get_queryset()
        today = timezone.localdate()
        ctx["summary"] = {
            "total": base.count(),
            "today": base.filter(
                punched_at__gte=_day_start(today),
                punched_at__lt=_day_end_exclusive(today),
            ).count(),
            "check_ins": base.filter(punch_type=AttendanceRecord.PunchType.CHECK_IN).count(),
            "check_outs": base.filter(
                punch_type=AttendanceRecord.PunchType.CHECK_OUT
            ).count(),
        }
        emp_qs = Employee.objects.filter(status=Employee.EmploymentStatus.ACTIVE).order_by(
            "first_name"
        )
        branch_ids = get_user_branch_ids(self.request.user)
        if branch_ids is not None:
            emp_qs = emp_qs.filter(assigned_branches__in=branch_ids).distinct()
        ctx["employees"] = emp_qs
        ctx["filters"] = {
            "q": self.request.GET.get("q", ""),
            "employee": self.request.GET.get("employee", ""),
            "punch_type": self.request.GET.get("punch_type", ""),
        }
        return ctx


class AttendanceAdminDetailView(AttendanceAdminAccessMixin, DetailView):
    model = AttendanceRecord
    template_name = "attendance/admin_detail.html"
    context_object_name = "record"

    def get_queryset(self):
        qs = AttendanceRecord.objects.select_related(
            "employee", "employee__department", "created_by"
        )
        branch_ids = get_user_branch_ids(self.request.user)
        if branch_ids is not None:
            qs = qs.filter(employee__assigned_branches__in=branch_ids).distinct()
        return qs
