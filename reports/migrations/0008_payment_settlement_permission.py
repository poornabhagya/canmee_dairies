from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0007_grant_period_payment_permissions"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="farmerpaymentloandeductionsetting",
            options={
                "ordering": ["-period_end", "-period_start", "farmer_id"],
                "permissions": [
                    ("view_farmerpaymentsheet", "Can view farmer payment sheet"),
                    (
                        "view_collectionpointpaymentsheet",
                        "Can view collection point payment sheet",
                    ),
                    (
                        "view_paymentsettlement",
                        "Can view payment settlement details",
                    ),
                ],
            },
        ),
    ]
