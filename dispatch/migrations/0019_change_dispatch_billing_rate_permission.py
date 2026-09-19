from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("dispatch", "0018_delivered_quantity_and_status_label"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="milkdistribution",
            options={
                "ordering": ["-date", "-id"],
                "permissions": [
                    (
                        "respond_branch_dispatch",
                        "Can respond to incoming branch dispatches",
                    ),
                    (
                        "change_dispatch_billing_rate",
                        "Can add or change billing rate on a milk dispatch",
                    ),
                ],
            },
        ),
    ]
