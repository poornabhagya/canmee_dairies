from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from .forms import (
    DepartmentForm,
    EmployeeDocumentFormSet,
    EmployeeForm,
    EmployeeResignationForm,
    EmployeeTerminationForm,
)
from .models import Department, Employee


def _employee_base_queryset():
    return Employee.objects.select_related("department", "user_account", "resigned_by").exclude(
        user_account__is_superuser=True
    )


def _apply_employee_search(qs, q):
    if not q:
        return qs
    return qs.filter(
        Q(employee_number__icontains=q)
        | Q(first_name__icontains=q)
        | Q(middle_name__icontains=q)
        | Q(last_name__icontains=q)
        | Q(common_name__icontains=q)
        | Q(initial__icontains=q)
        | Q(email__icontains=q)
        | Q(phone__icontains=q)
        | Q(whatsapp__icontains=q)
        | Q(job_title__icontains=q)
        | Q(department__name__icontains=q)
        | Q(user_account__username__icontains=q)
    )


@login_required
@permission_required("hrm.view_employee", raise_exception=True)
def employee_list(request):
    qs = _employee_base_queryset()
    status_tab_counts = {
        "all": qs.count(),
        "active": qs.filter(status=Employee.EmploymentStatus.ACTIVE).count(),
        "inactive": qs.filter(status=Employee.EmploymentStatus.INACTIVE).count(),
        "resigned": qs.filter(status=Employee.EmploymentStatus.RESIGNED).count(),
        "terminated": qs.filter(status=Employee.EmploymentStatus.TERMINATED).count(),
    }
    initial_status = (request.GET.get("status") or "all").strip()
    allowed_status = {"all"} | set(Employee.EmploymentStatus.values)
    if initial_status not in allowed_status:
        initial_status = "all"
    return render(
        request,
        "hrm/employee_list.html",
        {
            "employees": qs,
            "filter_status": initial_status,
            "status_tab_counts": status_tab_counts,
            "resign_form": EmployeeResignationForm(),
            "terminate_form": EmployeeTerminationForm(),
            "title": "Employees",
        },
    )


def _employee_detail_payload(employee):
    branches = list(employee.assigned_branches.order_by("code", "name"))
    user = employee.user_account
    return {
        "id": employee.pk,
        "employee_number": employee.employee_number or "",
        "common_name": employee.common_name or "",
        "first_name": employee.first_name or "",
        "middle_name": employee.middle_name or "",
        "last_name": employee.last_name or "",
        "full_name": employee.get_full_name() or employee.first_name or "",
        "initial": employee.initial or "",
        "nic": employee.nic or "",
        "date_of_birth": employee.date_of_birth.isoformat() if employee.date_of_birth else "",
        "email": employee.email or "",
        "phone": employee.phone or "",
        "whatsapp": employee.whatsapp or "",
        "job_title": employee.job_title or "",
        "department": employee.department.name if employee.department_id else "",
        "branches": [b.name for b in branches],
        "hire_date": employee.hire_date.isoformat() if employee.hire_date else "",
        "status": employee.status,
        "status_display": employee.get_status_display(),
        "attendance_password_set": bool(employee.attendance_password_set),
        "notes": employee.notes or "",
        "username": user.username if user else "",
        "user_email": user.email if user else "",
        "user_active": bool(user.is_active) if user else None,
        "user_roles": [g.name for g in user.groups.all()] if user else [],
        "last_login": user.last_login.strftime("%Y-%m-%d %H:%M") if user and user.last_login else "",
        "resignation_date": (
            employee.resignation_date.isoformat() if employee.resignation_date else ""
        ),
        "resignation_reason": employee.resignation_reason or "",
        "resignation_notes": employee.resignation_notes or "",
        "resigned_by": employee.resigned_by.get_username() if employee.resigned_by_id else "",
        "termination_date": (
            employee.termination_date.isoformat() if employee.termination_date else ""
        ),
        "termination_reason": employee.termination_reason or "",
        "termination_notes": employee.termination_notes or "",
        "terminated_by": (
            employee.terminated_by.get_username() if employee.terminated_by_id else ""
        ),
        "documents_count": employee.documents.count(),
        "date_created": (
            employee.date_created.strftime("%Y-%m-%d %H:%M") if employee.date_created else ""
        ),
        "last_updated": (
            employee.last_updated.strftime("%Y-%m-%d %H:%M") if employee.last_updated else ""
        ),
        "detail_url": reverse("hrm:employee_detail", args=[employee.pk]),
        "edit_url": reverse("hrm:employee_edit", args=[employee.pk]),
    }


