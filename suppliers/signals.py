from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import Supplier, SupplierAccount


@receiver(post_save, sender=Supplier)
def ensure_supplier_account(sender, instance, created, **kwargs):
    if created:
        SupplierAccount.objects.get_or_create(supplier=instance)
