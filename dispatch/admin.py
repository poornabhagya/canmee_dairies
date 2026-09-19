from django.contrib import admin

from .models import (
    BuyerAccount,
    BuyerPayment,
    BuyerPaymentAllocation,
    BuyerTransaction,
    MilkDistribution,
)


@admin.register(MilkDistribution)
class MilkDistributionAdmin(admin.ModelAdmin):
    list_display = (
        "dispatch_no",
        "date",
        "buyer",
        "kg",
        "total_amount",
        "paid_amount",
        "payment_status",
        "status",
    )
    list_filter = ("status", "payment_status", "rate_unit")
    search_fields = ("dispatch_no", "buyer__name")


@admin.register(BuyerAccount)
class BuyerAccountAdmin(admin.ModelAdmin):
    list_display = ("buyer", "balance", "updated_at")
    search_fields = ("buyer__name",)


@admin.register(BuyerTransaction)
class BuyerTransactionAdmin(admin.ModelAdmin):
    list_display = ("date", "buyer", "transaction_type", "amount", "reference")
    list_filter = ("transaction_type",)
    search_fields = ("buyer__name", "reference", "description")


class BuyerPaymentAllocationInline(admin.TabularInline):
    model = BuyerPaymentAllocation
    extra = 0


@admin.register(BuyerPayment)
class BuyerPaymentAdmin(admin.ModelAdmin):
    list_display = ("date", "buyer", "amount", "reference", "created_by")
    search_fields = ("buyer__name", "reference", "note")
    inlines = [BuyerPaymentAllocationInline]
