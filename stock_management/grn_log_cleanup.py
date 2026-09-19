"""Detect and remove duplicate StockLog GRN rows (keep one per confirmed GRN line)."""

from collections import defaultdict
from datetime import datetime, time
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from stock_management.models import StockLog
from suppliers.models import GRN, GRNItem


def _grn_log_at(grn):
    """Best-effort StockLog timestamp from GRN business date."""
    day = grn.date or timezone.localdate()
    dt = datetime.combine(day, time.min)
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt)
    return dt


def find_missing_grn_stock_logs(*, product_id=None, branch_id=None):
    """
    Confirmed GRN lines with no StockLog reference grn:{id} for that product.
    These make Stock History balance disagree with GRNs / FARMER GOODS pool.
    """
    item_qs = (
        GRNItem.objects.filter(grn__status=GRN.Status.CONFIRMED)
        .select_related("grn__supplier", "grn__branch", "product")
        .order_by("grn__date", "grn_id", "id")
    )
    if product_id:
        item_qs = item_qs.filter(product_id=product_id)
    if branch_id:
        item_qs = item_qs.filter(grn__branch_id=branch_id)

    rows = []
    for item in item_qs.iterator(chunk_size=300):
        ref = f"grn:{item.grn_id}"
        has_log = StockLog.objects.filter(
            action_type=StockLog.ActionType.GRN,
            product_id=item.product_id,
            reference=ref,
        ).exists()
        if has_log:
            continue
        grn = item.grn
        source = f"Supplier: {grn.supplier.name}" if grn.supplier_id else "Supplier"
        destination = f"Branch: {grn.branch.name}" if grn.branch_id else "Main"
        rows.append(
            {
                "grn_id": grn.id,
                "grn_number": grn.grn_number,
                "product_id": item.product_id,
                "product_name": item.product.name,
                "quantity": item.quantity,
                "source": source,
                "destination": destination,
                "grn_date": grn.date,
                "branch_name": grn.branch.name if grn.branch_id else "—",
                "reference": ref,
            }
        )
    return rows


@transaction.atomic
def apply_missing_grn_stock_log_backfill(*, product_id=None, branch_id=None, actor=None):
    if actor is not None and (
        not getattr(actor, "is_superuser", False) or not actor.is_active
    ):
        from django.core.exceptions import ValidationError

        raise ValidationError("Only an active superuser can backfill missing GRN stock logs.")

    rows = find_missing_grn_stock_logs(product_id=product_id, branch_id=branch_id)
    created = 0
    for row in rows:
        # Re-check under lock path via atomic block.
        if StockLog.objects.filter(
            action_type=StockLog.ActionType.GRN,
            product_id=row["product_id"],
            reference=row["reference"],
        ).exists():
            continue
        grn = GRN.objects.filter(pk=row["grn_id"]).first()
        at = _grn_log_at(grn) if grn else timezone.now()
        log = StockLog.objects.create(
            product_id=row["product_id"],
            quantity=row["quantity"],
            action_type=StockLog.ActionType.GRN,
            source=row["source"],
            destination=row["destination"],
            reference=row["reference"],
        )
        StockLog.objects.filter(pk=log.pk).update(date=at)
        created += 1
    return {"rows": rows, "created": created, "count": len(rows)}


def _log_group_key(log):
    return (
        log.product_id,
        Decimal(str(log.quantity or 0)),
        (log.source or "").strip(),
        (log.destination or "").strip(),
    )


def _item_group_key(item):
    grn = item.grn
    source = f"Supplier: {grn.supplier.name}" if grn.supplier_id else "Supplier"
    destination = f"Branch: {grn.branch.name}" if grn.branch_id else "Main"
    return (
        item.product_id,
        Decimal(str(item.quantity or 0)),
        source,
        destination,
    )


