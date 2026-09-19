from django.db.models.signals import post_save
from django.dispatch import receiver

from .inbox import notify_incoming_branch_dispatch
from .models import MilkDistribution
from .notifications import notify_dispatch_created


@receiver(post_save, sender=MilkDistribution)
def milk_distribution_created_notify(sender, instance, created, **kwargs):
    if not created:
        return
    notify_dispatch_created(instance)
    if instance.is_branch_dispatch():
        notify_incoming_branch_dispatch(instance)
