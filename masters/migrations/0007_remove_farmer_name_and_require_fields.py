from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0006_alter_farmer_options_farmer_common_name_and_more"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="farmer",
            options={"ordering": ["common_name", "full_name"]},
        ),
        migrations.AlterField(
            model_name="farmer",
            name="branch",
            field=models.ForeignKey(
                blank=False,
                null=True,
                on_delete=models.PROTECT,
                related_name="farmers",
                to="branches.branch",
            ),
        ),
        migrations.AlterField(
            model_name="farmer",
            name="collection_point",
            field=models.ForeignKey(
                blank=False,
                null=True,
                on_delete=models.SET_NULL,
                related_name="assigned_farmers",
                to="masters.collectionpoint",
                verbose_name="collection point",
            ),
        ),
        migrations.AlterField(
            model_name="farmer",
            name="common_name",
            field=models.CharField(max_length=100),
        ),
        migrations.AlterField(
            model_name="farmer",
            name="full_name",
            field=models.CharField(max_length=160),
        ),
        migrations.AlterField(
            model_name="farmer",
            name="registration_number",
            field=models.CharField(
                blank=True,
                help_text="Leave blank to assign automatically (F + Branch initial + Route initial + sequence).",
                max_length=50,
                null=True,
                unique=True,
            ),
        ),
        migrations.RemoveField(
            model_name="farmer",
            name="name",
        ),
    ]
