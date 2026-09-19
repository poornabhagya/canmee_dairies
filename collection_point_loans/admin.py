from django.contrib import admin

from .models import (
    CollectionPointLoan,
    CollectionPointLoanActionLog,
    CollectionPointLoanPayment,
    CollectionPointLoanPaymentAllocation,
    CollectionPointLoanRepaymentSchedule,
)


class CollectionPointLoanActionLogInline(admin.TabularInline):
    model = CollectionPointLoanActionLog
    extra = 0
    readonly_fields = ("action", "performed_by", "performed_at", "notes", "details")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class CollectionPointLoanRepaymentInline(admin.TabularInline):
    model = CollectionPointLoanRepaymentSchedule
    extra = 0
    readonly_fields = (
        "installment_number",
        "due_date",
        "installment_amount",
        "paid_amount",
        "is_paid",
        "paid_on",
        "payment_method",
    )


class CollectionPointLoanPaymentAllocationInline(admin.TabularInline):
    model = CollectionPointLoanPaymentAllocation
    extra = 0
    readonly_fields = ("schedule", "amount")


@admin.register(CollectionPointLoan)
class CollectionPointLoanAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "branch",
        "collection_point",
        "loan_amount",
        "status",
        "loan_date",
        "first_repayment_date",
        "installment_count",
        "submitted_at",
        "approved_at",
        "rejected_at",
        "created_at",
    )
    list_filter = ("status", "branch")
    search_fields = ("collection_point__name", "collection_point__number", "point_reference")
    inlines = [CollectionPointLoanRepaymentInline, CollectionPointLoanActionLogInline]


@admin.register(CollectionPointLoanActionLog)
class CollectionPointLoanActionLogAdmin(admin.ModelAdmin):
    list_display = ("loan_label", "loan", "action", "performed_by", "performed_at")
    list_filter = ("action", "performed_at")
    search_fields = ("loan_label", "notes", "performed_by__username")
    readonly_fields = ("loan", "loan_label", "action", "performed_by", "performed_at", "notes", "details")


@admin.register(CollectionPointLoanPayment)
class CollectionPointLoanPaymentAdmin(admin.ModelAdmin):
    list_display = ("loan", "payment_date", "method", "amount", "created_by", "created_at")
    list_filter = ("method", "payment_date", "created_at")
    search_fields = ("loan__collection_point__name", "loan__collection_point__number", "note")
    readonly_fields = ("created_at",)
    inlines = [CollectionPointLoanPaymentAllocationInline]
