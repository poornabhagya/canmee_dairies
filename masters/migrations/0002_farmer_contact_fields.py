from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0001_initial"),
    ]

    operations = [
        migrations.RenameField(
            model_name="farmer",
            old_name="phone",
            new_name="mobile",
        ),
        migrations.AddField(
            model_name="farmer",
            name="nic",
            field=models.CharField(
                blank=True,
                help_text="National Identity Card number",
                max_length=20,
                verbose_name="NIC",
            ),
        ),
        migrations.AddField(
            model_name="farmer",
            name="date_of_birth",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="farmer",
            name="whatsapp",
            field=models.CharField(blank=True, max_length=25),
        ),
        migrations.AddField(
            model_name="farmer",
            name="email",
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AlterModelOptions(
            name="farmer",
            options={"ordering": ["name"]},
        ),
    ]
