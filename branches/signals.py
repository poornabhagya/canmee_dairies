from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from dispatch.models import MilkDistribution
from milk_collections.models import MilkCollection
from suppliers.models import RawMilkSupplierCollection

from .models import BranchMilkStockAdjustment
from .services import recalculate_all_branch_stocks


@receiver(post_save, sender=MilkCollection)
def collection_saved_update_stock(sender, instance, **kwargs):
    recalculate_all_branch_stocks()


@receiver(post_delete, sender=MilkCollection)
def collection_deleted_update_stock(sender, instance, **kwargs):
    recalculate_all_branch_stocks()


@receiver(post_save, sender=MilkDistribution)
def distribution_saved_update_stock(sender, instance, **kwargs):
    recalculate_all_branch_stocks()


@receiver(post_delete, sender=MilkDistribution)
def distribution_deleted_update_stock(sender, instance, **kwargs):
    recalculate_all_branch_stocks()


@receiver(post_save, sender=RawMilkSupplierCollection)
def raw_milk_collection_saved_update_stock(sender, instance, **kwargs):
    recalculate_all_branch_stocks()


@receiver(post_delete, sender=RawMilkSupplierCollection)
def raw_milk_collection_deleted_update_stock(sender, instance, **kwargs):
    recalculate_all_branch_stocks()


@receiver(post_save, sender=BranchMilkStockAdjustment)
def stock_adjustment_saved_update_stock(sender, instance, **kwargs):
    recalculate_all_branch_stocks()


@receiver(post_delete, sender=BranchMilkStockAdjustment)
def stock_adjustment_deleted_update_stock(sender, instance, **kwargs):
    recalculate_all_branch_stocks()