def find_duplicate_grn_stock_logs(*, product_id=None, branch_id=None):
    """
    Return plan dict:
      keep_ids, delete_ids, groups (preview rows), set_references {log_id: 'grn:X'}
    """
    item_qs = GRNItem.objects.filter(grn__status=GRN.Status.CONFIRMED).select_related(
        "grn__supplier", "grn__branch", "product"
    )
    log_qs = StockLog.objects.filter(action_type=StockLog.ActionType.GRN).select_related(
        "product"
    )
    if product_id:
        item_qs = item_qs.filter(product_id=product_id)
        log_qs = log_qs.filter(product_id=product_id)
    if branch_id:
        item_qs = item_qs.filter(grn__branch_id=branch_id)
        # Destination filter: Branch: {name}
        from branches.models import Branch

        branch = Branch.objects.filter(pk=branch_id).first()
        if branch:
            log_qs = log_qs.filter(destination=f"Branch: {branch.name}")

    expected_by_key = defaultdict(list)  # key -> [GRNItem, ...]
    for item in item_qs.iterator(chunk_size=300):
        expected_by_key[_item_group_key(item)].append(item)

    logs_by_key = defaultdict(list)
    for log in log_qs.order_by("date", "id").iterator(chunk_size=500):
        logs_by_key[_log_group_key(log)].append(log)

    keep_ids = []
    delete_ids = []
    set_references = {}
    groups = []

    all_keys = set(expected_by_key) | set(logs_by_key)
    for key in sorted(all_keys, key=lambda k: (k[0] or 0, str(k[1]), k[2], k[3])):
        product_id_k, qty, source, destination = key
        items = expected_by_key.get(key, [])
        logs = logs_by_key.get(key, [])
        expected = len(items)
        actual = len(logs)
        if actual <= expected:
            # Still assign missing references on kept logs when possible.
            for log, item in zip(logs, items):
                keep_ids.append(log.id)
                want_ref = f"grn:{item.grn_id}"
                if (log.reference or "") != want_ref:
                    set_references[log.id] = want_ref
            continue
        if expected == 0 and actual > 0:
            # Orphan GRN logs with no matching confirmed GRN line.
            delete_ids.extend(log.id for log in logs)
            product_name = (
                logs[0].product.name if logs and logs[0].product_id else f"#{product_id_k}"
            )
            groups.append(
                {
                    "product_id": product_id_k,
                    "product_name": product_name,
                    "quantity": qty,
                    "source": source,
                    "destination": destination,
                    "expected": 0,
                    "actual": actual,
                    "delete_count": actual,
                    "keep_count": 0,
                    "kind": "orphan",
                }
            )
            continue

        # Keep earliest N logs; prefer ones that already have the correct reference.
        item_refs = [f"grn:{it.grn_id}" for it in items]
        scored = []
        for idx, log in enumerate(logs):
            ref = (log.reference or "").strip()
            prefer = 0 if ref in item_refs else 1
            scored.append((prefer, log.date, log.id, log))
        scored.sort()
        keep_logs = [t[3] for t in scored[:expected]]
        drop_logs = [t[3] for t in scored[expected:]]

        # Assign references to kept logs in item order.
        keep_logs_sorted = sorted(keep_logs, key=lambda l: (l.date, l.id))
        for log, item in zip(keep_logs_sorted, items):
            keep_ids.append(log.id)
            want_ref = f"grn:{item.grn_id}"
            if (log.reference or "") != want_ref:
                set_references[log.id] = want_ref
        delete_ids.extend(log.id for log in drop_logs)

        product_name = (
            (items[0].product.name if items else None)
            or (logs[0].product.name if logs else None)
            or f"#{product_id_k}"
        )
        groups.append(
            {
                "product_id": product_id_k,
                "product_name": product_name,
                "quantity": qty,
                "source": source,
                "destination": destination,
                "expected": expected,
                "actual": actual,
                "delete_count": len(drop_logs),
                "keep_count": expected,
                "kind": "duplicate",
            }
        )

    # Collapse true duplicate logs: same grn:{id} AND same product (multi-line GRNs
    # share one grn:id across products — those must not be treated as duplicates).
    ref_map = defaultdict(list)
    for log in StockLog.objects.filter(
        action_type=StockLog.ActionType.GRN, reference__startswith="grn:"
    ).select_related("product").order_by("date", "id"):
        if product_id and log.product_id != product_id:
            continue
        if branch_id:
            from branches.models import Branch

            branch = Branch.objects.filter(pk=branch_id).first()
            if branch and (log.destination or "") != f"Branch: {branch.name}":
                continue
        ref_map[(log.reference, log.product_id)].append(log)
    for (ref, pid), ref_logs in ref_map.items():
        if len(ref_logs) <= 1:
            continue
        keep_ids.append(ref_logs[0].id)
        drop = []
        for extra in ref_logs[1:]:
            if extra.id not in delete_ids:
                delete_ids.append(extra.id)
                drop.append(extra)
        if not drop:
            continue
        keep_log = ref_logs[0]
        groups.append(
            {
                "product_id": pid,
                "product_name": (
                    keep_log.product.name if keep_log.product_id else f"#{pid}"
                ),
                "quantity": keep_log.quantity,
                "source": keep_log.source or "",
                "destination": keep_log.destination or "",
                "expected": 1,
                "actual": len(ref_logs),
                "delete_count": len(drop),
                "keep_count": 1,
                "kind": "duplicate",
            }
        )

    # De-dupe keep/delete
    delete_set = set(delete_ids)
    keep_ids = [i for i in dict.fromkeys(keep_ids) if i not in delete_set]
    delete_ids = list(dict.fromkeys(delete_ids))

    return {
        "groups": groups,
        "keep_ids": keep_ids,
        "delete_ids": delete_ids,
        "set_references": set_references,
        "delete_count": len(delete_ids),
        "group_count": len(groups),
    }


@transaction.atomic
def apply_duplicate_grn_stock_log_cleanup(*, product_id=None, branch_id=None, actor=None):
    if actor is not None and (
        not getattr(actor, "is_superuser", False) or not actor.is_active
    ):
        from django.core.exceptions import ValidationError

        raise ValidationError("Only an active superuser can clean up duplicate GRN stock logs.")

    plan = find_duplicate_grn_stock_logs(product_id=product_id, branch_id=branch_id)
    deleted = 0
    if plan["delete_ids"]:
        deleted, _ = StockLog.objects.filter(
            id__in=plan["delete_ids"], action_type=StockLog.ActionType.GRN
        ).delete()

    updated_refs = 0
    for log_id, ref in plan["set_references"].items():
        if log_id in plan["delete_ids"]:
            continue
        n = StockLog.objects.filter(pk=log_id, action_type=StockLog.ActionType.GRN).update(
            reference=ref
        )
        updated_refs += n

    return {
        "deleted": deleted,
        "updated_refs": updated_refs,
        "group_count": plan["group_count"],
        "groups": plan["groups"],
    }
