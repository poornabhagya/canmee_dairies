from django.contrib import admin

from .models import FarmerLoan, FarmerLoanActionLog, FarmerLoanRepaymentSchedule


class FarmerLoanActionLogInline(admin.TabularInline):
    model = FarmerLoanActionLog
    extra = 0
    readonly_fields = ("action", "performed_by", "performed_at", "notes", "details")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


class FarmerLoanRepaymentInline(admin.TabularInline):
    model = FarmerLoanRepaymentSchedule
    extra = 0
    readonly_fields = ("installment_number", "due_date", "installment_amount", "paid_amount", "is_paid", "paid_on")


@admin.register(FarmerLoan)
class FarmerLoanAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "branch",
        "farmer",
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
    search_fields = ("farmer__full_name", "farmer__common_name", "farm_registration_number")
    inlines = [FarmerLoanRepaymentInline, FarmerLoanActionLogInline]


@admin.register(FarmerLoanActionLog)
class FarmerLoanActionLogAdmin(admin.ModelAdmin):
    list_display = ("loan_label", "loan", "action", "performed_by", "performed_at")
    list_filter = ("action", "performed_at")
    search_fields = ("loan_label", "notes", "performed_by__username")
    readonly_fields = ("loan", "loan_label", "action", "performed_by", "performed_at", "notes", "details")
