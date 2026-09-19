from django.conf import settings
from django.db import models
from django.utils import timezone


class AttendanceRecord(models.Model):
    class PunchType(models.TextChoices):
        CHECK_IN = "check_in", "Check in"
        CHECK_OUT = "check_out", "Check out"

    employee = models.ForeignKey(
        "hrm.Employee",
        on_delete=models.PROTECT,
        related_name="attendance_records",
    )
    punch_type = models.CharField(max_length=20, choices=PunchType.choices)
    punched_at = models.DateTimeField(default=timezone.now, db_index=True)

    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    accuracy_m = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    location_label = models.CharField(max_length=255, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(blank=True)
    device_label = models.CharField(max_length=255, blank=True)
    app_label = models.CharField(max_length=100, blank=True, default="Web")

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="attendance_records_created",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-punched_at", "-id"]
        permissions = [
            ("view_attendance_admin", "Can view full attendance details (admin)"),
        ]
        indexes = [
            models.Index(fields=["employee", "-punched_at"]),
            models.Index(fields=["punch_type", "-punched_at"]),
        ]

    def __str__(self):
        return f"{self.employee_id} {self.punch_type} @ {self.punched_at}"

    @property
    def has_coordinates(self):
        return self.latitude is not None and self.longitude is not None

    @property
    def maps_url(self):
        if not self.has_coordinates:
            return ""
        return f"https://www.google.com/maps?q={self.latitude},{self.longitude}"
