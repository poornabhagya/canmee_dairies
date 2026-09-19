"""GRN audit helpers for action logs and snapshots."""

from decimal import Decimal

from .models import GRN, GRNActionLog


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


def grn_audit_snapshot(grn):
    return {
        "grn_number": grn.grn_number,
        "supplier_id": grn.supplier_id,
        "supplier": str(grn.supplier) if grn.supplier_id else "",
        "branch_id": grn.branch_id,
        "branch": str(grn.branch) if grn.branch_id else "",
        "grn_type": grn.grn_type,
        "date": _serialize_value(grn.date),
        "status": grn.status,
        "total_amount": _serialize_value(grn.total_amount),
        "discount_scope": grn.discount_scope,
        "document_discount_percent": _serialize_value(grn.document_discount_percent),
        "document_discount_amount": _serialize_value(grn.document_discount_amount),
        "line_count": grn.items.count() if grn.pk else 0,
    }


def grn_audit_diff(before, after):
    changes = {}
    for key, new_value in after.items():
        old_value = before.get(key)
        if old_value != new_value:
            changes[key] = {"from": old_value, "to": new_value}
    return changes


def log_grn_action(grn, *, user, action, notes="", details=None):
    row = GRNActionLog.objects.create(
        grn=grn if getattr(grn, "pk", None) else None,
        grn_number=(grn.grn_number if grn is not None else "") or "",
        action=action,
        performed_by=user if getattr(user, "is_authenticated", False) else None,
        notes=notes or "",
        details=details or {},
    )
    try:
        from user_management.audit import log_audit_event

        log_audit_event(
            user=user if getattr(user, "is_authenticated", False) else None,
            action=action,
            module="GRN",
            summary=f"{(grn.grn_number if grn is not None else '') or 'GRN'} · {str(action).replace('_', ' ')}",
            object_type="grn",
            object_id=str(getattr(grn, "pk", "") or ""),
            source="grn",
            details={"notes": notes or "", **(details if isinstance(details, dict) else {})},
        )
    except Exception:
        pass
    return row


def log_grn_deleted(grn, *, user, notes=""):
    return log_grn_action(
        grn,
        user=user,
        action=GRNActionLog.Action.DELETED,
        notes=notes,
        details={"snapshot": grn_audit_snapshot(grn)},
    )
