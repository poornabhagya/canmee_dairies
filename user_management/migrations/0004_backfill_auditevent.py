from django.db import migrations


def _user_display(user):
    if user is None:
        return "", ""
    username = getattr(user, "username", "") or ""
    first = getattr(user, "first_name", "") or ""
    last = getattr(user, "last_name", "") or ""
    display = f"{first} {last}".strip() or username
    return username, display


def _bulk(AuditEvent, rows):
    if not rows:
        return
    AuditEvent.objects.bulk_create(rows, batch_size=500)


def forwards(apps, schema_editor):
    AuditEvent = apps.get_model("user_management", "AuditEvent")
    User = apps.get_model("auth", "User")
    users = {u.pk: u for u in User.objects.all()}

    FarmerLoanActionLog = apps.get_model("farmer_loans", "FarmerLoanActionLog")
    rows = []
    for log in FarmerLoanActionLog.objects.all().iterator():
        user = users.get(log.performed_by_id)
        username, display = _user_display(user)
        rows.append(
            AuditEvent(
                created_at=log.performed_at,
                user_id=log.performed_by_id,
                username=username,
                user_display=display,
                action=log.action,
                module="Farmer loans",
                summary=f"{log.loan_label or 'Loan'} · {log.action.replace('_', ' ')}",
                object_type="farmer_loan",
                object_id=str(log.loan_id or ""),
                source="farmer_loan",
                details={"notes": log.notes or "", "legacy_id": log.pk, **(log.details if isinstance(log.details, dict) else {})},
            )
        )
    _bulk(AuditEvent, rows)

    CollectionPointLoanActionLog = apps.get_model(
        "collection_point_loans", "CollectionPointLoanActionLog"
    )
    rows = []
    for log in CollectionPointLoanActionLog.objects.all().iterator():
        user = users.get(log.performed_by_id)
        username, display = _user_display(user)
        rows.append(
            AuditEvent(
                created_at=log.performed_at,
                user_id=log.performed_by_id,
                username=username,
                user_display=display,
                action=log.action,
                module="Point loans",
                summary=f"{log.loan_label or 'Loan'} · {log.action.replace('_', ' ')}",
                object_type="collection_point_loan",
                object_id=str(log.loan_id or ""),
                source="point_loan",
                details={"notes": log.notes or "", "legacy_id": log.pk, **(log.details if isinstance(log.details, dict) else {})},
            )
        )
    _bulk(AuditEvent, rows)

    GRNActionLog = apps.get_model("suppliers", "GRNActionLog")
    rows = []
    for log in GRNActionLog.objects.all().iterator():
        user = users.get(log.performed_by_id) if log.performed_by_id else None
        username, display = _user_display(user)
        rows.append(
            AuditEvent(
                created_at=log.performed_at,
                user_id=log.performed_by_id,
                username=username,
                user_display=display,
                action=log.action,
                module="GRN",
                summary=f"{log.grn_number or 'GRN'} · {log.action.replace('_', ' ')}",
                object_type="grn",
                object_id=str(log.grn_id or ""),
                source="grn",
                details={"notes": log.notes or "", "legacy_id": log.pk, **(log.details if isinstance(log.details, dict) else {})},
            )
        )
    _bulk(AuditEvent, rows)

    CollectionPointFeeHistory = apps.get_model("masters", "CollectionPointFeeHistory")
    CollectionPoint = apps.get_model("masters", "CollectionPoint")
    point_names = dict(CollectionPoint.objects.values_list("id", "name"))
    rows = []
    for row in CollectionPointFeeHistory.objects.all().iterator():
        user = users.get(row.changed_by_id) if row.changed_by_id else None
        username, display = _user_display(user)
        point_name = point_names.get(row.collection_point_id, f"Point #{row.collection_point_id}")
        rows.append(
            AuditEvent(
                created_at=row.effective_at or row.created_at,
                user_id=row.changed_by_id,
                username=username,
                user_display=display,
                action="updated",
                module="Collection points",
                summary=(
                    f"{point_name} fees · collector {row.collector_fee} / additional {row.additional}"
                ),
                object_type="collection_point",
                object_id=str(row.collection_point_id or ""),
                source="fee_history",
                details={
                    "collector_fee": str(row.collector_fee),
                    "additional": str(row.additional),
                    "legacy_id": row.pk,
                },
            )
        )
    _bulk(AuditEvent, rows)

    LogEntry = apps.get_model("admin", "LogEntry")
    ContentType = apps.get_model("contenttypes", "ContentType")
    action_map = {1: "created", 2: "updated", 3: "deleted"}
    ct_labels = {}
    for ct in ContentType.objects.all():
        ct_labels[ct.pk] = (ct.app_label.replace("_", " ").title(), ct.model)
    rows = []
    for entry in LogEntry.objects.all().iterator():
        user = users.get(entry.user_id) if entry.user_id else None
        username, display = _user_display(user)
        module, model = ct_labels.get(entry.content_type_id, ("Django admin", ""))
        rows.append(
            AuditEvent(
                created_at=entry.action_time,
                user_id=entry.user_id,
                username=username,
                user_display=display,
                action=action_map.get(entry.action_flag, "updated"),
                module="Django admin",
                summary=f"{entry.object_repr}"[:255],
                object_type=model,
                object_id=str(entry.object_id or "")[:64],
                source="admin",
                details={
                    "change_message": entry.change_message or "",
                    "legacy_id": entry.pk,
                    "app": module,
                },
            )
        )
    _bulk(AuditEvent, rows)


def backwards(apps, schema_editor):
    AuditEvent = apps.get_model("user_management", "AuditEvent")
    AuditEvent.objects.filter(
        source__in=["farmer_loan", "point_loan", "grn", "fee_history", "admin"]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("user_management", "0003_auditevent"),
        ("farmer_loans", "0010_installment_method"),
        ("collection_point_loans", "0006_payment_ledger"),
        ("suppliers", "0016_grn_audit_fields_and_action_log"),
        ("masters", "0028_collectionpointfeehistory"),
        ("admin", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
