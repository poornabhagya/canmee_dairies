from django.contrib import admin

from .models import (
    ConsumptionSettlement,
    FarmerGoodsIssue,
    FarmerGoodsModuleSettings,
    FarmerGoodsStock,
    GRN,
    GRNActionLog,
    GRNItem,
    InventoryUsage,
    RawMilkSupplierCollection,
    RawMilkSupplierPayment,
    RawMilkSupplierPaymentAllocation,
    Supplier,
    SupplierAccount,
    SupplierRateHistory,
    SupplierTransaction,
)


@admin.register(FarmerGoodsModuleSettings)
class FarmerGoodsModuleSettingsAdmin(admin.ModelAdmin):
    list_display = ("enable_accept_notifications", "updated_at", "updated_by")


class GRNItemInline(admin.TabularInline):
    model = GRNItem
    extra = 1
    fields = (
        "product",
        "quantity",
        "free_quantity",
        "unit_price",
        "issuing_price",
        "line_discount_percent",
        "line_discount_amount",
        "total_price",
    )
    readonly_fields = ("total_price",)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "rate", "rate_unit", "contact_number", "status", "created_at")
    list_filter = ("category", "status", "rate_unit", "created_at")
    search_fields = ("name", "contact_number", "email")


@admin.register(SupplierRateHistory)
class SupplierRateHistoryAdmin(admin.ModelAdmin):
    list_display = ("supplier", "rate", "rate_unit", "effective_from", "created_at")
    list_filter = ("rate_unit", "effective_from")
    search_fields = ("supplier__name",)


@admin.register(SupplierAccount)
class SupplierAccountAdmin(admin.ModelAdmin):
    list_display = ("supplier", "balance", "updated_at")
    list_filter = ("supplier__category", "updated_at")
    search_fields = ("supplier__name",)


@admin.register(SupplierTransaction)
class SupplierTransactionAdmin(admin.ModelAdmin):
    list_display = ("supplier", "date", "transaction_type", "amount", "reference")
    list_filter = ("supplier", "supplier__category", "transaction_type", "date")
    search_fields = ("supplier__name", "reference", "description")
    date_hierarchy = "date"


@admin.register(GRN)
class GRNAdmin(admin.ModelAdmin):
    list_display = (
        "grn_number",
        "supplier",
        "branch",
        "grn_type",
        "date",
        "total_amount",
        "status",
        "created_by",
        "confirmed_by",
        "discount_scope",
    )
    list_filter = ("supplier", "supplier__category", "grn_type", "status", "discount_scope", "date")
    search_fields = ("grn_number", "supplier__name")
    readonly_fields = ("created_at", "updated_at", "confirmed_at", "created_by", "confirmed_by")
    inlines = [GRNItemInline]
    date_hierarchy = "date"


@admin.register(GRNActionLog)
class GRNActionLogAdmin(admin.ModelAdmin):
    list_display = ("grn_number", "action", "performed_by", "performed_at")
    list_filter = ("action", "performed_at")
    search_fields = ("grn_number", "performed_by__username")
    readonly_fields = ("grn", "grn_number", "action", "performed_by", "performed_at", "notes", "details")
    date_hierarchy = "performed_at"


@admin.register(FarmerGoodsStock)
class FarmerGoodsStockAdmin(admin.ModelAdmin):
    list_display = ("product", "quantity", "updated_at")
    list_filter = ("updated_at",)
    search_fields = ("product__name",)

    def save_model(self, request, obj, form, change):
        obj.full_clean()
        super().save_model(request, obj, form, change)


@admin.register(FarmerGoodsIssue)
class FarmerGoodsIssueAdmin(admin.ModelAdmin):
    list_display = ("product", "quantity", "from_branch", "issue_to_type", "issue_to_id", "date")
    list_filter = ("issue_to_type", "date")
    search_fields = ("product__name",)
    date_hierarchy = "date"


@admin.register(ConsumptionSettlement)
class ConsumptionSettlementAdmin(admin.ModelAdmin):
    list_display = ("product", "quantity", "source_type", "source_id", "date")
    list_filter = ("source_type", "date")
    search_fields = ("product__name",)
    date_hierarchy = "date"


@admin.register(InventoryUsage)
class InventoryUsageAdmin(admin.ModelAdmin):
    list_display = ("product", "quantity", "usage_reason", "date")
    list_filter = ("date",)
    search_fields = ("product__name", "usage_reason")
    date_hierarchy = "date"


@admin.register(RawMilkSupplierCollection)
class RawMilkSupplierCollectionAdmin(admin.ModelAdmin):
    list_display = (
        "date",
        "branch",
        "supplier",
        "dispatch_no",
        "status",
        "payment_status",
        "total_amount",
        "paid_amount",
        "kg",
        "liters",
        "created_by",
        "created_at",
    )
    list_filter = ("date", "branch", "supplier", "status", "payment_status")
    search_fields = ("supplier__name", "branch__name", "branch__code", "dispatch_no", "browser_number")


class RawMilkSupplierPaymentAllocationInline(admin.TabularInline):
    model = RawMilkSupplierPaymentAllocation
    extra = 0


@admin.register(RawMilkSupplierPayment)
class RawMilkSupplierPaymentAdmin(admin.ModelAdmin):
    list_display = ("date", "supplier", "amount", "reference", "created_by")
    search_fields = ("supplier__name", "reference", "note")
    inlines = [RawMilkSupplierPaymentAllocationInline]
