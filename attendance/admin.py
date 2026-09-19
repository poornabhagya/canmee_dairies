from django.contrib import admin

from .models import AttendanceRecord


@admin.register(AttendanceRecord)
class AttendanceRecordAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "employee",
        "punch_type",
        "punched_at",
        "ip_address",
        "device_label",
        "app_label",
    )
    list_filter = ("punch_type", "punched_at", "app_label")
    search_fields = (
        "employee__nic",
        "employee__employee_number",
        "employee__first_name",
        "ip_address",
        "device_label",
    )
    readonly_fields = ("created_at",)
    date_hierarchy = "punched_at"
