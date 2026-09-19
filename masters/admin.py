from django.contrib import admin
from .models import (
    Route,
    CollectionPoint,
    CollectionPointBankAccount,
    FarmerBankAccount,
    CompanyProfile,
    CompanyBankAccount,
    Bank,
    Buyer,
    BuyerRateHistory,
    Farmer,
)


@admin.register(CompanyProfile)
class CompanyProfileAdmin(admin.ModelAdmin):
    list_display = ("name", "phone", "email", "updated_at")


class CompanyBankAccountInline(admin.TabularInline):
    model = CompanyBankAccount
    extra = 1
    fields = (
        "account_name",
        "account_number",
        "bank_name",
        "bank_code",
        "bank_branch",
        "branch_code",
        "is_primary",
        "is_active",
    )


CompanyProfileAdmin.inlines = [CompanyBankAccountInline]


@admin.register(CompanyBankAccount)
class CompanyBankAccountAdmin(admin.ModelAdmin):
    list_display = (
        "account_name",
        "account_number",
        "bank_name",
        "bank_code",
        "is_primary",
        "is_active",
    )
    list_filter = ("is_primary", "is_active", "bank_name")
    search_fields = ("account_name", "account_number", "bank_name")


@admin.register(Bank)
class BankAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "is_active", "sort_order")
    list_editable = ("code", "is_active", "sort_order")
    list_filter = ("is_active",)
    search_fields = ("name", "code")
    ordering = ("sort_order", "name")


class CollectionPointBankAccountInline(admin.TabularInline):
    model = CollectionPointBankAccount
    extra = 1
    fields = (
        "account_name",
        "account_number",
        "bank_name",
        "bank_code",
        "bank_branch",
        "branch_code",
        "is_primary",
    )


class FarmerBankAccountInline(admin.TabularInline):
    model = FarmerBankAccount
    extra = 1
    fields = (
        "account_name",
        "account_number",
        "bank_name",
        "bank_code",
        "bank_branch",
        "branch_code",
        "is_primary",
    )


@admin.register(CollectionPoint)
class CollectionPointAdmin(admin.ModelAdmin):
    list_display = ("number", "name", "route")
    search_fields = ("number", "name")
    inlines = [CollectionPointBankAccountInline]


@admin.register(Farmer)
class FarmerAdmin(admin.ModelAdmin):
    list_display = (
        "common_name",
        "full_name",
        "route",
        "collection_point",
        "nic",
        "date_of_birth",
        "mobile",
        "whatsapp",
        "email",
    )
    list_filter = ("route",)
    search_fields = (
        "common_name",
        "full_name",
        "nic",
        "mobile",
        "whatsapp",
        "email",
        "registration_number",
    )
    readonly_fields = ("route",)
    inlines = [FarmerBankAccountInline]


@admin.register(Buyer)
class BuyerAdmin(admin.ModelAdmin):
    list_display = ("name", "rate", "rate_unit", "phone", "email")
    search_fields = ("name", "contact_person", "phone", "email")


@admin.register(BuyerRateHistory)
class BuyerRateHistoryAdmin(admin.ModelAdmin):
    list_display = ("buyer", "rate", "rate_unit", "effective_from")
    list_filter = ("rate_unit", "effective_from")
    search_fields = ("buyer__name",)


admin.site.register(Route)
