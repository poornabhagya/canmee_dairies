from django.conf import settings
from django.db import models


class Employee(models.Model):
    class EmploymentStatus(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        RESIGNED = "resigned", "Resigned"
        TERMINATED = "terminated", "Terminated"

    class DocumentType(models.TextChoices):
        ID_CARD = "id_card", "ID Card"
        WORK_PERMIT = "work_permit", "Work Permit"
        PASSPORT = "passport", "Passport"

    employee_number = models.CharField(
        max_length=50,
        unique=True,
        null=True,
        blank=True,
        help_text="Leave blank to assign automatically (EMP + id).",
    )
    first_name = models.CharField(max_length=255)
    user_account = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="employee_profile",
    )
    common_name = models.CharField(max_length=100, blank=True)
    initial = models.CharField(max_length=255, blank=True)
    middle_name = models.CharField(max_length=100, blank=True)
    last_name = models.CharField(max_length=100, blank=True)
    date_of_birth = models.DateField(null=True, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=50, blank=True)
    whatsapp = models.CharField(max_length=50, blank=True)
    nic = models.CharField(
        max_length=20,
        unique=True,
        null=True,
        blank=True,
        help_text="National Identity Card number used for attendance login.",
        db_index=True,
    )
    attendance_password_set = models.BooleanField(
        default=False,
        help_text="True after the employee completes first attendance password setup.",
    )
    job_title = models.CharField(max_length=150, blank=True)
    department = models.ForeignKey(
        "Department",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="employees",
    )
    hire_date = models.DateField(null=True, blank=True)
    document_type = models.CharField(
        max_length=20,
        choices=DocumentType.choices,
        blank=True,
    )
    document_file = models.FileField(
        upload_to="hrm/employee_documents/",
        blank=True,
        null=True,
    )
    status = models.CharField(
        max_length=20,
        choices=EmploymentStatus.choices,
        default=EmploymentStatus.ACTIVE,
    )
    resignation_date = models.DateField(null=True, blank=True)
    resignation_reason = models.CharField(max_length=255, blank=True)
    resignation_notes = models.TextField(blank=True)
    resigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="employee_resignations_recorded",
    )
    termination_date = models.DateField(null=True, blank=True)
    termination_reason = models.CharField(max_length=255, blank=True)
    termination_notes = models.TextField(blank=True)
    terminated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="employee_terminations_recorded",
    )
    notes = models.TextField(blank=True)
    date_created = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["last_name", "first_name"]
        verbose_name = "Employee"
        verbose_name_plural = "Employees"

    def save(self, *args, **kwargs):
        needs_auto_number = self._state.adding and not (
            self.employee_number and str(self.employee_number).strip()
        )
        if needs_auto_number:
            self.employee_number = None
        super().save(*args, **kwargs)
        if needs_auto_number:
            new_number = f"EMP{self.pk:05d}"
            Employee.objects.filter(pk=self.pk).update(employee_number=new_number)
            self.employee_number = new_number

    def __str__(self):
        num = self.employee_number or "-"
        name = self.get_full_name() or self.first_name or "-"
        return f"{num} - {name}"

    def get_full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p.strip() for p in parts if p and str(p).strip())


class Department(models.Model):
    name = models.CharField(max_length=150, unique=True)
    date_created = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class EmployeeDocument(models.Model):
    class DocumentType(models.TextChoices):
        NIC = "nic", "NIC"
        PASSPORT = "passport", "Passport"
        DRIVING_LICENCE = "driving_licence", "Driving Licence"
        OTHER = "other", "Other"

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE, related_name="documents")
    document_type = models.CharField(max_length=20, choices=DocumentType.choices)
    document_file = models.FileField(upload_to="hrm/employee_documents/")
    date_created = models.DateTimeField(auto_now_add=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date_created", "id"]

    def __str__(self):
        return f"{self.employee} - {self.get_document_type_display()}"
