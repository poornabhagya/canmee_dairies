from django.conf import settings
from django.db import models
from django.utils import timezone


class UserProfile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    avatar = models.ImageField(
        upload_to="avatars/%Y/%m/",
        blank=True,
        null=True,
        help_text="Square images work best (e.g. 400×400). Max 2 MB.",
    )
    phone = models.CharField(max_length=32, blank=True)
    notes = models.TextField(blank=True)

    class Meta:
        verbose_name = "user profile"
        verbose_name_plural = "user profiles"

    def __str__(self):
        return f"Profile for {self.user.get_username()}"


class AuditEvent(models.Model):
    """Central trail of user actions: who, when, what, where."""

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_events",
    )
    username = models.CharField(max_length=150, blank=True, db_index=True)
    user_display = models.CharField(max_length=255, blank=True)
    action = models.CharField(max_length=40, db_index=True)
    module = models.CharField(max_length=80, blank=True, db_index=True)
    summary = models.CharField(max_length=255, blank=True)
    method = models.CharField(max_length=10, blank=True)
    path = models.CharField(max_length=500, blank=True)
    status_code = models.PositiveSmallIntegerField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True)
    object_type = models.CharField(max_length=80, blank=True)
    object_id = models.CharField(max_length=64, blank=True)
    source = models.CharField(max_length=40, default="http", db_index=True)
    details = models.JSONField(blank=True, default=dict)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "audit event"
        verbose_name_plural = "audit events"
        indexes = [
            models.Index(fields=["-created_at", "-id"], name="user_manage_created_a8e1c2_idx"),
            models.Index(fields=["module", "-created_at"], name="user_manage_module_4b91d0_idx"),
            models.Index(fields=["action", "-created_at"], name="user_manage_action_9c2e44_idx"),
            models.Index(fields=["user", "-created_at"], name="user_manage_user_id_7f3a11_idx"),
        ]

    def __str__(self):
        who = self.user_display or self.username or "system"
        return f"{self.created_at:%Y-%m-%d %H:%M} · {who} · {self.action}"

    @property
    def details_script_id(self):
        return f"audit-json-{self.pk}"

    def user_initials(self):
        name = (self.user_display or self.username or "?").strip()
        parts = [p for p in name.replace(".", " ").split() if p]
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        return (name[:2] or "?").upper()

    def action_label(self):
        labels = {
            "login": "Signed in",
            "logout": "Signed out",
            "login_failed": "Sign-in failed",
            "create": "Created",
            "created": "Created",
            "update": "Updated",
            "updated": "Updated",
            "delete": "Deleted",
            "deleted": "Deleted",
            "submitted": "Submitted",
            "approved": "Approved",
            "rejected": "Rejected",
            "confirmed": "Confirmed",
            "reverted_to_draft": "Reverted to draft",
            "schedule_paid_updated": "Schedule paid updated",
        }
        key = (self.action or "").lower()
        if key in labels:
            return labels[key]
        return (self.action or "").replace("_", " ").capitalize() or "Action"

    def action_tone(self):
        key = (self.action or "").lower()
        if key in {"delete", "deleted", "login_failed", "rejected"} or (
            self.status_code and self.status_code >= 400
        ):
            return "danger"
        if key in {"create", "created", "approved", "confirmed", "login"}:
            return "success"
        if key in {"logout"}:
            return "muted"
        if key in {"submitted", "reverted_to_draft"}:
            return "warn"
        return "info"
