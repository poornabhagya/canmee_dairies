"""Resolve StockLog rows to related GRN / issue / transfer documents for modals."""

from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from canmee_dairies.formatting import format_money
from stock_management.models import StockIssue, StockLog, StockTransfer, StockTransferNote
from suppliers.models import FarmerGoodsIssue, GRN, GRNItem


def _parse_ref(reference):
    ref = (reference or "").strip()
    if not ref or ":" not in ref:
        return None, None
    kind, raw = ref.split(":", 1)
    try:
        return kind, int(raw)
    except (TypeError, ValueError):
        return kind, raw


def _grn_payload(grn):
    items = list(grn.items.select_related("product").all())
    lines_subtotal = sum((i.total_price or Decimal("0") for i in items), Decimal("0"))
    doc_disc = Decimal("0")
    if grn.discount_scope == GRN.DiscountScope.DOCUMENT:
        if grn.document_discount_percent > 0:
            doc_disc = lines_subtotal * (grn.document_discount_percent / Decimal("100"))
        elif grn.document_discount_amount > 0:
            doc_disc = min(lines_subtotal, grn.document_discount_amount)
    payable = (lines_subtotal - doc_disc).quantize(Decimal("0.01"))
    return {
        "doc_type": "grn",
        "title": f"GRN · {grn.grn_number}",
        "subtitle": f"{grn.get_status_display()} · {grn.date.isoformat() if grn.date else ''}".strip(" ·"),
        "print_url": reverse("grn-print", args=[grn.id]),
        "open_url": reverse("grn-list") + f"?q={grn.grn_number}",
        "embed_url": "",
        "meta": [
            {"label": "Supplier", "value": grn.supplier.name if grn.supplier_id else "—"},
            {"label": "Branch", "value": grn.branch.name if grn.branch_id else "—"},
            {"label": "Type", "value": grn.get_grn_type_display()},
            {"label": "Discount mode", "value": grn.get_discount_scope_display()},
        ],
        "lines": [
            {
                "product": item.product.name if item.product_id else "—",
                "unit": item.product.unit if item.product_id else "",
                "quantity": str(item.quantity or Decimal("0")),
                "extra": format_money(item.unit_price or Decimal("0")),
                "total": format_money(item.total_price or Decimal("0")),
            }
            for item in items
        ],
        "totals": [
            {"label": "Lines subtotal", "value": format_money(lines_subtotal)},
            {"label": "Document discount", "value": format_money(doc_disc)},
            {"label": "Payable total", "value": format_money(payable)},
        ],
        "columns": ["Product", "Qty", "Rate", "Line total"],
    }


def _fg_issue_payload(issue):
    batch_ref = issue.batch_ref or ""
    siblings = list(
        FarmerGoodsIssue.objects.filter(batch_ref=batch_ref).select_related("product", "from_branch")
        if batch_ref
        else [issue]
    )
    if not siblings:
        siblings = [issue]
    head = siblings[0]
    print_url = (
        reverse("farmer-goods-issue-print", args=[batch_ref]) if batch_ref else ""
    )
    embed_url = f"{print_url}?embed=1" if print_url else ""
    dest_type = head.get_issue_to_type_display()
    return {
        "doc_type": "fg_issue",
        "title": f"Issue receipt · {batch_ref[:8] + '…' if len(batch_ref) > 8 else (batch_ref or f'#{head.pk}')}",
        "subtitle": f"{dest_type} · {timezone.localtime(head.date).strftime('%Y-%m-%d %H:%M') if head.date else ''}",
        "print_url": print_url,
        "open_url": reverse("farmer-goods-issue-list"),
        "embed_url": embed_url,
        "meta": [
            {
                "label": "From branch",
                "value": head.from_branch.name if head.from_branch_id else "—",
            },
            {"label": "Issue to", "value": dest_type},
            {"label": "Status", "value": head.get_driver_status_display()},
            {"label": "Lines", "value": str(len(siblings))},
        ],
        "lines": [
            {
                "product": row.product.name if row.product_id else "—",
                "unit": row.product.unit if row.product_id else "",
                "quantity": str(row.quantity or Decimal("0")),
                "extra": "",
                "total": "",
            }
            for row in siblings
        ],
        "totals": [],
        "columns": ["Product", "Qty", "", ""],
    }


