from django.contrib import admin

from .models import (
    Asset,
    AssetCategory,
    AssetDepreciationEntry,
    AssetLabelBatch,
    AssetLocation,
    AssetModuleSettings,
    AssetTransfer,
    AssetType,
    AssetValuation,
    AssetVerification,
    DepreciationPolicy,
)


@admin.register(AssetModuleSettings)
class AssetModuleSettingsAdmin(admin.ModelAdmin):
    list_display = ("enable_depreciation", "default_label_prefix", "updated_at", "updated_by")


@admin.register(AssetType)
class AssetTypeAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("code", "name")


@admin.register(AssetCategory)
class AssetCategoryAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "asset_type", "parent", "is_active")
    list_filter = ("asset_type", "is_active")
    search_fields = ("code", "name")


@admin.register(AssetLocation)
class AssetLocationAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "branch", "parent", "is_active")
    list_filter = ("branch", "is_active")
    search_fields = ("code", "name")


@admin.register(DepreciationPolicy)
class DepreciationPolicyAdmin(admin.ModelAdmin):
    list_display = ("name", "method", "useful_life_months", "annual_rate_percent", "is_active")
    list_filter = ("method", "is_active")
    search_fields = ("name",)


class AssetTransferInline(admin.TabularInline):
    model = AssetTransfer
    extra = 0
    readonly_fields = (
        "from_branch",
        "to_branch",
        "from_location",
        "to_location",
        "transferred_at",
        "note",
        "transferred_by",
    )
    can_delete = False


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = (
        "asset_tag",
        "name",
        "asset_type",
        "branch",
        "category",
        "status",
        "current_value",
        "location",
    )
    list_filter = ("asset_type", "status", "condition", "branch", "category")
    search_fields = ("asset_tag", "name", "serial_number")
    autocomplete_fields = ("asset_type", "category", "location", "custodian", "depreciation_policy")
    inlines = [AssetTransferInline]


@admin.register(AssetVerification)
class AssetVerificationAdmin(admin.ModelAdmin):
    list_display = ("asset", "result", "verified_at", "verified_by", "assessed_value")
    list_filter = ("result",)


@admin.register(AssetValuation)
class AssetValuationAdmin(admin.ModelAdmin):
    list_display = ("asset", "valued_at", "previous_value", "new_value", "valued_by")


@admin.register(AssetDepreciationEntry)
class AssetDepreciationEntryAdmin(admin.ModelAdmin):
    list_display = (
        "asset",
        "period_start",
        "period_end",
        "amount",
        "book_value_after",
        "run_by",
    )
    list_filter = ("period_start",)


@admin.register(AssetLabelBatch)
class AssetLabelBatchAdmin(admin.ModelAdmin):
    list_display = ("id", "label_count", "created_at", "created_by", "note")
    filter_horizontal = ("assets",)


@admin.register(AssetTransfer)
class AssetTransferAdmin(admin.ModelAdmin):
    list_display = ("asset", "from_branch", "to_branch", "transferred_at", "transferred_by")
