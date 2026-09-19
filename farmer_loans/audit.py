from decimal import Decimal

from .models import FarmerLoan, FarmerLoanActionLog


def _serialize_value(value):
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "pk"):
        return value.pk
    return str(value)


def loan_audit_snapshot(loan):
    return {
        "branch_id": loan.branch_id,
        "branch": str(loan.branch) if loan.branch_id else "",
        "farmer_id": loan.farmer_id,
        "farmer": str(loan.farmer) if loan.farmer_id else "",
        "farm_registration_number": loan.farm_registration_number,
        "average_monthly_milk_production": _serialize_value(loan.average_monthly_milk_production),
        "average_monthly_milk_income": _serialize_value(loan.average_monthly_milk_income),
        "loan_amount": _serialize_value(loan.loan_amount),
        "loan_date": _serialize_value(loan.loan_date),
        "first_repayment_date": _serialize_value(loan.first_repayment_date),
        "installment_count": loan.installment_count,
        "requested_installment_amount": _serialize_value(loan.requested_installment_amount),
        "reason": loan.reason,
        "status": loan.status,
    }


def loan_audit_diff(before, after):
    changes = {}
    for key, new_value in after.items():
        old_value = before.get(key)
        if old_value != new_value:
            changes[key] = {"from": old_value, "to": new_value}
    return changes


def log_farmer_loan_action(loan, *, user, action, notes="", details=None):
    FarmerLoanActionLog.objects.create(
        loan=loan,
        loan_label=f"Loan #{loan.pk}" if loan.pk else "",
        action=action,
        performed_by=user,
        notes=notes or "",
        details=details or {},
    )
    try:
        from user_management.audit import log_audit_event

        log_audit_event(
            user=user,
            action=action,
            module="Farmer loans",
            summary=f"{'Loan #' + str(loan.pk) if loan.pk else 'Loan'} · {str(action).replace('_', ' ')}",
            object_type="farmer_loan",
            object_id=str(loan.pk or ""),
            source="farmer_loan",
            details={"notes": notes or "", **(details if isinstance(details, dict) else {})},
        )
    except Exception:
        pass


def log_farmer_loan_deleted(loan, *, user, notes=""):
    log_farmer_loan_action(
        loan,
        user=user,
        action=FarmerLoanActionLog.Action.DELETED,
        notes=notes,
        details={"snapshot": loan_audit_snapshot(loan)},
    )
