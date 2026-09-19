from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Department",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=150, unique=True)),
                ("date_created", models.DateTimeField(auto_now_add=True)),
                ("last_updated", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="Employee",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "employee_number",
                    models.CharField(
                        blank=True,
                        help_text="Leave blank to assign automatically (EMP + id).",
                        max_length=50,
                        null=True,
                        unique=True,
                    ),
                ),
                ("first_name", models.CharField(max_length=100)),
                ("common_name", models.CharField(blank=True, max_length=100)),
                ("initial", models.CharField(blank=True, max_length=10)),
                ("middle_name", models.CharField(blank=True, max_length=100)),
                ("last_name", models.CharField(blank=True, max_length=100)),
                ("date_of_birth", models.DateField(blank=True, null=True)),
                ("email", models.EmailField(blank=True, max_length=254)),
                ("phone", models.CharField(blank=True, max_length=50)),
                ("whatsapp", models.CharField(blank=True, max_length=50)),
                ("job_title", models.CharField(blank=True, max_length=150)),
                ("hire_date", models.DateField(blank=True, null=True)),
                (
                    "document_type",
                    models.CharField(
                        blank=True,
                        choices=[("id_card", "ID Card"), ("work_permit", "Work Permit"), ("passport", "Passport")],
                        max_length=20,
                    ),
                ),
                ("document_file", models.FileField(blank=True, null=True, upload_to="hrm/employee_documents/")),
                (
                    "status",
                    models.CharField(
                        choices=[("active", "Active"), ("inactive", "Inactive"), ("terminated", "Terminated")],
                        default="active",
                        max_length=20,
                    ),
                ),
                ("notes", models.TextField(blank=True)),
                ("date_created", models.DateTimeField(auto_now_add=True)),
                ("last_updated", models.DateTimeField(auto_now=True)),
                (
                    "department",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="employees",
                        to="hrm.department",
                    ),
                ),
                (
                    "user_account",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="employee_profile",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["last_name", "first_name"], "verbose_name": "Employee", "verbose_name_plural": "Employees"},
        ),
    ]
