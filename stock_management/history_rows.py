"""Unified Stock History rows: StockLog + draft GRNs + rejected/pending FG issues."""

from datetime import datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.db.models import Q
from django.utils import timezone

from branches.models import Branch
from stock_management.models import Product, StockLog
from suppliers.models import FarmerGoodsIssue, GRN, GRNItem
from suppliers.services import _farmer_goods_issue_destination_label


STATUS_META = {
    "posted": {"label": "Posted", "css": "posted"},
    "draft": {"label": "Draft", "css": "draft"},
    "pending": {"label": "Pending", "css": "pending"},
    "accepted": {"label": "Accepted", "css": "accepted"},
    "rejected": {"label": "Rejected", "css": "rejected"},
    "not_required": {"label": "Posted", "css": "posted"},
}


def _aware(dt):
    if dt is None:
        return timezone.now()
    if timezone.is_aware(dt):
        return dt
    return timezone.make_aware(dt)


def _date_at_branch_midnight(day):
    return timezone.make_aware(datetime.combine(day, time.min))


def make_history_row(
    *,
    date,
    action_type,
    product,
    quantity,
    source,
    destination,
    status_key,
    log_id=None,
    doc_type="",
    doc_id=None,
    affects_balance=True,
    sort_key=None,
):
    meta = STATUS_META.get(status_key, {"label": status_key.title(), "css": "posted"})
    action_label = {
        StockLog.ActionType.GRN: "GRN",
        StockLog.ActionType.TRANSFER: "Transfer",
        StockLog.ActionType.ISSUE: "Issue",
    }.get(action_type, action_type)

    def _action_display(label=action_label):
        return label

    return SimpleNamespace(
        id=log_id,
        date=_aware(date),
        action_type=action_type,
        product=product,
        quantity=quantity or Decimal("0"),
        source=source or "—",
        destination=destination or "—",
        balance_qty=Decimal("0"),
        status_key=status_key,
        status_label=meta["label"],
        status_css=meta["css"],
        doc_type=doc_type,
        doc_id=doc_id,
        affects_balance=affects_balance,
        sort_key=sort_key or (_aware(date), log_id or 0, doc_id or 0),
        get_action_type_display=_action_display,
    )


def _fg_status_key(issue):
    if issue.driver_status == FarmerGoodsIssue.DriverStatus.REJECTED:
        return "rejected"
    if issue.driver_status == FarmerGoodsIssue.DriverStatus.PENDING:
        return "pending"
    if issue.driver_status == FarmerGoodsIssue.DriverStatus.ACCEPTED:
        return "accepted"
    return "not_required"


def stocklog_to_history_row(log, status_key="posted", date=None):
    when = date or log.date
    return make_history_row(
        date=when,
        action_type=log.action_type,
        product=log.product,
        quantity=log.quantity,
        source=log.source,
        destination=log.destination,
        status_key=status_key,
        log_id=log.pk,
        doc_type="log",
        doc_id=log.pk,
        affects_balance=True,
        sort_key=(when, 0, log.pk),
    )


def fg_issue_id_from_log_reference(ref):
    ref = (ref or "").strip()
    if not ref.startswith("fg-issue:"):
        return None
    try:
        return int(ref.split(":", 1)[1])
    except (TypeError, ValueError):
        return None


def live_fg_issue_ids_for_logs(logs):
    ids = []
    for log in logs:
        issue_id = fg_issue_id_from_log_reference(getattr(log, "reference", None))
        if issue_id is not None:
            ids.append(issue_id)
    if not ids:
        return set()
    return set(FarmerGoodsIssue.objects.filter(pk__in=ids).values_list("pk", flat=True))


def stocklog_is_live(log, live_issue_ids):
    """Drop StockLog rows whose fg-issue:{id} receipt was deleted."""
    issue_id = fg_issue_id_from_log_reference(getattr(log, "reference", None))
    if issue_id is None:
        return True
    return issue_id in live_issue_ids