@login_required
@permission_required("hrm.view_employee", raise_exception=True)
def employee_detail(request, pk):
    employee = get_object_or_404(
        _employee_base_queryset()
        .select_related("department", "user_account", "resigned_by", "terminated_by")
        .prefetch_related("documents", "assigned_branches", "user_account__groups"),
        pk=pk,
    )
    branches = list(employee.assigned_branches.order_by("code", "name"))
    return render(
        request,
        "hrm/employee_detail.html",
        {
            "employee": employee,
            "employee_branches": branches,
            "title": f"Employee - {employee.get_full_name()}",
        },
    )


@login_required
@permission_required("hrm.view_employee", raise_exception=True)
def employee_detail_json(request, pk):
    employee = get_object_or_404(
        _employee_base_queryset()
        .select_related("department", "user_account", "resigned_by", "terminated_by")
        .prefetch_related("documents", "assigned_branches", "user_account__groups"),
        pk=pk,
    )
    return JsonResponse({"ok": True, "employee": _employee_detail_payload(employee)})


@login_required
@permission_required("hrm.add_employee", raise_exception=True)
def employee_create(request):
    if request.method == "POST":
        form = EmployeeForm(request.POST, request.FILES, user=request.user, is_edit=False)
        doc_formset = EmployeeDocumentFormSet(request.POST, request.FILES, prefix="docs")
        if form.is_valid() and doc_formset.is_valid():
            emp = form.save()
            doc_formset.instance = emp
            doc_formset.save()
            messages.success(request, "Employee created.")
            return redirect("hrm:employee_detail", pk=emp.pk)
    else:
        form = EmployeeForm(user=request.user, is_edit=False)
        doc_formset = EmployeeDocumentFormSet(prefix="docs")
    return render(
        request,
        "hrm/employee_form.html",
        {"form": form, "doc_formset": doc_formset, "is_edit": False, "title": "Add employee"},
    )


