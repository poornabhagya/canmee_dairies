from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("hrm", "0003_alter_employeedocument_document_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="employee",
            name="resignation_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="employee",
            name="resignation_notes",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="employee",
            name="resignation_reason",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="employee",
            name="resigned_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="employee_resignations_recorded",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="employee",
            name="status",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("inactive", "Inactive"),
                    ("resigned", "Resigned"),
                    ("terminated", "Terminated"),
                ],
                default="active",
                max_length=20,
            ),
        ),
    ]
