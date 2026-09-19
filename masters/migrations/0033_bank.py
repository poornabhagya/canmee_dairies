from django.db import migrations, models


BANKS = (
    "Amana Bank",
    "Bank of Ceylon",
    "Cargills Bank",
    "Citibank",
    "Commercial Bank of Ceylon",
    "DFCC Bank",
    "Habib Bank",
    "Hatton National Bank",
    "HDFC Bank",
    "HSBC",
    "Indian Bank",
    "Indian Overseas Bank",
    "MCB Bank",
    "National Development Bank",
    "National Savings Bank",
    "Nations Trust Bank",
    "Pan Asia Bank",
    "People's Bank",
    "Public Bank",
    "Regional Development Bank",
    "Sampath Bank",
    "Sanasa Development Bank",
    "Seylan Bank",
    "Standard Chartered Bank",
    "State Bank of India",
    "Union Bank of Colombo",
)


def seed_banks(apps, schema_editor):
    Bank = apps.get_model("masters", "Bank")
    for index, name in enumerate(BANKS, start=1):
        Bank.objects.get_or_create(
            name=name,
            defaults={"sort_order": index * 10, "is_active": True, "is_deleted": False},
        )


def unseed_banks(apps, schema_editor):
    Bank = apps.get_model("masters", "Bank")
    Bank.objects.filter(name__in=BANKS).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0032_collection_point_bank_accounts"),
    ]

    operations = [
        migrations.CreateModel(
            name="Bank",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("name", models.CharField(max_length=120, unique=True, verbose_name="Bank")),
                ("is_active", models.BooleanField(default=True, verbose_name="Active")),
                ("sort_order", models.PositiveSmallIntegerField(default=0, verbose_name="Order")),
            ],
            options={
                "verbose_name": "Bank",
                "verbose_name_plural": "Banks",
                "ordering": ["sort_order", "name"],
            },
        ),
        migrations.RunPython(seed_banks, unseed_banks),
    ]