def _transfer_note_payload(note):
    from stock_management.services import transfer_note_priced_lines

    lines = transfer_note_priced_lines(note)
    total = sum((row["amount"] for row in lines), Decimal("0")).quantize(Decimal("0.01"))
    return {
        "doc_type": "transfer_note",
        "title": f"Transfer · {note.transfer_number}",
        "subtitle": (
            f"{note.from_branch.name if note.from_branch_id else '—'} → "
            f"{note.to_branch.name if note.to_branch_id else '—'}"
        ),
        "print_url": reverse("stock-transfer-note-print", args=[note.id]),
        "open_url": reverse("stock-transfer-note-list"),
        "embed_url": "",
        "meta": [
            {"label": "From", "value": note.from_branch.name if note.from_branch_id else "—"},
            {"label": "To", "value": note.to_branch.name if note.to_branch_id else "—"},
            {
                "label": "Date",
                "value": note.date.isoformat() if getattr(note, "date", None) else "",
            },
            {"label": "Remarks", "value": note.remarks or "—"},
        ],
        "lines": [
            {
                "product": row["product"].name,
                "unit": row["product"].unit or "",
                "quantity": str(row["quantity"]),
                "extra": format_money(row["unit_price"]),
                "total": format_money(row["amount"]),
            }
            for row in lines
        ],
        "totals": [{"label": "Total", "value": format_money(total)}],
        "columns": ["Product", "Qty", "Unit price", "Amount"],
    }


def _legacy_transfer_payload(transfer):
    return {
        "doc_type": "transfer_legacy",
        "title": f"Transfer · #{transfer.pk}",
        "subtitle": f"{transfer.from_location} → Branch: {transfer.to_branch.name if transfer.to_branch_id else '—'}",
        "print_url": "",
        "open_url": reverse("stock-transfer-note-list"),
        "embed_url": "",
        "meta": [
            {"label": "From", "value": transfer.from_location or "Main"},
            {
                "label": "To branch",
                "value": transfer.to_branch.name if transfer.to_branch_id else "—",
            },
            {
                "label": "Product",
                "value": transfer.product.name if transfer.product_id else "—",
            },
            {"label": "Quantity", "value": str(transfer.quantity or Decimal("0"))},
        ],
        "lines": [
            {
                "product": transfer.product.name if transfer.product_id else "—",
                "unit": transfer.product.unit if transfer.product_id else "",
                "quantity": str(transfer.quantity or Decimal("0")),
                "extra": "",
                "total": "",
            }
        ],
        "totals": [],
        "columns": ["Product", "Qty", "", ""],
    }


def _stock_issue_payload(issue):
    open_url = reverse("stock-history")
    if issue.issued_to_type == StockIssue.IssuedToType.STOCKTAKE and issue.issued_to_id:
        open_url = reverse("stock-count-edit", args=[issue.issued_to_id])
    meta = [
        {"label": "Branch", "value": issue.branch.name if issue.branch_id else "—"},
        {"label": "Issued to", "value": issue.get_issued_to_type_display()},
    ]
    if issue.issued_to_type == StockIssue.IssuedToType.STOCKTAKE:
        meta.append({"label": "Stock count ID", "value": str(issue.issued_to_id)})
    else:
        meta.append({"label": "Target ID", "value": str(issue.issued_to_id)})
    meta.extend(
        [
            {
                "label": "Product",
                "value": issue.product.name if issue.product_id else "—",
            },
            {"label": "Quantity", "value": str(issue.quantity or Decimal("0"))},
        ]
    )
    return {
        "doc_type": "stock_issue",
        "title": f"Stock issue · #{issue.pk}",
        "subtitle": issue.get_issued_to_type_display(),
        "print_url": "",
        "open_url": open_url,
        "embed_url": "",
        "meta": meta,
        "lines": [
            {
                "product": issue.product.name if issue.product_id else "—",
                "unit": issue.product.unit if issue.product_id else "",
                "quantity": str(issue.quantity or Decimal("0")),
                "extra": "",
                "total": "",
            }
        ],
        "totals": [],
        "columns": ["Product", "Qty", "", ""],
    }


def _unknown_payload(log, message):
    return {
        "doc_type": "unknown",
        "title": f"{log.get_action_type_display()} · log #{log.pk}",
        "subtitle": timezone.localtime(log.date).strftime("%Y-%m-%d %H:%M") if log.date else "",
        "print_url": "",
        "open_url": "",
        "embed_url": "",
        "meta": [
            {"label": "Product", "value": log.product.name if log.product_id else "—"},
            {"label": "Quantity", "value": str(log.quantity or Decimal("0"))},
            {"label": "Source", "value": log.source or "—"},
            {"label": "Destination", "value": log.destination or "—"},
            {"label": "Reference", "value": log.reference or "—"},
        ],
        "lines": [],
        "totals": [],
        "columns": [],
        "message": message,
    }


def _heuristic_grn(log):
    qty = log.quantity or Decimal("0")
    day = timezone.localtime(log.date).date() if log.date else None
    base = GRNItem.objects.filter(
        product_id=log.product_id,
        quantity=qty,
        grn__status=GRN.Status.CONFIRMED,
    ).select_related("grn__supplier", "grn__branch", "product")
    source = log.source or ""
    if source.startswith("Supplier: "):
        supplier_name = source.replace("Supplier: ", "", 1).strip()
        base = base.filter(grn__supplier__name=supplier_name)
    dest = log.destination or ""
    if dest.startswith("Branch: "):
        branch_name = dest.replace("Branch: ", "", 1).strip()
        base = base.filter(grn__branch__name=branch_name)

    # Prefer GRN document/created date near the log time; else best supplier/branch/qty match.
    if day and log.date:
        near = base.filter(
            grn__date__gte=day - timedelta(days=1),
            grn__date__lte=day + timedelta(days=1),
        ).order_by("grn__date", "id")
        item = near.first()
        if item:
            return item.grn
        near_created = base.filter(
            grn__created_at__gte=log.date - timedelta(days=1),
            grn__created_at__lte=log.date + timedelta(days=1),
        ).order_by("grn__created_at", "id")
        item = near_created.first()
        if item:
            return item.grn

    item = base.order_by("-grn__date", "-id").first()
    return item.grn if item else None