def stocklogs_to_history_rows(logs):
    """Convert posted StockLogs; skip leftover logs for deleted issue receipts."""
    rows = []
    log_list = list(logs)
    live_ids = live_fg_issue_ids_for_logs(log_list)
    issue_ids = []
    for log in log_list:
        issue_id = fg_issue_id_from_log_reference(getattr(log, "reference", None))
        if issue_id is not None:
            issue_ids.append(issue_id)
    issues = {
        issue.pk: issue
        for issue in FarmerGoodsIssue.objects.filter(pk__in=issue_ids).only(
            "id", "date", "driver_status"
        )
    }
    for log in log_list:
        if not stocklog_is_live(log, live_ids):
            continue
        issue = issues.get(fg_issue_id_from_log_reference(getattr(log, "reference", None)))
        status_key = _fg_status_key(issue) if issue else enrich_log_status(log)
        when = issue.date if issue and issue.date else log.date
        rows.append(stocklog_to_history_row(log, status_key=status_key, date=when))
    return rows


def enrich_log_status(log):
    """Prefer linked FG issue status when reference is fg-issue:{id}."""
    issue_id = fg_issue_id_from_log_reference(log.reference)
    if issue_id is None:
        return "posted"
    issue = FarmerGoodsIssue.objects.filter(pk=issue_id).first()
    if issue:
        return _fg_status_key(issue)
    return "posted"


def draft_grn_history_rows(*, product_id=None, branch_id=None, from_day=None, to_day=None, branch_ids=None):
    qs = (
        GRNItem.objects.filter(grn__status=GRN.Status.DRAFT)
        .select_related("product", "grn__supplier", "grn__branch")
        .order_by("grn__date", "id")
    )
    if product_id:
        qs = qs.filter(product_id=product_id)
    if branch_id:
        qs = qs.filter(grn__branch_id=branch_id)
    elif branch_ids is not None:
        qs = qs.filter(Q(grn__branch_id__in=branch_ids) | Q(grn__branch_id__isnull=True))
    if from_day:
        qs = qs.filter(grn__date__gte=from_day)
    if to_day:
        qs = qs.filter(grn__date__lte=to_day)

    rows = []
    for item in qs.iterator(chunk_size=200):
        grn = item.grn
        when = _date_at_branch_midnight(grn.date) if grn.date else grn.created_at
        rows.append(
            make_history_row(
                date=when,
                action_type=StockLog.ActionType.GRN,
                product=item.product,
                quantity=item.quantity,
                source=f"Supplier: {grn.supplier.name}" if grn.supplier_id else "Supplier",
                destination=f"Branch: {grn.branch.name}" if grn.branch_id else "Main",
                status_key="draft",
                doc_type="grn",
                doc_id=grn.pk,
                affects_balance=False,
                sort_key=(_aware(when), 1, item.pk),
            )
        )
    return rows


def _filtered_fg_issue_qs(
    *, product_id=None, branch_id=None, from_day=None, to_day=None, before_day=None, branch_ids=None
):
    qs = FarmerGoodsIssue.objects.select_related("product", "from_branch").order_by("date", "id")
    if product_id:
        qs = qs.filter(product_id=product_id)
    if branch_id:
        qs = qs.filter(from_branch_id=branch_id)
    elif branch_ids is not None:
        qs = qs.filter(from_branch_id__in=branch_ids)
    if from_day:
        start = timezone.make_aware(datetime.combine(from_day, time.min))
        qs = qs.filter(date__gte=start)
    if to_day:
        end = timezone.make_aware(datetime.combine(to_day + timedelta(days=1), time.min))
        qs = qs.filter(date__lt=end)
    if before_day:
        cutoff = timezone.make_aware(datetime.combine(before_day, time.min))
        qs = qs.filter(date__lt=cutoff)
    return qs


def _logged_fg_issue_ids(issue_ids):
    if not issue_ids:
        return set()
    refs = [f"fg-issue:{issue_id}" for issue_id in issue_ids]
    found = set()
    for ref in StockLog.objects.filter(reference__in=refs).values_list("reference", flat=True):
        issue_id = fg_issue_id_from_log_reference(ref)
        if issue_id is not None:
            found.add(issue_id)
    return found


