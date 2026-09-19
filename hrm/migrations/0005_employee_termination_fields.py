from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("hrm", "0004_employee_resignation_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="employee",
            name="termination_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="employee",
            name="termination_notes",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="employee",
            name="termination_reason",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name="employee",
            name="terminated_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="employee_terminations_recorded",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
