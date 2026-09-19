from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("collections", "0007_milk_collection_paid_flag"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="milkcollection",
            options={
                "ordering": [
                    "-date",
                    "route__code",
                    "collection_point__number",
                    "farmer__full_name",
                ],
                "permissions": [
                    (
                        "view_milkcollectionpaymentstatus",
                        "Can view milk collection payment status",
                    ),
                ],
            },
        ),
    ]