@login_required
@permission_required("hrm.change_employee", raise_exception=True)
def employee_edit(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    if request.method == "POST":
        form = EmployeeForm(
            request.POST,
            request.FILES,
            instance=employee,
            user=request.user,
            is_edit=True,
        )
        doc_formset = EmployeeDocumentFormSet(
            request.POST,
            request.FILES,
            instance=employee,
            prefix="docs",
        )
        if form.is_valid() and doc_formset.is_valid():
            form.save()
            doc_formset.save()
            messages.success(request, "Employee updated.")
            return redirect("hrm:employee_detail", pk=employee.pk)
    else:
        form = EmployeeForm(instance=employee, user=request.user, is_edit=True)
        doc_formset = EmployeeDocumentFormSet(instance=employee, prefix="docs")
    return render(
        request,
        "hrm/employee_form.html",
        {
            "form": form,
            "doc_formset": doc_formset,
            "employee": employee,
            "is_edit": True,
            "title": "Edit employee",
        },
    )


@login_required
@permission_required("hrm.change_employee", raise_exception=True)
def employee_status_update(request, pk):
    if request.method != "POST":
        return JsonResponse({"ok": False, "error": "Invalid request."}, status=405)
    employee = get_object_or_404(Employee, pk=pk)
    status = (request.POST.get("status") or "").strip()
    allowed = {
        Employee.EmploymentStatus.ACTIVE,
        Employee.EmploymentStatus.INACTIVE,
    }
    if status not in allowed:
        return JsonResponse(
            {"ok": False, "error": "Use the resignation or termination form for this status."},
            status=400,
        )
    employee.status = status
    if status == Employee.EmploymentStatus.ACTIVE:
        employee.resignation_date = None
        employee.resignation_reason = ""
        employee.resignation_notes = ""
        employee.resigned_by = None
        employee.termination_date = None
        employee.termination_reason = ""
        employee.termination_notes = ""
        employee.terminated_by = None
    employee.save()
    return JsonResponse(
        {
            "ok": True,
            "status": employee.status,
            "status_label": employee.get_status_display(),
        }
    )


@login_required
@permission_required("hrm.change_employee", raise_exception=True)
def employee_resign(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    if employee.status == Employee.EmploymentStatus.RESIGNED:
        messages.info(request, "This employee is already marked as resigned.")
        return redirect("hrm:employee_detail", pk=employee.pk)
    if request.method == "POST":
        form = EmployeeResignationForm(request.POST)
        if form.is_valid():
            employee.resignation_date = form.cleaned_data["resignation_date"]
            employee.resignation_reason = form.cleaned_data["resignation_reason"]
            employee.resignation_notes = form.cleaned_data["resignation_notes"]
            employee.resigned_by = request.user
            employee.status = Employee.EmploymentStatus.RESIGNED
            employee.save(
                update_fields=[
                    "resignation_date",
                    "resignation_reason",
                    "resignation_notes",
                    "resigned_by",
                    "status",
                    "last_updated",
                ]
            )
            if form.cleaned_data.get("deactivate_user") and employee.user_account_id:
                employee.user_account.is_active = False
                employee.user_account.save(update_fields=["is_active"])
            messages.success(
                request,
                f"Resignation recorded for {employee.get_full_name() or employee.first_name}.",
            )
            return redirect(reverse("hrm:employee_list") + "?status=resigned")
        messages.error(request, "Please correct the resignation details.")
    else:
        form = EmployeeResignationForm()
    return render(
        request,
        "hrm/employee_resign.html",
        {"employee": employee, "form": form, "title": "Record resignation"},
    )


@login_required
@permission_required("hrm.change_employee", raise_exception=True)
def employee_terminate(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    if employee.status == Employee.EmploymentStatus.TERMINATED:
        messages.info(request, "This employee is already marked as terminated.")
        return redirect("hrm:employee_detail", pk=employee.pk)
    if request.method == "POST":
        form = EmployeeTerminationForm(request.POST)
        if form.is_valid():
            employee.termination_date = form.cleaned_data["termination_date"]
            employee.termination_reason = form.cleaned_data["termination_reason"]
            employee.termination_notes = form.cleaned_data["termination_notes"]
            employee.terminated_by = request.user
            employee.status = Employee.EmploymentStatus.TERMINATED
            employee.save(
                update_fields=[
                    "termination_date",
                    "termination_reason",
                    "termination_notes",
                    "terminated_by",
                    "status",
                    "last_updated",
                ]
            )
            if form.cleaned_data.get("deactivate_user") and employee.user_account_id:
                employee.user_account.is_active = False
                employee.user_account.save(update_fields=["is_active"])
            messages.success(
                request,
                f"Termination recorded for {employee.get_full_name() or employee.first_name}.",
            )
            return redirect(reverse("hrm:employee_list") + "?status=terminated")
        messages.error(request, "Please correct the termination details.")
    else:
        form = EmployeeTerminationForm()
    return render(
        request,
        "hrm/employee_terminate.html",
        {"employee": employee, "form": form, "title": "Record termination"},
    )


@login_required
@permission_required("hrm.delete_employee", raise_exception=True)
def employee_delete(request, pk):
    employee = get_object_or_404(Employee, pk=pk)
    if request.method == "POST":
        name = employee.get_full_name()
        employee.delete()
        messages.success(request, f"Deleted employee {name}.")
        return redirect("hrm:employee_list")
    return redirect("hrm:employee_list")


@login_required
@permission_required("hrm.view_department", raise_exception=True)
def department_manage(request):
    form = DepartmentForm()
    edit_department = None
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            if not request.user.has_perm("hrm.add_department"):
                messages.error(request, "You do not have permission to add departments.")
                return redirect("hrm:department_manage")
            form = DepartmentForm(request.POST)
            if form.is_valid():
                form.save()
                messages.success(request, "Department added.")
                return redirect("hrm:department_manage")
        elif action == "edit":
            if not request.user.has_perm("hrm.change_department"):
                messages.error(request, "You do not have permission to edit departments.")
                return redirect("hrm:department_manage")
            department = get_object_or_404(Department, pk=request.POST.get("department_id"))
            form = DepartmentForm(request.POST, instance=department)
            if form.is_valid():
                form.save()
                messages.success(request, "Department updated.")
                return redirect("hrm:department_manage")
            edit_department = department
        elif action == "delete":
            if not request.user.has_perm("hrm.delete_department"):
                messages.error(request, "You do not have permission to delete departments.")
                return redirect("hrm:department_manage")
            department = get_object_or_404(Department, pk=request.POST.get("department_id"))
            department.delete()
            messages.success(request, "Department deleted.")
            return redirect("hrm:department_manage")
    elif request.GET.get("edit"):
        edit_department = get_object_or_404(Department, pk=request.GET.get("edit"))
        form = DepartmentForm(instance=edit_department)
    return render(
        request,
        "hrm/department_manage.html",
        {
            "departments": Department.objects.order_by("name"),
            "form": form,
            "edit_department": edit_department,
            "title": "Manage departments",
        },
    )
