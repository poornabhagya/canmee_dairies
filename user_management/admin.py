from django.contrib import admin

from .models import AuditEvent, UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone", "avatar")
    search_fields = ("user__username", "phone")
    fields = ("user", "avatar", "phone", "notes")


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "username",
        "action",
        "module",
        "summary",
        "status_code",
        "ip_address",
    )
    list_filter = ("action", "module", "source", "method")
    search_fields = ("username", "user_display", "summary", "path", "ip_address")
    readonly_fields = (
        "created_at",
        "user",
        "username",
        "user_display",
        "action",
        "module",
        "summary",
        "method",
        "path",
        "status_code",
        "ip_address",
        "user_agent",
        "object_type",
        "object_id",
        "source",
        "details",
    )
    ordering = ("-created_at", "-id")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
