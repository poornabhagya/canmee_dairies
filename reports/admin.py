from django.contrib import admin

from .models import (
    CollectionPointPeriodPayment,
    CollectionPointPaymentCorrection,
    CollectionPointPaymentLoanDeductionSetting,
    CollectionPointPaymentSheetRecord,
    CollectionPointPreviousOutstanding,
    FarmerPaymentLoanDeductionSetting,
    FarmerPaymentSheetRecord,
    FarmerPeriodPayment,
)


@admin.register(CollectionPointPaymentLoanDeductionSetting)
class CollectionPointPaymentLoanDeductionSettingAdmin(admin.ModelAdmin):
    list_display = (
        "collection_point",
        "period_start",
        "period_end",
        "deduct_loan",
        "updated_by",
        "updated_at",
    )
    list_filter = ("deduct_loan", "period_start", "period_end")
    search_fields = ("collection_point__name", "collection_point__number")
    raw_id_fields = ("collection_point", "updated_by")


@admin.register(FarmerPaymentLoanDeductionSetting)
class FarmerPaymentLoanDeductionSettingAdmin(admin.ModelAdmin):
    list_display = ("farmer", "period_start", "period_end", "deduct_loan", "updated_by", "updated_at")
    list_filter = ("deduct_loan", "period_start", "period_end")
    search_fields = ("farmer__common_name", "farmer__full_name", "farmer__registration_number")
    raw_id_fields = ("farmer", "updated_by")


@admin.register(FarmerPeriodPayment)
class FarmerPeriodPaymentAdmin(admin.ModelAdmin):
    list_display = (
        "farmer",
        "period_start",
        "period_end",
        "amount",
        "expected_amount",
        "rate",
        "paid_at",
        "recorded_by",
    )
    list_filter = ("period_start", "period_end", "paid_at")
    search_fields = ("farmer__common_name", "farmer__full_name", "farmer__registration_number", "note")
    raw_id_fields = ("farmer", "recorded_by")


@admin.register(FarmerPaymentSheetRecord)
class FarmerPaymentSheetRecordAdmin(admin.ModelAdmin):
    list_display = (
        "period_start",
        "period_end",
        "branch_key",
        "farmer_count",
        "total_gross",
        "total_paid",
        "last_paid_at",
    )
    list_filter = ("period_start", "period_end", "last_paid_at")
    search_fields = ("branch_key",)


@admin.register(CollectionPointPeriodPayment)
class CollectionPointPeriodPaymentAdmin(admin.ModelAdmin):
    list_display = (
        "collection_point",
        "period_start",
        "period_end",
        "amount",
        "expected_amount",
        "rate",
        "collector_fee_rate",
        "paid_at",
        "recorded_by",
    )
    list_filter = ("period_start", "period_end", "paid_at")
    search_fields = ("collection_point__name", "collection_point__number", "note")
    raw_id_fields = ("collection_point", "recorded_by")


@admin.register(CollectionPointPaymentSheetRecord)
class CollectionPointPaymentSheetRecordAdmin(admin.ModelAdmin):
    list_display = (
        "period_start",
        "period_end",
        "branch_key",
        "point_count",
        "total_gross",
        "total_paid",
        "last_paid_at",
    )
    list_filter = ("period_start", "period_end", "last_paid_at")
    search_fields = ("branch_key",)


@admin.register(CollectionPointPreviousOutstanding)
class CollectionPointPreviousOutstandingAdmin(admin.ModelAdmin):
    list_display = (
        "collection_point",
        "amount",
        "originated_period_start",
        "originated_period_end",
        "available_on",
        "is_recovered",
        "recovered_at",
    )
    list_filter = ("is_recovered", "available_on", "originated_period_start")
    search_fields = ("collection_point__name", "collection_point__number")
    raw_id_fields = ("collection_point", "originated_payment", "created_by")


@admin.register(CollectionPointPaymentCorrection)
class CollectionPointPaymentCorrectionAdmin(admin.ModelAdmin):
    list_display = (
        "collection_point",
        "period_start",
        "period_end",
        "amount",
        "updated_by",
        "updated_at",
    )
    list_filter = ("period_start", "period_end")
    search_fields = ("collection_point__name", "collection_point__number", "note")
    raw_id_fields = ("collection_point", "updated_by")