def _heuristic_fg_issue(log):
    qty = log.quantity or Decimal("0")
    qs = FarmerGoodsIssue.objects.filter(product_id=log.product_id, quantity=qty).exclude(
        driver_status=FarmerGoodsIssue.DriverStatus.REJECTED
    )
    source = log.source or ""
    if source.startswith("Branch: "):
        qs = qs.filter(from_branch__name=source.replace("Branch: ", "", 1).strip())
    if log.date:
        local = timezone.localtime(log.date)
        qs = qs.filter(
            date__gte=local - timedelta(minutes=5),
            date__lte=local + timedelta(minutes=5),
        )
    return qs.order_by("id").first()


def _heuristic_transfer_note(log):
    qty = log.quantity or Decimal("0")
    source = log.source or ""
    dest = log.destination or ""
    if not (source.startswith("Branch: ") and dest.startswith("Branch: ")):
        return None
    from_name = source.replace("Branch: ", "", 1).strip()
    to_name = dest.replace("Branch: ", "", 1).strip()
    qs = StockTransferNote.objects.filter(
        from_branch__name=from_name,
        to_branch__name=to_name,
        lines__product_id=log.product_id,
        lines__quantity=qty,
    ).distinct()
    if log.date:
        day = timezone.localtime(log.date).date()
        qs = qs.filter(date__gte=day - timedelta(days=1), date__lte=day + timedelta(days=1))
    return qs.order_by("-id").first()


def _heuristic_stock_issue(log):
    qty = log.quantity or Decimal("0")
    source = log.source or ""
    qs = StockIssue.objects.filter(product_id=log.product_id, quantity=qty)
    if source.startswith("Branch: "):
        qs = qs.filter(branch__name=source.replace("Branch: ", "", 1).strip())
    return qs.order_by("-id").first()


def resolve_grn_document(grn):
    return _grn_payload(grn)


def resolve_fg_issue_document(issue):
    return _fg_issue_payload(issue)


def resolve_stock_log_document(log: StockLog):
    """Return modal payload for a StockLog row."""
    kind, value = _parse_ref(log.reference)

    if kind == "grn" and isinstance(value, int):
        grn = GRN.objects.select_related("supplier", "branch").filter(pk=value).first()
        if grn:
            return _grn_payload(grn)
    if kind == "fg-issue" and isinstance(value, int):
        issue = (
            FarmerGoodsIssue.objects.select_related("product", "from_branch")
            .filter(pk=value)
            .first()
        )
        if issue:
            return _fg_issue_payload(issue)
    if kind == "st-note" and isinstance(value, int):
        note = (
            StockTransferNote.objects.select_related("from_branch", "to_branch")
            .prefetch_related("lines__product")
            .filter(pk=value)
            .first()
        )
        if note:
            return _transfer_note_payload(note)
    if kind == "st-legacy" and isinstance(value, int):
        transfer = (
            StockTransfer.objects.select_related("product", "to_branch").filter(pk=value).first()
        )
        if transfer:
            return _legacy_transfer_payload(transfer)
    if kind == "stock-issue" and isinstance(value, int):
        issue = (
            StockIssue.objects.select_related("product", "branch").filter(pk=value).first()
        )
        if issue:
            return _stock_issue_payload(issue)

    # Historical logs without reference — best-effort match.
    if log.action_type == StockLog.ActionType.GRN:
        grn = _heuristic_grn(log)
        if grn:
            return _grn_payload(grn)
    if log.action_type == StockLog.ActionType.ISSUE:
        fg = _heuristic_fg_issue(log)
        if fg:
            return _fg_issue_payload(fg)
        stock_issue = _heuristic_stock_issue(log)
        if stock_issue:
            return _stock_issue_payload(stock_issue)
    if log.action_type == StockLog.ActionType.TRANSFER:
        # Farmer-goods branch issues are logged as TRANSFER.
        fg = _heuristic_fg_issue(log)
        if fg and fg.issue_to_type == FarmerGoodsIssue.IssueToType.BRANCH:
            return _fg_issue_payload(fg)
        note = _heuristic_transfer_note(log)
        if note:
            return _transfer_note_payload(note)

    return _unknown_payload(
        log,
        "Could not link this log to a GRN, issue receipt, or transfer note. "
        "Newer movements store a reference for reliable lookup.",
    )
