from django.db import migrations


def assign_all_branches(apps, schema_editor):
    Branch = apps.get_model("branches", "Branch")
    Buyer = apps.get_model("masters", "Buyer")
    Supplier = apps.get_model("suppliers", "Supplier")
    branch_ids = list(Branch.objects.values_list("id", flat=True))
    if not branch_ids:
        return
    for buyer in Buyer.objects.all():
        buyer.branches.set(branch_ids)
    for supplier in Supplier.objects.all():
        supplier.branches.set(branch_ids)


class Migration(migrations.Migration):

    dependencies = [
        ("masters", "0013_buyer_supplier_branches"),
        ("suppliers", "0011_buyer_supplier_branches"),
    ]

    operations = [
        migrations.RunPython(assign_all_branches, migrations.RunPython.noop),
    ]