def _fg_issue_history_row(issue, *, affects_balance, sort_tier):
    action = (
        StockLog.ActionType.TRANSFER
        if issue.issue_to_type == FarmerGoodsIssue.IssueToType.BRANCH
        else StockLog.ActionType.ISSUE
    )
    source = (
        f"Branch: {issue.from_branch.name}"
        if issue.from_branch_id
        else "Farmer goods"
    )
    return make_history_row(
        date=issue.date,
        action_type=action,
        product=issue.product,
        quantity=issue.quantity,
        source=source,
        destination=_farmer_goods_issue_destination_label(issue),
        status_key=_fg_status_key(issue),
        doc_type="fg_issue",
        doc_id=issue.pk,
        affects_balance=affects_balance,
        sort_key=(_aware(issue.date), sort_tier, issue.pk),
    )


def non_posted_fg_issue_rows(
    *, product_id=None, branch_id=None, from_day=None, to_day=None, before_day=None, branch_ids=None
):
    """Rejected + pending farmer-goods issues (not in StockLog / not stock-posted)."""
    qs = _filtered_fg_issue_qs(
        product_id=product_id,
        branch_id=branch_id,
        from_day=from_day,
        to_day=to_day,
        before_day=before_day,
        branch_ids=branch_ids,
    ).filter(
        driver_status__in=[
            FarmerGoodsIssue.DriverStatus.REJECTED,
            FarmerGoodsIssue.DriverStatus.PENDING,
        ]
    )
    return [
        _fg_issue_history_row(issue, affects_balance=False, sort_tier=2)
        for issue in qs.iterator(chunk_size=200)
    ]


def applied_fg_issue_missing_log_rows(
    *, product_id=None, branch_id=None, from_day=None, to_day=None, before_day=None, branch_ids=None
):
    """
    Accepted / not-required issue receipts that never got a StockLog.

    Stock Overview Available counts these; Stock History previously omitted them,
    so deleting a receipt updated Available but left history/balance stale.
    """
    qs = _filtered_fg_issue_qs(
        product_id=product_id,
        branch_id=branch_id,
        from_day=from_day,
        to_day=to_day,
        before_day=before_day,
        branch_ids=branch_ids,
    ).exclude(
        driver_status__in=[
            FarmerGoodsIssue.DriverStatus.PENDING,
            FarmerGoodsIssue.DriverStatus.REJECTED,
        ]
    )
    issue_ids = list(qs.values_list("pk", flat=True))
    logged_ids = _logged_fg_issue_ids(issue_ids)
    rows = []
    for issue in qs.iterator(chunk_size=200):
        if issue.pk in logged_ids:
            continue
        rows.append(_fg_issue_history_row(issue, affects_balance=True, sort_tier=0))
    return rows


def extra_fg_issue_history_rows(
    *, product_id=None, branch_id=None, from_day=None, to_day=None, before_day=None, branch_ids=None
):
    kwargs = {
        "product_id": product_id,
        "branch_id": branch_id,
        "from_day": from_day,
        "to_day": to_day,
        "before_day": before_day,
        "branch_ids": branch_ids,
    }
    return non_posted_fg_issue_rows(**kwargs) + applied_fg_issue_missing_log_rows(**kwargs)


def attach_running_balances(rows, signed_delta_fn, opening=None):
    """
    Compute balance for display. Only rows with affects_balance=True change the total;
    draft/rejected/pending keep the current running balance at their position.
    """
    ordered = sorted(rows, key=lambda r: r.sort_key)
    running = {pid: Decimal(str(bal)) for pid, bal in (opening or {}).items()}
    for row in ordered:
        pid = row.product.id if hasattr(row.product, "id") else row.product
        if row.affects_balance:
            # Rebuild a tiny log-like object for existing delta helper.
            fake = SimpleNamespace(
                quantity=row.quantity,
                action_type=row.action_type,
                source=row.source,
                destination=row.destination,
                product_id=pid,
            )
            running[pid] = running.get(pid, Decimal("0.00")) + signed_delta_fn(fake)
        row.balance_qty = running.get(pid, Decimal("0.00"))
    return ordered
