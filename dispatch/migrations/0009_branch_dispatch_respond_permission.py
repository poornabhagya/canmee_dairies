from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("dispatch", "0008_branch_dispatch_workflow"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="milkdistribution",
            options={
                "permissions": [
                    (
                        "respond_branch_dispatch",
                        "Can respond to incoming branch dispatches",
                    ),
                ],
            },
        ),
    ]
