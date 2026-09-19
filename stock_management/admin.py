from django.contrib import admin

from .models import (
    BranchStock,
    MainStock,
    Product,
    StockCount,
    StockIssue,
    StockLog,
    StockTransfer,
    StockTransferLine,
    StockTransferNote,
)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "unit", "status", "updated_at")
    list_filter = ("category", "status", "updated_at")
    search_fields = ("name", "description")


@admin.register(MainStock)
class MainStockAdmin(admin.ModelAdmin):
    list_display = ("product", "quantity", "updated_at")
    list_filter = ("product", "updated_at")
    search_fields = ("product__name",)

    def save_model(self, request, obj, form, change):
        obj.full_clean()
        super().save_model(request, obj, form, change)


@admin.register(BranchStock)
class BranchStockAdmin(admin.ModelAdmin):
    list_display = ("branch", "product", "quantity", "updated_at")
    list_filter = ("branch", "product", "updated_at")
    search_fields = ("branch__name", "product__name")

    def save_model(self, request, obj, form, change):
        obj.full_clean()
        super().save_model(request, obj, form, change)


@admin.register(StockCount)
class StockCountAdmin(admin.ModelAdmin):
    list_display = ("count_number", "branch", "date", "status", "created_by", "posted_at")
    list_filter = ("status", "branch", "date")
    search_fields = ("count_number", "remarks")
    date_hierarchy = "date"
class StockTransferLineInline(admin.TabularInline):
    model = StockTransferLine
    extra = 0
    readonly_fields = ("unit_price",)


@admin.register(StockTransferNote)
class StockTransferNoteAdmin(admin.ModelAdmin):
    list_display = ("transfer_number", "from_branch", "to_branch", "date", "created_by")
    list_filter = ("from_branch", "to_branch", "date")
    search_fields = ("transfer_number", "remarks")
    date_hierarchy = "date"
    inlines = [StockTransferLineInline]


@admin.register(StockTransfer)
class StockTransferAdmin(admin.ModelAdmin):
    list_display = ("product", "quantity", "from_location", "to_branch", "date", "created_by")
    list_filter = ("product", "to_branch", "date")
    search_fields = ("product__name", "to_branch__name")
    date_hierarchy = "date"


@admin.register(StockIssue)
class StockIssueAdmin(admin.ModelAdmin):
    list_display = ("product", "quantity", "branch", "issued_to_type", "issued_to_id", "date")
    list_filter = ("product", "branch", "issued_to_type", "date")
    search_fields = ("product__name", "branch__name")
    date_hierarchy = "date"


@admin.register(StockLog)
class StockLogAdmin(admin.ModelAdmin):
    list_display = ("action_type", "product", "quantity", "source", "destination", "reference", "date")
    list_filter = ("action_type", "product", "date")
    search_fields = ("product__name", "source", "destination", "reference")
    date_hierarchy = "date"
