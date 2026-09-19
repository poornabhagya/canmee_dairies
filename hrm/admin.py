from django.contrib import admin

from .models import Department, Employee, EmployeeDocument


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "date_created", "last_updated")
    search_fields = ("name",)


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = (
        "employee_number",
        "first_name",
        "last_name",
        "nic",
        "department",
        "status",
        "user_account",
    )
    list_filter = ("status", "department")
    search_fields = ("employee_number", "first_name", "middle_name", "last_name", "email", "nic")


@admin.register(EmployeeDocument)
class EmployeeDocumentAdmin(admin.ModelAdmin):
    list_display = ("employee", "document_type", "document_file", "date_created")
    list_filter = ("document_type",)
    search_fields = ("employee__first_name", "employee__employee_number")
