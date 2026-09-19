from django.contrib import admin

from .models import Branch, BranchMilkStock, BranchMilkStockAdjustment


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "branch_manager", "maximum_milk_storage", "collection_unit")
    search_fields = ("code", "name", "location", "manager_contact", "branch_contact")
    filter_horizontal = ("users", "employees")
    autocomplete_fields = ("branch_manager",)


@admin.register(BranchMilkStock)
class BranchMilkStockAdmin(admin.ModelAdmin):
    list_display = ("branch", "collected_kg", "dispatched_kg", "current_kg", "updated_at")
    search_fields = ("branch__code", "branch__name")


@admin.register(BranchMilkStockAdjustment)
class BranchMilkStockAdjustmentAdmin(admin.ModelAdmin):
    list_display = ("date", "branch", "adjustment_type", "kg", "liters", "created_by", "created_at")
    list_filter = ("adjustment_type", "date", "branch")
    search_fields = ("branch__code", "branch__name", "remarks")
