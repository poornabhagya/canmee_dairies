from datetime import datetime, timedelta
from decimal import Decimal
import uuid
from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from branches.models import Branch
from stock_management.models import BranchStock, MainStock, Product, StockIssue, StockLog, StockTransfer, StockTransferLine

from .models import (
    ConsumptionSettlement,
    FarmerGoodsIssue,
    FarmerGoodsModuleSettings,
    FarmerGoodsStock,
    GRN,
    GRNItem,
    InventoryUsage,
    Supplier,
    SupplierAccount,
    SupplierTransaction,
    compute_line_subtotal,
)


def farmer_goods_accept_notifications_enabled():
    """True: CP issues wait for Accept. False: apply immediately, no accept notifications."""
    from django.db.utils import OperationalError, ProgrammingError

    try:
        return bool(FarmerGoodsModuleSettings.get_solo().enable_accept_notifications)
    except (OperationalError, ProgrammingError):
        return True


def default_farmer_goods_issue_driver_status(issue_to_type):
    if issue_to_type == FarmerGoodsIssue.IssueToType.COLLECTION_POINT:
        if farmer_goods_accept_notifications_enabled():
            return FarmerGoodsIssue.DriverStatus.PENDING
        return FarmerGoodsIssue.DriverStatus.ACCEPTED
    return FarmerGoodsIssue.DriverStatus.NOT_REQUIRED


def auto_accept_all_pending_farmer_goods_issues(*, note=""):
    """Apply remaining pending CP batches (used when accept notifications are turned off)."""
    note = (note or "Applied automatically (accept notifications disabled)")[:255]
    refs = list(
        FarmerGoodsIssue.objects.filter(
            issue_to_type=FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
            driver_status=FarmerGoodsIssue.DriverStatus.PENDING,
        )
        .exclude(batch_ref="")
        .values_list("batch_ref", flat=True)
        .distinct()
    )
    accepted = 0
    errors = []
    for batch_ref in refs:
        try:
            accept_farmer_goods_issue_batch(batch_ref=batch_ref, route=None, note=note)
            accepted += 1
        except ValidationError as exc:
            msg = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            errors.append(msg)
    return {"accepted": accepted, "failed": len(errors), "errors": errors}


def _issued_at_from_date(issue_date):
    if issue_date is None:
        return timezone.now()
    tz = timezone.get_current_timezone()
    return timezone.make_aware(datetime.combine(issue_date, datetime.min.time()), tz)


def _positive(value, field_name="amount"):
    if value is None or Decimal(str(value)) <= 0:
        raise ValidationError({field_name: f"{field_name.replace('_', ' ').title()} must be greater than zero."})
    return Decimal(str(value))


def finalize_grn_totals(grn):
    """
    Recompute each line's stored subtotal and GRN payable total (including document discount).
    """
    grn = GRN.objects.select_for_update().get(pk=grn.pk)
    subtotal = Decimal("0.00")
    for item in grn.items.select_related("grn").all():
        item.total_price = compute_line_subtotal(item)
        item.save(update_fields=["total_price"])
        subtotal += item.total_price
    if grn.discount_scope == GRN.DiscountScope.DOCUMENT:
        pct = grn.document_discount_percent or Decimal("0")
        flat = grn.document_discount_amount or Decimal("0")
        if pct > Decimal("0"):
            total = subtotal * (Decimal("1") - pct / Decimal("100"))
        else:
            total = max(Decimal("0"), subtotal - flat)
    else:
        total = subtotal
    grn.total_amount = total.quantize(Decimal("0.01"))
    grn.save(update_fields=["total_amount"])
    return grn


def _upsert_supplier_balance(supplier, transaction_type, amount):
    account, _ = SupplierAccount.objects.select_for_update().get_or_create(
        supplier=supplier, defaults={"balance": Decimal("0.00")}
    )
    if transaction_type == SupplierTransaction.TransactionType.CREDIT:
        account.balance += amount
    else:
        account.balance -= amount
    account.save(update_fields=["balance", "updated_at"])
    return account


@transaction.atomic
def record_supplier_payment(*, supplier, amount, description="", reference="Payment"):
    pay_amount = _positive(amount)
    if supplier.status != Supplier.Status.ACTIVE:
        raise ValidationError("Cannot record payment for an inactive supplier.")
    tx = SupplierTransaction.objects.create(
        supplier=supplier,
        transaction_type=SupplierTransaction.TransactionType.DEBIT,
        amount=pay_amount,
        reference=reference,
        description=description or "Supplier payment",
    )
    _upsert_supplier_balance(supplier, tx.transaction_type, pay_amount)
    return tx


@transaction.atomic
def confirm_grn(*, grn, actor=None):
    from suppliers.grn_audit import grn_audit_snapshot, log_grn_action
    from suppliers.models import GRNActionLog

    grn = GRN.objects.select_for_update().select_related("supplier").prefetch_related("items__product").get(pk=grn.pk)
    if grn.status == GRN.Status.CONFIRMED:
        return grn
    if grn.supplier.status != Supplier.Status.ACTIVE:
        raise ValidationError("Cannot confirm GRN for an inactive supplier.")
    if grn.supplier.category == Supplier.Category.RAW_MILK_SUPPLIER:
        raise ValidationError("Cannot confirm GRN for a raw milk supplier.")

    items = list(grn.items.all())
    if not items:
        raise ValidationError("Cannot confirm GRN without items.")

    finalize_grn_totals(grn)
    grn.refresh_from_db()
    total = grn.total_amount

    # Stock corrections restore branch inventory only — do not charge the supplier.
    if grn.grn_type != GRN.GRNType.STOCK_CORRECTION:
        tx = SupplierTransaction.objects.create(
            supplier=grn.supplier,
            transaction_type=SupplierTransaction.TransactionType.CREDIT,
            amount=total,
            reference=f"GRN:{grn.grn_number}",
            description=f"GRN confirmed: {grn.grn_number}",
        )
        _upsert_supplier_balance(grn.supplier, tx.transaction_type, total)

    for item in items:
        qty = _positive(item.quantity, "quantity")
        destination = f"Branch: {grn.branch.name}" if grn.branch_id else "Main"
        source = f"Supplier: {grn.supplier.name}"
        if grn.grn_type == GRN.GRNType.STOCK_CORRECTION:
            if not grn.branch_id:
                raise ValidationError("Stock correction GRNs require a receiving branch.")
            branch_stock, _ = BranchStock.objects.select_for_update().get_or_create(
                branch=grn.branch,
                product=item.product,
                defaults={"quantity": Decimal("0.00")},
            )
            branch_stock.quantity += qty
            branch_stock.save(update_fields=["quantity", "updated_at"])
            # Corrections restore stock that was already issued; keep the farmer-goods
            # pool in sync so leftover branch qty can still be issued (Calup Gel case).
            pool, _ = FarmerGoodsStock.objects.select_for_update().get_or_create(
                product=item.product, defaults={"quantity": Decimal("0.00")}
            )
            pool.quantity += qty
            pool.save(update_fields=["quantity", "updated_at"])
        elif grn.grn_type == GRN.GRNType.CORPORATE:
            # Branch-aware GRNs should immediately contribute to branch stock.
            # Legacy GRNs without a branch continue to post to MainStock.
            if grn.branch_id:
                branch_stock, _ = BranchStock.objects.select_for_update().get_or_create(
                    branch=grn.branch,
                    product=item.product,
                    defaults={"quantity": Decimal("0.00")},
                )
                branch_stock.quantity += qty
                branch_stock.save(update_fields=["quantity", "updated_at"])
            else:
                stock, _ = MainStock.objects.select_for_update().get_or_create(
                    product=item.product, defaults={"quantity": Decimal("0.00")}
                )
                stock.quantity += qty
                stock.save(update_fields=["quantity", "updated_at"])
        else:
            stock, _ = FarmerGoodsStock.objects.select_for_update().get_or_create(
                product=item.product, defaults={"quantity": Decimal("0.00")}
            )
            stock.quantity += qty
            stock.save(update_fields=["quantity", "updated_at"])
            # FARMER_GOODS GRNs can now also affect branch inventory when a branch is selected.
            if grn.branch_id:
                branch_stock, _ = BranchStock.objects.select_for_update().get_or_create(
                    branch=grn.branch,
                    product=item.product,
                    defaults={"quantity": Decimal("0.00")},
                )
                branch_stock.quantity += qty
                branch_stock.save(update_fields=["quantity", "updated_at"])

        StockLog.objects.create(
            product=item.product,
            quantity=qty,
            action_type=StockLog.ActionType.GRN,
            source=source,
            destination=destination,
            reference=f"grn:{grn.pk}",
        )

    now = timezone.now()
    grn.status = GRN.Status.CONFIRMED
    update_fields = ["status"]
    if actor is not None:
        grn.confirmed_by = actor
        grn.confirmed_at = now
        update_fields.extend(["confirmed_by", "confirmed_at"])
    grn.save(update_fields=update_fields)
    if actor is not None:
        log_grn_action(
            grn,
            user=actor,
            action=GRNActionLog.Action.CONFIRMED,
            details={"snapshot": grn_audit_snapshot(grn)},
        )
    return grn


def _farmer_goods_issue_log_reference(issue):
    return f"fg-issue:{issue.pk}"


def _farmer_goods_issue_destination_label(issue):
    """Human-readable destination for StockLog (mirrors UI label helper)."""
    from masters.models import CollectionPoint, Farmer

    name = str(issue.issue_to_id)
    if issue.issue_to_type == FarmerGoodsIssue.IssueToType.BRANCH:
        name = Branch.objects.filter(pk=issue.issue_to_id).values_list("name", flat=True).first() or name
        return f"Branch: {name}"
    if issue.issue_to_type == FarmerGoodsIssue.IssueToType.COLLECTION_POINT:
        name = (
            CollectionPoint.objects.filter(pk=issue.issue_to_id).values_list("name", flat=True).first() or name
        )
        return f"Collection point: {name}"
    if issue.issue_to_type == FarmerGoodsIssue.IssueToType.FARMER:
        name = Farmer.objects.filter(pk=issue.issue_to_id).values_list("common_name", flat=True).first() or name
        return f"Farmer: {name}"
    return name


def log_farmer_goods_issue_stock(issue, *, at=None):
    """
    Write a StockLog when farmer-goods stock is applied.
    Branch destinations are TRANSFER; point/farmer destinations are ISSUE.
    Pending/rejected issues must not be logged.
    """
    if issue.driver_status in (
        FarmerGoodsIssue.DriverStatus.PENDING,
        FarmerGoodsIssue.DriverStatus.REJECTED,
    ):
        return None
    reference = _farmer_goods_issue_log_reference(issue)
    if StockLog.objects.filter(reference=reference).exists():
        return None
    if issue.issue_to_type == FarmerGoodsIssue.IssueToType.BRANCH:
        action_type = StockLog.ActionType.TRANSFER
    else:
        action_type = StockLog.ActionType.ISSUE
    if issue.from_branch_id:
        branch_name = getattr(getattr(issue, "from_branch", None), "name", None)
        if not branch_name:
            branch_name = (
                Branch.objects.filter(pk=issue.from_branch_id).values_list("name", flat=True).first()
                or issue.from_branch_id
            )
        source = f"Branch: {branch_name}"
    else:
        source = "Farmer goods"
    destination = _farmer_goods_issue_destination_label(issue)
    log = StockLog.objects.create(
        product_id=issue.product_id,
        quantity=issue.quantity,
        action_type=action_type,
        source=source,
        destination=destination,
        reference=reference,
    )
    when = at or issue.date
    if when is not None:
        StockLog.objects.filter(pk=log.pk).update(date=when)
        log.date = when
    return log


def sync_farmer_goods_issue_stock_log_dates(*, product_id=None):
    """
    Point StockLog.date at the issue receipt date.

    Older accept/backfill writes used 'now', so history looked like 16 Aug even
    when the receipt was July. After a date-range delete, those leftover stamps
    made it look like deleted receipts were still in history.
    """
    qs = StockLog.objects.filter(reference__startswith="fg-issue:")
    if product_id:
        qs = qs.filter(product_id=product_id)
    updated = 0
    for log in qs.iterator(chunk_size=500):
        issue_id = None
        ref = (log.reference or "").strip()
        if ref.startswith("fg-issue:"):
            try:
                issue_id = int(ref.split(":", 1)[1])
            except (TypeError, ValueError):
                issue_id = None
        if issue_id is None:
            continue
        issue_date = (
            FarmerGoodsIssue.objects.filter(pk=issue_id).values_list("date", flat=True).first()
        )
        if not issue_date or log.date == issue_date:
            continue
        StockLog.objects.filter(pk=log.pk).update(date=issue_date)
        updated += 1
    return updated


def unlog_farmer_goods_issue_stock(issue):
    """Remove StockLog rows created for this farmer-goods issue (edit/delete undo)."""
    deleted = StockLog.objects.filter(reference=_farmer_goods_issue_log_reference(issue)).delete()[0]
    if deleted:
        return deleted
    # Legacy logs without fg-issue:{id} — delete only a unique match.
    if issue.issue_to_type == FarmerGoodsIssue.IssueToType.BRANCH:
        action_types = [StockLog.ActionType.TRANSFER, StockLog.ActionType.ISSUE]
    else:
        action_types = [StockLog.ActionType.ISSUE]
    if issue.from_branch_id:
        branch_name = getattr(getattr(issue, "from_branch", None), "name", None)
        if not branch_name:
            branch_name = (
                Branch.objects.filter(pk=issue.from_branch_id).values_list("name", flat=True).first()
                or issue.from_branch_id
            )
        source = f"Branch: {branch_name}"
    else:
        source = "Farmer goods"
    qs = StockLog.objects.filter(
        product_id=issue.product_id,
        quantity=issue.quantity,
        action_type__in=action_types,
        source=source,
        destination=_farmer_goods_issue_destination_label(issue),
        reference="",
    )
    if issue.date:
        qs = qs.filter(
            date__gte=issue.date - timedelta(days=2),
            date__lte=issue.date + timedelta(days=2),
        )
    if qs.count() != 1:
        return 0
    return qs.delete()[0]


def _branch_stock_on_hand(branch_id, product_id):
    row = (
        BranchStock.objects.filter(branch_id=branch_id, product_id=product_id)
        .values_list("quantity", flat=True)
        .first()
    )
    return row if row is not None else Decimal("0")


def assert_farmer_goods_issue_qty_allowed(
    *,
    from_branch_id,
    product_id,
    quantity,
    product_name=None,
    skip_global=False,
):
    """
    Hard gate: issue qty must be > 0 and not exceed branch on-hand, movement
    Available, or (unless skip_global) the farmer-goods pool.
    """
    qty = _positive(quantity, "quantity")
    if not from_branch_id:
        return qty

    movement = branch_farmer_goods_available_qty(from_branch_id, product_id)
    on_hand = _branch_stock_on_hand(from_branch_id, product_id)
    caps = [movement, on_hand]
    global_avail = None
    if not skip_global:
        global_avail = _global_farmer_goods_available_qty(product_id)
        caps.append(global_avail)
    effective = min(caps)
    if effective < Decimal("0"):
        effective = Decimal("0")
    if qty > effective:
        name = product_name or (
            Product.objects.filter(pk=product_id).values_list("name", flat=True).first()
            or f"product #{product_id}"
        )
        detail = f"branch stock {on_hand}, movement available {movement}"
        if global_avail is not None:
            detail += f", farmer-goods pool {global_avail}"
        raise ValidationError(
            f"Cannot issue {qty} of {name}: only {effective} available ({detail}). "
            "Stock cannot go negative."
        )
    return qty


def _adjust_from_branch_stock_for_fg_issue(issue, *, reverse=False):
    """
    Keep BranchStock in sync with farmer-goods issues.

    FG GRNs with a branch also post to BranchStock; issues from that branch must
    reduce (or restore) the same BranchStock so on-hand matches movement Available.
    """
    if not issue.from_branch_id:
        return
    # Destination-branch credit is handled separately for BRANCH issues.
    if (
        issue.issue_to_type == FarmerGoodsIssue.IssueToType.BRANCH
        and issue.issue_to_id == issue.from_branch_id
    ):
        return
    qty = _positive(issue.quantity, "quantity")
    branch_stock, _ = BranchStock.objects.select_for_update().get_or_create(
        branch_id=issue.from_branch_id,
        product_id=issue.product_id,
        defaults={"quantity": Decimal("0.00")},
    )
    if reverse:
        branch_stock.quantity += qty
    else:
        if branch_stock.quantity < qty:
            name = getattr(getattr(issue, "product", None), "name", None) or issue.product_id
            raise ValidationError(
                f"Insufficient branch stock for {name}: need {qty}, "
                f"available {branch_stock.quantity}. Stock cannot go negative."
            )
        branch_stock.quantity -= qty
    branch_stock.save(update_fields=["quantity", "updated_at"])


@transaction.atomic
def issue_goods(
    *,
    product,
    quantity,
    issue_to_type,
    issue_to_id,
    from_branch=None,
    batch_ref="",
    issued_at=None,
    driver_status=None,
    _skip_branch_availability_check=False,
):
    qty = _positive(quantity, "quantity")
    if driver_status is None:
        driver_status = default_farmer_goods_issue_driver_status(issue_to_type)

    from_branch_id = getattr(from_branch, "id", None) or from_branch
    if from_branch_id and not _skip_branch_availability_check:
        assert_farmer_goods_issue_qty_allowed(
            from_branch_id=from_branch_id,
            product_id=product.id if hasattr(product, "id") else product,
            quantity=qty,
            product_name=getattr(product, "name", None),
        )

    stock, _ = FarmerGoodsStock.objects.select_for_update().get_or_create(
        product=product, defaults={"quantity": Decimal("0.00")}
    )
    apply_stock_now = driver_status != FarmerGoodsIssue.DriverStatus.PENDING
    if apply_stock_now:
        if stock.quantity < qty:
            raise ValidationError("Cannot issue more than available farmer goods stock.")
        stock.quantity -= qty
        stock.save(update_fields=["quantity", "updated_at"])
        if issue_to_type == FarmerGoodsIssue.IssueToType.BRANCH:
            branch = Branch.objects.filter(pk=issue_to_id).first()
            if branch:
                branch_stock, _ = BranchStock.objects.select_for_update().get_or_create(
                    branch=branch, product=product, defaults={"quantity": Decimal("0.00")}
                )
                branch_stock.quantity += qty
                branch_stock.save(update_fields=["quantity", "updated_at"])
    else:
        # Soft-reserve against global stock while awaiting driver acceptance.
        reserved = (
            FarmerGoodsIssue.objects.filter(
                product=product,
                driver_status=FarmerGoodsIssue.DriverStatus.PENDING,
            )
            .aggregate(total=Sum("quantity"))
            .get("total")
            or Decimal("0")
        )
        if stock.quantity - reserved < qty:
            raise ValidationError("Cannot issue more than available farmer goods stock.")

    issue = FarmerGoodsIssue.objects.create(
        from_branch=from_branch,
        product=product,
        quantity=qty,
        issue_to_type=issue_to_type,
        issue_to_id=issue_to_id,
        batch_ref=batch_ref or "",
        date=issued_at or timezone.now(),
        driver_status=driver_status,
    )
    if apply_stock_now:
        if from_branch is not None:
            issue.from_branch = from_branch
        _adjust_from_branch_stock_for_fg_issue(issue, reverse=False)
        log_farmer_goods_issue_stock(issue, at=issue.date)
    return issue


@transaction.atomic
def issue_goods_batch(
    *,
    line_items,
    issue_to_type,
    issue_to_id,
    from_branch=None,
    issue_date=None,
    driver_status=None,
):
    if not line_items:
        raise ValidationError("Add at least one product line.")

    from_branch_id = getattr(from_branch, "id", None) or from_branch
    need_by_product = defaultdict(lambda: Decimal("0"))
    names = {}
    for product, quantity in line_items:
        qty = _positive(quantity, "quantity")
        pid = product.id if hasattr(product, "id") else int(product)
        need_by_product[pid] += qty
        names[pid] = getattr(product, "name", None) or names.get(pid)

    if from_branch_id:
        for product_id, need in need_by_product.items():
            assert_farmer_goods_issue_qty_allowed(
                from_branch_id=from_branch_id,
                product_id=product_id,
                quantity=need,
                product_name=names.get(product_id),
            )

    batch_ref = str(uuid.uuid4())
    issued_at = _issued_at_from_date(issue_date)
    created = []
    for product, quantity in line_items:
        created.append(
            issue_goods(
                product=product,
                quantity=quantity,
                issue_to_type=issue_to_type,
                issue_to_id=issue_to_id,
                from_branch=from_branch,
                batch_ref=batch_ref,
                issued_at=issued_at,
                driver_status=driver_status,
                # Batch already validated totals; per-line stock deduct still
                # re-checks FarmerGoodsStock / BranchStock under lock.
                _skip_branch_availability_check=True,
            )
        )
    return created, batch_ref


def _reverse_farmer_goods_issue_line(issue):
    """
    Undo stock movement for one FarmerGoodsIssue row (must run inside transaction with locks).
    Pending CP issues have not deducted stock yet; rejected rows were never applied (or already restored).
    """
    if issue.driver_status in (
        FarmerGoodsIssue.DriverStatus.PENDING,
        FarmerGoodsIssue.DriverStatus.REJECTED,
    ):
        unlog_farmer_goods_issue_stock(issue)
        return
    qty = _positive(issue.quantity, "quantity")
    stock = FarmerGoodsStock.objects.select_for_update().get(product_id=issue.product_id)
    stock.quantity += qty
    stock.save(update_fields=["quantity", "updated_at"])
    if issue.issue_to_type == FarmerGoodsIssue.IssueToType.BRANCH:
        bs = (
            BranchStock.objects.select_for_update()
            .filter(branch_id=issue.issue_to_id, product_id=issue.product_id)
            .first()
        )
        if bs is None:
            raise ValidationError(
                "Cannot update this receipt: destination branch stock row is missing. Contact support."
            )
        if bs.quantity < qty:
            raise ValidationError(
                "Cannot update this receipt: some quantity was already used at the destination branch."
            )
        bs.quantity -= qty
        bs.save(update_fields=["quantity", "updated_at"])
    _adjust_from_branch_stock_for_fg_issue(issue, reverse=True)
    unlog_farmer_goods_issue_stock(issue)


def _apply_stock_for_accepted_issue(issue):
    """Deduct farmer-goods stock when a pending CP issue is accepted."""
    qty = _positive(issue.quantity, "quantity")
    stock = FarmerGoodsStock.objects.select_for_update().get(product_id=issue.product_id)
    if stock.quantity < qty:
        raise ValidationError(
            f"Insufficient stock to accept {issue.product.name}: need {qty}, "
            f"available {stock.quantity}. Stock cannot go negative."
        )
    stock.quantity -= qty
    stock.save(update_fields=["quantity", "updated_at"])
    _adjust_from_branch_stock_for_fg_issue(issue, reverse=False)


def farmer_goods_payment_eligible_q():
    """Issues that may appear on payment sheets / deductions."""
    return (
        Q(issue_to_type=FarmerGoodsIssue.IssueToType.COLLECTION_POINT)
        & Q(driver_status=FarmerGoodsIssue.DriverStatus.ACCEPTED)
    ) | (
        ~Q(issue_to_type=FarmerGoodsIssue.IssueToType.COLLECTION_POINT)
        & ~Q(driver_status=FarmerGoodsIssue.DriverStatus.REJECTED)
    )


def farmer_goods_stock_affecting_q():
    """Issues that still hold stock (pending + accepted + not_required)."""
    return ~Q(driver_status=FarmerGoodsIssue.DriverStatus.REJECTED)


@transaction.atomic
def accept_farmer_goods_issue_batch(*, batch_ref, route=None, note=""):
    """Route driver accepts a collection-point issue batch and applies stock."""
    issues = list(
        FarmerGoodsIssue.objects.select_for_update()
        .select_related("product")
        .filter(batch_ref=batch_ref)
        .order_by("id")
    )
    if not issues:
        raise ValidationError("Issue receipt not found.")
    head = issues[0]
    if head.issue_to_type != FarmerGoodsIssue.IssueToType.COLLECTION_POINT:
        raise ValidationError("Only collection-point issues require driver acceptance.")
    if head.driver_status == FarmerGoodsIssue.DriverStatus.ACCEPTED:
        return issues
    if head.driver_status == FarmerGoodsIssue.DriverStatus.REJECTED:
        raise ValidationError("This issue was rejected and cannot be accepted.")
    if head.driver_status != FarmerGoodsIssue.DriverStatus.PENDING:
        raise ValidationError("This issue is not awaiting driver acceptance.")
    if route is not None:
        _assert_batch_belongs_to_route(head, route)
    for row in issues:
        _apply_stock_for_accepted_issue(row)
    now = timezone.now()
    note = (note or "").strip()[:255]
    FarmerGoodsIssue.objects.filter(batch_ref=batch_ref).update(
        driver_status=FarmerGoodsIssue.DriverStatus.ACCEPTED,
        driver_responded_at=now,
        driver_response_note=note,
    )
    for issue in issues:
        issue.driver_status = FarmerGoodsIssue.DriverStatus.ACCEPTED
        issue.driver_responded_at = now
        issue.driver_response_note = note
        log_farmer_goods_issue_stock(issue, at=issue.date or now)
    return issues


def bulk_accept_farmer_goods_issue_batches(*, batch_refs, user, note=""):
    """Accept multiple pending CP batches for Admin / superuser / Branch Manager.

    Returns accepted, skipped (already accepted), and errors per batch_ref.
    """
    refs = []
    seen = set()
    for raw in batch_refs or []:
        ref = str(raw or "").strip()
        if not ref or ref in seen:
            continue
        seen.add(ref)
        refs.append(ref)
    if not refs:
        raise ValidationError("Select at least one pending issue.")

    accepted = []
    skipped = []
    errors = []
    note = (note or "").strip()[:255]
    for batch_ref in refs:
        try:
            assert_farmer_goods_batch_accessible_to_user(batch_ref, user)
            head = (
                FarmerGoodsIssue.objects.filter(batch_ref=batch_ref)
                .only("driver_status")
                .first()
            )
            if head and head.driver_status == FarmerGoodsIssue.DriverStatus.ACCEPTED:
                skipped.append(batch_ref)
                continue
            accept_farmer_goods_issue_batch(
                batch_ref=batch_ref, route=None, note=note
            )
            accepted.append(batch_ref)
        except ValidationError as exc:
            message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            errors.append({"batch_ref": batch_ref, "error": message})
    return {
        "accepted": accepted,
        "skipped": skipped,
        "errors": errors,
        "accepted_count": len(accepted),
        "skipped_count": len(skipped),
        "error_count": len(errors),
    }


@transaction.atomic
def reject_farmer_goods_issue_batch(*, batch_ref, route=None, note=""):
    """Route driver rejects a collection-point issue batch (no stock was applied yet)."""
    issues = list(
        FarmerGoodsIssue.objects.select_for_update()
        .select_related("product")
        .filter(batch_ref=batch_ref)
        .order_by("id")
    )
    if not issues:
        raise ValidationError("Issue receipt not found.")
    head = issues[0]
    if head.issue_to_type != FarmerGoodsIssue.IssueToType.COLLECTION_POINT:
        raise ValidationError("Only collection-point issues can be rejected by the route driver.")
    if head.driver_status == FarmerGoodsIssue.DriverStatus.REJECTED:
        return issues
    if head.driver_status == FarmerGoodsIssue.DriverStatus.ACCEPTED:
        raise ValidationError("This issue was already accepted.")
    if head.driver_status != FarmerGoodsIssue.DriverStatus.PENDING:
        raise ValidationError("This issue is not awaiting driver response.")
    if route is not None:
        _assert_batch_belongs_to_route(head, route)
    if any(issue.is_settled for issue in issues):
        raise ValidationError("Cannot reject an issue that is already settled.")
    now = timezone.now()
    note = (note or "").strip()[:255]
    FarmerGoodsIssue.objects.filter(batch_ref=batch_ref).update(
        driver_status=FarmerGoodsIssue.DriverStatus.REJECTED,
        driver_responded_at=now,
        driver_response_note=note,
    )
    for issue in issues:
        issue.driver_status = FarmerGoodsIssue.DriverStatus.REJECTED
        issue.driver_responded_at = now
        issue.driver_response_note = note
    return issues


def _assert_batch_belongs_to_route(head, route):
    from masters.models import CollectionPoint

    point_ids = set(route.collection_points.values_list("pk", flat=True))
    number_ids = set()
    for number in route.collection_points.values_list("number", flat=True):
        try:
            number_ids.add(int(str(number).strip()))
        except (TypeError, ValueError):
            continue
    if head.issue_to_id not in point_ids and head.issue_to_id not in number_ids:
        # Resolve via CollectionPoint pk lookup for safety
        point = CollectionPoint.objects.filter(pk=head.issue_to_id).select_related("route").first()
        if not point or point.route_id != route.pk:
            raise ValidationError("This issue does not belong to your route.")


def pending_collection_point_batches_for_route(route):
    """Pending CP issue batches for points on this route (for driver notifications)."""
    points = list(route.collection_points.order_by("number", "name"))
    return pending_collection_point_batches_for_points(points)


def _pending_cp_issue_id_candidates(points):
    point_ids = {p.pk for p in points}
    number_ids = set()
    for point in points:
        try:
            number_ids.add(int(str(point.number).strip()))
        except (TypeError, ValueError):
            continue
    return point_ids | number_ids


def _pending_cp_issues_base_qs(points):
    id_candidates = _pending_cp_issue_id_candidates(points)
    if not id_candidates:
        return FarmerGoodsIssue.objects.none()
    return FarmerGoodsIssue.objects.filter(
        issue_to_type=FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
        issue_to_id__in=id_candidates,
        driver_status=FarmerGoodsIssue.DriverStatus.PENDING,
        batch_ref__gt="",
    )


def pending_collection_point_batches_for_points(points, *, limit=None):
    """Group pending collection-point issue batches for the given points."""
    from django.db.models import Max

    points = list(points or [])
    if not points:
        return []
    base_qs = _pending_cp_issues_base_qs(points)
    if not base_qs.exists():
        return []

    ref_rows = list(
        base_qs.values("batch_ref")
        .annotate(latest=Max("date"), min_id=Max("id"))
        .order_by("-latest", "-min_id")
    )
    if limit is not None:
        ref_rows = ref_rows[: max(0, int(limit))]
    batch_refs = [row["batch_ref"] for row in ref_rows if row.get("batch_ref")]
    if not batch_refs:
        return []

    issues = list(
        base_qs.filter(batch_ref__in=batch_refs)
        .select_related("product", "from_branch")
        .order_by("-date", "batch_ref", "id")
    )
    point_by_pk = {p.pk: p for p in points}
    point_by_number = {}
    for point in points:
        try:
            point_by_number[int(str(point.number).strip())] = point
        except (TypeError, ValueError):
            continue
    by_ref = {}
    for issue in issues:
        point = point_by_pk.get(issue.issue_to_id) or point_by_number.get(issue.issue_to_id)
        if not point:
            continue
        bucket = by_ref.setdefault(
            issue.batch_ref,
            {
                "batch_ref": issue.batch_ref,
                "date": issue.date,
                "point": point,
                "from_branch": issue.from_branch,
                "lines": [],
            },
        )
        bucket["lines"].append(issue)
    # Preserve newest-first order from ref_rows.
    return [by_ref[ref] for ref in batch_refs if ref in by_ref]


def count_pending_collection_point_batches_for_points(points):
    """Cheap distinct batch_ref count for pending CP issues."""
    points = list(points or [])
    if not points:
        return 0
    return (
        _pending_cp_issues_base_qs(points)
        .values("batch_ref")
        .distinct()
        .count()
    )


def _branch_ids_for_farmer_goods_manager(user):
    """Branch IDs a branch manager may manage (assigned + managed)."""
    from branches.models import Branch

    ids = set(user.assigned_branches.values_list("id", flat=True))
    emp = getattr(user, "employee_profile", None)
    if emp is not None:
        ids.update(
            Branch.objects.filter(branch_manager_id=emp.pk).values_list("id", flat=True)
        )
    return ids


def _collection_points_for_farmer_goods_manager(user):
    from masters.models import CollectionPoint
    from user_management.templatetags.user_tags import (
        user_can_manage_farmer_goods_notifications,
        user_is_admin_or_superuser,
    )

    if not user_can_manage_farmer_goods_notifications(user):
        return []
    points_qs = CollectionPoint.objects.select_related("route", "route__branch").order_by(
        "route__code", "number", "name"
    )
    if not user_is_admin_or_superuser(user):
        branch_ids = _branch_ids_for_farmer_goods_manager(user)
        if not branch_ids:
            return []
        points_qs = points_qs.filter(route__branch_id__in=branch_ids)
    return list(points_qs)


def pending_collection_point_batches_for_user(user, *, limit=None):
    """
    Pending CP issue batches visible to Admin/superuser (all) or Branch Manager
    (points on routes in their managed/assigned branches).
    """
    return pending_collection_point_batches_for_points(
        _collection_points_for_farmer_goods_manager(user),
        limit=limit,
    )


def count_pending_collection_point_batches_for_user(user):
    """Badge/count helper — avoids loading and pricing every pending batch."""
    return count_pending_collection_point_batches_for_points(
        _collection_points_for_farmer_goods_manager(user)
    )


def approx_price_map_for_issues(display_issues):
    """
    Fast topbar/feed pricing using latest GRN issue price (not full FIFO replay).
    Accurate FIFO remains on receipts / payment sheet / pending-accept page.
    """
    issue_list = list(display_issues)
    if not issue_list:
        return {}
    cache = {}
    out = {}
    for issue in issue_list:
        key = (issue.product_id, issue.from_branch_id)
        if key not in cache:
            cache[key] = latest_farmer_goods_unit_price(issue.product_id, issue.from_branch_id)
        qty = issue.quantity or Decimal("0")
        out[issue.id] = (qty * cache[key]).quantize(Decimal("0.01"))
    return out


def assert_farmer_goods_batch_accessible_to_user(batch_ref, user):
    """Raise ValidationError if the user cannot accept/reject this pending batch."""
    from masters.models import CollectionPoint
    from user_management.templatetags.user_tags import (
        user_can_manage_farmer_goods_notifications,
        user_is_admin_or_superuser,
    )

    if not user_can_manage_farmer_goods_notifications(user):
        raise ValidationError("You do not have permission to respond to this issue.")
    if user_is_admin_or_superuser(user):
        return
    head = (
        FarmerGoodsIssue.objects.filter(batch_ref=batch_ref)
        .only("issue_to_type", "issue_to_id", "from_branch_id")
        .first()
    )
    if not head:
        raise ValidationError("Issue receipt not found.")
    branch_ids = _branch_ids_for_farmer_goods_manager(user)
    if head.from_branch_id and head.from_branch_id in branch_ids:
        return
    point = (
        CollectionPoint.objects.filter(pk=head.issue_to_id)
        .select_related("route")
        .first()
    )
    if point and point.route_id and point.route.branch_id in branch_ids:
        return
    # Also resolve by point number stored as issue_to_id
    for point in CollectionPoint.objects.filter(route__branch_id__in=branch_ids).only(
        "id", "number", "route_id"
    ):
        try:
            number_id = int(str(point.number).strip())
        except (TypeError, ValueError):
            continue
        if number_id == head.issue_to_id:
            return
    raise ValidationError("This issue does not belong to your branches.")


@transaction.atomic
def update_farmer_goods_issue_batch(
    *,
    batch_ref,
    line_items,
    issue_to_type,
    issue_to_id,
    from_branch,
    issue_date=None,
):
    """
    Reverse stock for an existing batch, replace lines, and re-apply using the same batch_ref.
    Preserves settlement flags per product; uses issue_date when provided.
    """
    if not line_items:
        raise ValidationError("Add at least one product line.")
    existing = list(
        FarmerGoodsIssue.objects.select_related("product", "from_branch")
        .filter(batch_ref=batch_ref)
        .order_by("id")
    )
    if not existing:
        raise ValidationError("Issue receipt not found.")
    if existing[0].driver_status == FarmerGoodsIssue.DriverStatus.REJECTED:
        raise ValidationError("Cannot edit a rejected issue.")
    prior_driver_status = existing[0].driver_status
    prior_responded_at = existing[0].driver_responded_at
    prior_response_note = existing[0].driver_response_note or ""
    settlement_by_product = {
        row.product_id: {
            "is_settled": row.is_settled,
            "settled_at": row.settled_at,
            "settlement_note": row.settlement_note,
        }
        for row in existing
    }
    issued_at = _issued_at_from_date(issue_date) if issue_date is not None else existing[0].date
    for row in existing:
        _reverse_farmer_goods_issue_line(row)
    FarmerGoodsIssue.objects.filter(batch_ref=batch_ref).delete()
    created = []
    for product, quantity in line_items:
        issue = issue_goods(
            product=product,
            quantity=quantity,
            issue_to_type=issue_to_type,
            issue_to_id=issue_to_id,
            from_branch=from_branch,
            batch_ref=batch_ref,
            issued_at=issued_at,
            driver_status=prior_driver_status
            if issue_to_type == FarmerGoodsIssue.IssueToType.COLLECTION_POINT
            else None,
        )
        if issue_to_type == FarmerGoodsIssue.IssueToType.COLLECTION_POINT:
            issue.driver_responded_at = prior_responded_at
            issue.driver_response_note = prior_response_note
            issue.save(update_fields=["driver_responded_at", "driver_response_note"])
        settlement = settlement_by_product.get(product.pk)
        if settlement and settlement["is_settled"]:
            issue.is_settled = True
            issue.settled_at = settlement["settled_at"]
            issue.settlement_note = settlement["settlement_note"] or ""
            issue.save(update_fields=["is_settled", "settled_at", "settlement_note"])
        created.append(issue)
    return created


@transaction.atomic
def delete_farmer_goods_issue_batch(*, batch_ref):
    """
    Reverse stock movements and delete all rows in a batch.
    """
    existing = list(
        FarmerGoodsIssue.objects.select_related("product", "from_branch")
        .filter(batch_ref=batch_ref)
        .order_by("id")
    )
    if not existing:
        raise ValidationError("Issue receipt not found.")
    for row in existing:
        _reverse_farmer_goods_issue_line(row)
    FarmerGoodsIssue.objects.filter(batch_ref=batch_ref).delete()
    return len(existing)


@transaction.atomic
def delete_farmer_goods_issue_line(*, issue_id):
    """
    Reverse stock for one issue line and delete only that row.
    """
    issue = (
        FarmerGoodsIssue.objects.select_related("product", "from_branch")
        .filter(pk=issue_id)
        .first()
    )
    if not issue:
        raise ValidationError("Issue line not found.")
    if issue.is_settled:
        raise ValidationError("Cannot delete a paid line. Undo payment first.")
    if issue.driver_status == FarmerGoodsIssue.DriverStatus.REJECTED:
        raise ValidationError("Cannot delete a rejected issue line.")
    batch_ref = issue.batch_ref or ""
    _reverse_farmer_goods_issue_line(issue)
    issue.delete()
    remaining = FarmerGoodsIssue.objects.filter(batch_ref=batch_ref).count() if batch_ref else 0
    return {"batch_ref": batch_ref, "remaining": remaining}


@transaction.atomic
def update_farmer_goods_issue_line(*, issue_id, product, quantity):
    """
    Replace one issue line's product/quantity while keeping the same receipt batch.
    """
    issue = (
        FarmerGoodsIssue.objects.select_related("product", "from_branch")
        .select_for_update()
        .filter(pk=issue_id)
        .first()
    )
    if not issue:
        raise ValidationError("Issue line not found.")
    if issue.is_settled:
        raise ValidationError("Cannot edit a paid line. Undo payment first.")
    if issue.driver_status == FarmerGoodsIssue.DriverStatus.REJECTED:
        raise ValidationError("Cannot edit a rejected issue line.")

    batch_ref = issue.batch_ref or ""
    issue_to_type = issue.issue_to_type
    issue_to_id = issue.issue_to_id
    from_branch = issue.from_branch
    issued_at = issue.date
    prior_driver_status = issue.driver_status
    prior_responded_at = issue.driver_responded_at
    prior_response_note = issue.driver_response_note or ""

    _reverse_farmer_goods_issue_line(issue)
    issue.delete()

    new_issue = issue_goods(
        product=product,
        quantity=quantity,
        issue_to_type=issue_to_type,
        issue_to_id=issue_to_id,
        from_branch=from_branch,
        batch_ref=batch_ref,
        issued_at=issued_at,
        driver_status=prior_driver_status
        if issue_to_type == FarmerGoodsIssue.IssueToType.COLLECTION_POINT
        else None,
    )
    if issue_to_type == FarmerGoodsIssue.IssueToType.COLLECTION_POINT:
        new_issue.driver_responded_at = prior_responded_at
        new_issue.driver_response_note = prior_response_note
        new_issue.save(update_fields=["driver_responded_at", "driver_response_note"])
    return new_issue


@transaction.atomic
def add_farmer_goods_issue_line(*, batch_ref, product, quantity):
    """Append one product line to an existing unsettled receipt."""
    existing = list(
        FarmerGoodsIssue.objects.select_related("from_branch", "product")
        .filter(batch_ref=batch_ref)
        .order_by("id")
    )
    if not existing:
        raise ValidationError("Issue receipt not found.")
    head = existing[0]
    if head.driver_status == FarmerGoodsIssue.DriverStatus.REJECTED:
        raise ValidationError("Cannot add a product to a rejected issue.")
    if all(row.is_settled for row in existing):
        raise ValidationError("Cannot add a product to a paid receipt. Undo payment first.")
    return issue_goods(
        product=product,
        quantity=quantity,
        issue_to_type=head.issue_to_type,
        issue_to_id=head.issue_to_id,
        from_branch=head.from_branch,
        batch_ref=batch_ref,
        issued_at=head.date,
        driver_status=head.driver_status
        if head.issue_to_type == FarmerGoodsIssue.IssueToType.COLLECTION_POINT
        else None,
    )


@transaction.atomic
def update_farmer_goods_issue_header(*, batch_ref, from_branch, issue_to_id, issue_date):
    """Change date, issuing branch, and destination while keeping the same products."""
    existing = list(
        FarmerGoodsIssue.objects.select_related("from_branch", "product")
        .filter(batch_ref=batch_ref)
        .order_by("id")
    )
    if not existing:
        raise ValidationError("Issue receipt not found.")
    head = existing[0]
    if head.driver_status == FarmerGoodsIssue.DriverStatus.REJECTED:
        raise ValidationError("Cannot edit a rejected issue.")
    if all(row.is_settled for row in existing):
        raise ValidationError("Cannot edit a paid receipt. Undo payment first.")
    dest_ok, dest_message = _validate_farmer_goods_issue_destination(
        issue_to_type=head.issue_to_type,
        issue_to_id=issue_to_id,
        from_branch_id=getattr(from_branch, "id", from_branch),
    )
    if not dest_ok:
        raise ValidationError(dest_message)
    line_items = [(row.product, row.quantity) for row in existing]
    return update_farmer_goods_issue_batch(
        batch_ref=batch_ref,
        line_items=line_items,
        issue_to_type=head.issue_to_type,
        issue_to_id=issue_to_id,
        from_branch=from_branch,
        issue_date=issue_date,
    )


@transaction.atomic
def consume_goods(*, product, quantity, source_type, source_id):
    qty = _positive(quantity, "quantity")
    issued_total = (
        FarmerGoodsIssue.objects.filter(product=product, issue_to_type=source_type, issue_to_id=source_id)
        .aggregate(total=Sum("quantity"))
        .get("total")
        or Decimal("0.00")
    )
    consumed_total = (
        ConsumptionSettlement.objects.filter(product=product, source_type=source_type, source_id=source_id)
        .aggregate(total=Sum("quantity"))
        .get("total")
        or Decimal("0.00")
    )
    available = issued_total - consumed_total
    if available < qty:
        raise ValidationError("Cannot consume more than issued stock.")
    return ConsumptionSettlement.objects.create(
        product=product,
        quantity=qty,
        source_type=source_type,
        source_id=source_id,
    )


@transaction.atomic
def record_inventory_usage(*, product, quantity, usage_reason):
    qty = _positive(quantity, "quantity")
    stock, _ = MainStock.objects.select_for_update().get_or_create(
        product=product, defaults={"quantity": Decimal("0.00")}
    )
    if stock.quantity < qty:
        raise ValidationError("Cannot use more than available main stock.")
    stock.quantity -= qty
    stock.save(update_fields=["quantity", "updated_at"])
    return InventoryUsage.objects.create(product=product, quantity=qty, usage_reason=usage_reason)


def _grn_item_selling_unit_price(item):
    """Selling price per unit charged to farmers (GRN issuing price)."""
    issuing = item.issuing_price if item.issuing_price is not None else Decimal("0")
    if issuing > Decimal("0"):
        return issuing.quantize(Decimal("0.01"))
    purchase = item.unit_price if item.unit_price is not None else Decimal("0")
    return purchase.quantize(Decimal("0.01"))


def _latest_grn_selling_unit_price(product_id, branch_id=None, *, allow_transfer_fallback=True):
    """Latest confirmed GRN selling price for a product (optionally scoped to branch)."""
    qs = GRNItem.objects.filter(
        grn__status=GRN.Status.CONFIRMED,
        grn__grn_type__in=[
            GRN.GRNType.FARMER_GOODS,
            GRN.GRNType.CORPORATE,
            GRN.GRNType.STOCK_CORRECTION,
        ],
        product_id=product_id,
    ).select_related("grn")
    if branch_id:
        qs = qs.filter(Q(grn__branch_id=branch_id) | Q(grn__branch_id__isnull=True))
    item = qs.order_by("-grn__date", "-id").first()
    if item:
        return _grn_item_selling_unit_price(item)
    if not branch_id or not allow_transfer_fallback:
        return Decimal("0")

    # Stock may have arrived via transfer; use stored transfer price or source-branch GRN.
    from stock_management.models import StockTransferLine

    transfer_line = (
        StockTransferLine.objects.filter(
            product_id=product_id,
            note__to_branch_id=branch_id,
        )
        .select_related("note")
        .order_by("-note__date", "-id")
        .first()
    )
    if transfer_line is None:
        return Decimal("0")
    stored = transfer_line.unit_price or Decimal("0")
    if stored > Decimal("0"):
        return stored.quantize(Decimal("0.01"))
    return _latest_grn_selling_unit_price(
        product_id,
        transfer_line.note.from_branch_id,
        allow_transfer_fallback=False,
    )


def latest_grn_line_prices(product_id, branch_id=None):
    """
    Return (unit_price, issuing_price) from the latest confirmed purchase GRN line.

    Prefers the same branch, then any branch. Skips stock-correction GRNs so
    corrections do not seed themselves from earlier zero-price fixes.
    Prefers lines that already have a positive rate or issue price.
    """
    base = GRNItem.objects.filter(
        product_id=product_id,
        grn__status=GRN.Status.CONFIRMED,
        grn__grn_type__in=[GRN.GRNType.FARMER_GOODS, GRN.GRNType.CORPORATE],
    ).select_related("grn")

    def _pick(qs):
        priced = (
            qs.filter(Q(unit_price__gt=0) | Q(issuing_price__gt=0))
            .order_by("-grn__date", "-id")
            .first()
        )
        if priced:
            return priced
        return qs.order_by("-grn__date", "-id").first()

    item = None
    if branch_id:
        item = _pick(base.filter(grn__branch_id=branch_id))
    if item is None:
        item = _pick(base)
    if item is None:
        return Decimal("0.00"), Decimal("0.00")
    return (
        (item.unit_price or Decimal("0")).quantize(Decimal("0.01")),
        (item.issuing_price or Decimal("0")).quantize(Decimal("0.01")),
    )


def latest_farmer_goods_unit_price(product_id, branch_id=None):
    """Public helper: latest indicative selling unit price for a product."""
    return _latest_grn_selling_unit_price(product_id, branch_id)


def _fifo_consume_layers(layers, need):
    """Consume quantity from FIFO layers; returns total cost and remaining unpriced qty."""
    cost = Decimal("0")
    remaining = need
    for layer in layers:
        if remaining <= Decimal("0"):
            break
        take = min(layer["remaining"], remaining)
        if take <= Decimal("0"):
            continue
        layer_price = layer.get("unit_price", Decimal("0"))
        cost += take * layer_price
        layer["remaining"] -= take
        remaining -= take
    return cost, remaining


def fifo_price_map_for_issues(display_issues):
    """
    Price issue lines using FIFO by branch + product from confirmed GRN selling prices.
    When FIFO layers are exhausted, fall back to the latest GRN price for that product.
    Returns {issue_id: line_total_price_decimal}.
    """
    issue_list = list(display_issues)
    if not issue_list:
        return {}
    product_ids = {i.product_id for i in issue_list if i.product_id}
    if not product_ids:
        return {}

    layer_map = defaultdict(list)
    grn_items = (
        GRNItem.objects.filter(
            grn__status=GRN.Status.CONFIRMED,
            grn__grn_type__in=[
                GRN.GRNType.FARMER_GOODS,
                GRN.GRNType.CORPORATE,
                GRN.GRNType.STOCK_CORRECTION,
            ],
            product_id__in=product_ids,
        )
        .select_related("grn")
        .order_by("grn__date", "id")
    )
    for item in grn_items:
        qty = item.quantity or Decimal("0")
        if qty <= Decimal("0"):
            continue
        unit_price = _grn_item_selling_unit_price(item)
        layer_map[(item.grn.branch_id, item.product_id)].append(
            {"remaining": qty, "unit_price": unit_price}
        )

    display_ids = {i.id for i in issue_list}
    line_price_map = {}
    # Only stock-applied issues consume FIFO layers (pending CP issues have not deducted yet).
    all_issues_qs = (
        FarmerGoodsIssue.objects.filter(product_id__in=product_ids)
        .exclude(
            driver_status__in=[
                FarmerGoodsIssue.DriverStatus.REJECTED,
                FarmerGoodsIssue.DriverStatus.PENDING,
            ]
        )
        .order_by("date", "id")
        .values("id", "product_id", "quantity", "from_branch_id")
    )
    for issue in all_issues_qs:
        pid = issue["product_id"]
        branch_id = issue["from_branch_id"]
        need = issue["quantity"] or Decimal("0")
        if need <= Decimal("0"):
            if issue["id"] in display_ids:
                line_price_map[issue["id"]] = Decimal("0")
            continue
        cost = Decimal("0")
        for layer_key in ((branch_id, pid), (None, pid)):
            layers = layer_map.get(layer_key)
            if not layers:
                continue
            part_cost, need = _fifo_consume_layers(layers, need)
            cost += part_cost
            if need <= Decimal("0"):
                break
        # Match preview_farmer_goods_line_price: unpriced remainder uses latest GRN
        # selling price. Without this, a partially exhausted FIFO layer understates
        # the line (e.g. 2×2500 + 5 unpriced → 5000/7 ≈ 714.29 instead of ~2500).
        if need > Decimal("0"):
            unit = _latest_grn_selling_unit_price(pid, branch_id)
            if unit > Decimal("0"):
                cost += need * unit
        if issue["id"] in display_ids:
            line_price_map[issue["id"]] = cost.quantize(Decimal("0.01"))

    return line_price_map


def priced_farmer_goods_issue_lines(issues):
    """Return priced line rows and batch total for an issue receipt."""
    issue_list = list(issues)
    price_map = fifo_price_map_for_issues(issue_list)
    lines = []
    total = Decimal("0")
    for issue in issue_list:
        line_amount = price_map.get(issue.id, Decimal("0"))
        qty = issue.quantity or Decimal("0")
        unit_price = (
            (line_amount / qty).quantize(Decimal("0.01")) if qty > Decimal("0") else Decimal("0")
        )
        lines.append(
            {
                "issue": issue,
                "quantity": qty,
                "unit_price": unit_price,
                "line_amount": line_amount,
            }
        )
        total += line_amount
    return lines, total.quantize(Decimal("0.01"))


def goods_category_bucket(category_code):
    """Map product category code to payment-sheet deduction bucket."""
    code = (category_code or "").strip().lower()
    if code == "cattle_feeds":
        return "feed"
    if code == "vitamins":
        return "vitamin"
    if code == "omi":
        return "omi"
    if code == "minerals":
        return "minerals"
    if code in ("bolokka", "ropes"):
        return "others"
    return "others"


def summarize_priced_farmer_goods_issues(issues):
    """
    FIFO-priced farmer goods lines with deduction buckets for the payment sheet.
    Returns (items, total, feed, vitamin, omi, minerals, others).
    """
    issue_list = list(issues)
    lines, total = priced_farmer_goods_issue_lines(issue_list)
    feed_total = Decimal("0")
    vitamin_total = Decimal("0")
    omi_total = Decimal("0")
    minerals_total = Decimal("0")
    others_total = Decimal("0")
    items = []
    for row in lines:
        issue = row["issue"]
        amount = row["line_amount"]
        bucket = goods_category_bucket(issue.product.category)
        if bucket == "feed":
            feed_total += amount
        elif bucket == "vitamin":
            vitamin_total += amount
        elif bucket == "omi":
            omi_total += amount
        elif bucket == "minerals":
            minerals_total += amount
        else:
            others_total += amount
        items.append(
            {
                "issue_id": issue.id,
                "date": issue.date,
                "product_name": issue.product.name if issue.product_id else "—",
                "quantity": row["quantity"],
                "unit_price": row["unit_price"],
                "amount": amount,
                "category_bucket": bucket,
                "batch_ref": issue.batch_ref or "",
                "is_settled": issue.is_settled,
                "settled_at": issue.settled_at,
            }
        )
    q = lambda v: v.quantize(Decimal("0.01"))
    return (
        items,
        q(total),
        q(feed_total),
        q(vitamin_total),
        q(omi_total),
        q(minerals_total),
        q(others_total),
    )


def batch_amount_totals_for_issues(issues):
    """Sum priced line amounts grouped by batch_ref."""
    issue_list = list(issues)
    price_map = fifo_price_map_for_issues(issue_list)
    totals = defaultdict(lambda: Decimal("0"))
    for issue in issue_list:
        totals[issue.batch_ref] += price_map.get(issue.id, Decimal("0"))
    return {ref: amount.quantize(Decimal("0.01")) for ref, amount in totals.items()}


def goods_issue_batch_settlement_summary(issues):
    """Roll up per-line settlement flags for one issue receipt batch."""
    issue_list = list(issues)
    if not issue_list:
        return {
            "settlement_status": "n/a",
            "settled_count": 0,
            "line_count": 0,
            "settled_at": None,
            "driver_status": FarmerGoodsIssue.DriverStatus.NOT_REQUIRED,
        }

    driver_status = issue_list[0].driver_status
    if issue_list[0].issue_to_type == FarmerGoodsIssue.IssueToType.BRANCH:
        return {
            "settlement_status": "n/a",
            "settled_count": 0,
            "line_count": len(issue_list),
            "settled_at": None,
            "driver_status": driver_status,
        }

    if driver_status == FarmerGoodsIssue.DriverStatus.PENDING:
        return {
            "settlement_status": "awaiting_driver",
            "settled_count": 0,
            "line_count": len(issue_list),
            "settled_at": None,
            "driver_status": driver_status,
        }
    if driver_status == FarmerGoodsIssue.DriverStatus.REJECTED:
        return {
            "settlement_status": "rejected",
            "settled_count": 0,
            "line_count": len(issue_list),
            "settled_at": None,
            "driver_status": driver_status,
        }

    settled = [issue for issue in issue_list if issue.is_settled]
    settled_count = len(settled)
    line_count = len(issue_list)
    if settled_count == line_count:
        settled_at = max(
            (issue.settled_at for issue in settled if issue.settled_at),
            default=None,
        )
        return {
            "settlement_status": "settled",
            "settled_count": settled_count,
            "line_count": line_count,
            "settled_at": settled_at,
            "driver_status": driver_status,
        }
    if settled_count > 0:
        return {
            "settlement_status": "partial",
            "settled_count": settled_count,
            "line_count": line_count,
            "settled_at": None,
            "driver_status": driver_status,
        }
    return {
        "settlement_status": "pending",
        "settled_count": 0,
        "line_count": line_count,
        "settled_at": None,
        "driver_status": driver_status,
    }


def branch_farmer_goods_availability_map(branch_id):
    """Branch-level available qty per product (received - transfers out - issues)."""
    received_map = {}
    for row in (
        GRNItem.objects.filter(
            grn__status=GRN.Status.CONFIRMED,
            grn__branch_id=branch_id,
        )
        .values("product_id")
        .annotate(total=Sum("quantity"))
    ):
        pid = row["product_id"]
        if pid:
            received_map[pid] = received_map.get(pid, Decimal("0")) + (row["total"] or Decimal("0"))

    for row in (
        StockTransferLine.objects.filter(note__to_branch_id=branch_id)
        .values("product_id")
        .annotate(total=Sum("quantity"))
    ):
        pid = row["product_id"]
        if pid:
            received_map[pid] = received_map.get(pid, Decimal("0")) + (row["total"] or Decimal("0"))

    for row in (
        StockTransfer.objects.filter(to_branch_id=branch_id)
        .values("product_id")
        .annotate(total=Sum("quantity"))
    ):
        pid = row["product_id"]
        if pid:
            received_map[pid] = received_map.get(pid, Decimal("0")) + (row["total"] or Decimal("0"))

    transfer_map = {
        row["product_id"]: (row["total"] or Decimal("0"))
        for row in StockTransferLine.objects.filter(note__from_branch_id=branch_id)
        .values("product_id")
        .annotate(total=Sum("quantity"))
        if row["product_id"]
    }

    issue_map = {}
    for row in (
        StockIssue.objects.filter(branch_id=branch_id)
        .values("product_id")
        .annotate(total=Sum("quantity"))
    ):
        pid = row["product_id"]
        if pid:
            issue_map[pid] = issue_map.get(pid, Decimal("0")) + (row["total"] or Decimal("0"))

    for row in (
        FarmerGoodsIssue.objects.filter(
            from_branch_id=branch_id,
            issue_to_type__in=[
                FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
                FarmerGoodsIssue.IssueToType.FARMER,
            ],
        )
        .exclude(driver_status=FarmerGoodsIssue.DriverStatus.REJECTED)
        .values("product_id")
        .annotate(total=Sum("quantity"))
    ):
        pid = row["product_id"]
        if pid:
            issue_map[pid] = issue_map.get(pid, Decimal("0")) + (row["total"] or Decimal("0"))

    product_ids = set(received_map) | set(transfer_map) | set(issue_map)
    avail_map = {}
    for pid in product_ids:
        received = received_map.get(pid, Decimal("0"))
        avail_map[pid] = received - transfer_map.get(pid, Decimal("0")) - issue_map.get(pid, Decimal("0"))
    return avail_map


def branch_farmer_goods_available_qty(branch_id, product_id):
    return branch_farmer_goods_availability_map(branch_id).get(product_id, Decimal("0"))


def _global_farmer_goods_available_qty(product_id):
    stock = FarmerGoodsStock.objects.filter(product_id=product_id).first()
    qty = stock.quantity if stock else Decimal("0")
    reserved = (
        FarmerGoodsIssue.objects.filter(
            product_id=product_id,
            driver_status=FarmerGoodsIssue.DriverStatus.PENDING,
        )
        .aggregate(total=Sum("quantity"))
        .get("total")
        or Decimal("0")
    )
    return qty - reserved


def preview_farmer_goods_line_price(*, product_id, quantity, from_branch_id):
    """FIFO price preview for a new issue line (matches receipt pricing logic)."""
    qty = Decimal(str(quantity))
    if qty <= Decimal("0"):
        return Decimal("0"), Decimal("0"), "none"

    layer_map = defaultdict(list)
    grn_items = (
        GRNItem.objects.filter(
            grn__status=GRN.Status.CONFIRMED,
            grn__grn_type__in=[
                GRN.GRNType.FARMER_GOODS,
                GRN.GRNType.CORPORATE,
                GRN.GRNType.STOCK_CORRECTION,
            ],
            product_id=product_id,
        )
        .select_related("grn")
        .order_by("grn__date", "id")
    )
    for item in grn_items:
        layer_qty = item.quantity or Decimal("0")
        if layer_qty <= Decimal("0"):
            continue
        layer_map[(item.grn.branch_id, item.product_id)].append(
            {"remaining": layer_qty, "unit_price": _grn_item_selling_unit_price(item)}
        )

    preview_layers = {key: [dict(layer) for layer in layers] for key, layers in layer_map.items()}

    for issue in (
        FarmerGoodsIssue.objects.filter(product_id=product_id)
        .exclude(
            driver_status__in=[
                FarmerGoodsIssue.DriverStatus.REJECTED,
                FarmerGoodsIssue.DriverStatus.PENDING,
            ]
        )
        .order_by("date", "id")
        .values("quantity", "from_branch_id")
    ):
        need = issue["quantity"] or Decimal("0")
        if need <= Decimal("0"):
            continue
        branch_id = issue["from_branch_id"]
        for layer_key in ((branch_id, product_id), (None, product_id)):
            layers = preview_layers.get(layer_key)
            if not layers:
                continue
            _, need = _fifo_consume_layers(layers, need)
            if need <= Decimal("0"):
                break

    cost = Decimal("0")
    remaining = qty
    price_method = "fifo"
    for layer_key in ((from_branch_id, product_id), (None, product_id)):
        layers = preview_layers.get(layer_key)
        if not layers:
            continue
        part_cost, remaining = _fifo_consume_layers(layers, remaining)
        cost += part_cost
        if remaining <= Decimal("0"):
            break

    if remaining > Decimal("0"):
        unit = _latest_grn_selling_unit_price(product_id, from_branch_id)
        if unit > Decimal("0"):
            fifo_part = cost
            cost += remaining * unit
            price_method = "fifo_latest" if fifo_part > Decimal("0") else "latest"
        else:
            price_method = "unpriced"

    line_total = cost.quantize(Decimal("0.01"))
    unit_price = (line_total / qty).quantize(Decimal("0.01")) if qty else Decimal("0")
    return unit_price, line_total, price_method


def _validate_farmer_goods_issue_destination(*, issue_to_type, issue_to_id, from_branch_id):
    if not issue_to_type or not issue_to_id:
        return True, ""
    if issue_to_type == FarmerGoodsIssue.IssueToType.COLLECTION_POINT:
        from masters.models import CollectionPoint

        point = CollectionPoint.objects.select_related("route").filter(pk=issue_to_id).first()
        if not point or not point.route_id or point.route.branch_id != from_branch_id:
            return False, "Collection point does not belong to the selected branch."
        return True, ""
    if issue_to_type == FarmerGoodsIssue.IssueToType.FARMER:
        from masters.models import Farmer

        farmer = Farmer.objects.filter(pk=issue_to_id).first()
        if not farmer or farmer.branch_id != from_branch_id:
            return False, "Farmer does not belong to the selected branch."
        return True, ""
    return True, ""


def check_farmer_goods_issue_line(
    *,
    from_branch_id,
    product_id,
    quantity,
    issue_to_type=None,
    issue_to_id=None,
):
    product = Product.objects.filter(pk=product_id).first()
    if not product:
        raise ValidationError("Product not found.")

    qty = Decimal(str(quantity)) if quantity not in (None, "") else Decimal("1")
    if qty <= Decimal("0"):
        raise ValidationError("Quantity must be greater than zero.")

    movement_available = branch_farmer_goods_available_qty(from_branch_id, product_id)
    on_hand = _branch_stock_on_hand(from_branch_id, product_id)
    branch_available = min(movement_available, on_hand)
    if branch_available < Decimal("0"):
        branch_available = Decimal("0")
    global_available = _global_farmer_goods_available_qty(product_id)
    effective_available = min(branch_available, global_available)
    if effective_available < Decimal("0"):
        effective_available = Decimal("0")
    unit_price, line_total, price_method = preview_farmer_goods_line_price(
        product_id=product_id,
        quantity=qty,
        from_branch_id=from_branch_id,
    )
    dest_ok, dest_message = _validate_farmer_goods_issue_destination(
        issue_to_type=issue_to_type,
        issue_to_id=issue_to_id,
        from_branch_id=from_branch_id,
    )
    can_issue = qty <= effective_available and dest_ok

    return {
        "product_id": product.id,
        "product_name": product.name,
        "unit": product.unit or "",
        "quantity": qty,
        "branch_available": branch_available,
        "branch_on_hand": on_hand,
        "movement_available": movement_available,
        "global_available": global_available,
        "effective_available": effective_available,
        "unit_price": unit_price,
        "line_total": line_total,
        "price_method": price_method,
        "can_issue": can_issue,
        "destination_valid": dest_ok,
        "destination_message": dest_message,
    }


def farmer_goods_branch_check_rows(branch_id):
    """Products at a branch with availability and latest GRN unit price."""
    avail_map = branch_farmer_goods_availability_map(branch_id)
    if not avail_map:
        return []

    product_ids = list(avail_map.keys())
    on_hand_map = dict(
        BranchStock.objects.filter(branch_id=branch_id, product_id__in=product_ids).values_list(
            "product_id", "quantity"
        )
    )
    pending_map = {
        row["product_id"]: row["total"] or Decimal("0")
        for row in FarmerGoodsIssue.objects.filter(
            product_id__in=product_ids,
            driver_status=FarmerGoodsIssue.DriverStatus.PENDING,
        )
        .values("product_id")
        .annotate(total=Sum("quantity"))
    }
    global_map = dict(
        FarmerGoodsStock.objects.filter(product_id__in=product_ids).values_list("product_id", "quantity")
    )
    products = {
        p.id: p
        for p in Product.objects.filter(id__in=product_ids, status=Product.Status.ACTIVE).only(
            "id", "name", "unit"
        )
    }

    rows = []
    for product_id, movement_available in avail_map.items():
        product = products.get(product_id)
        if not product:
            continue
        on_hand = on_hand_map.get(product_id, Decimal("0"))
        pool = global_map.get(product_id, Decimal("0")) - pending_map.get(product_id, Decimal("0"))
        branch_available = min(movement_available, on_hand)
        if branch_available < Decimal("0"):
            branch_available = Decimal("0")
        if pool < Decimal("0"):
            pool = Decimal("0")
        unit_price = _latest_grn_selling_unit_price(product_id, branch_id)
        rows.append(
            {
                "product_id": product.id,
                "product_name": product.name,
                "unit": product.unit or "",
                "branch_available": branch_available,
                "global_available": pool,
                "effective_available": min(branch_available, pool),
                "unit_price": unit_price,
            }
        )
    rows.sort(key=lambda row: row["product_name"].lower())
    return rows

def user_can_view_driver_farmer_goods_requests(user):
    """Admin, BM, superuser, or anyone who can issue farmer goods."""
    from user_management.templatetags.user_tags import (
        user_can_manage_farmer_goods_notifications,
    )

    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user_can_manage_farmer_goods_notifications(user):
        return True
    return user.has_perm("suppliers.add_farmergoodsissue")


def _branch_ids_for_driver_goods_request_viewer(user):
    from user_management.templatetags.user_tags import user_is_admin_or_superuser

    if user_is_admin_or_superuser(user):
        return None  # all branches
    ids = set(_branch_ids_for_farmer_goods_manager(user))
    ids.update(user.assigned_branches.values_list("id", flat=True))
    return ids


def driver_farmer_goods_requests_for_user(user, *, status=None, with_line_count=True):
    """All driver goods requests in the user's branch scope (optional status filter)."""
    from django.db.models import Count, Q

    from .models import DriverFarmerGoodsRequest

    if not user_can_view_driver_farmer_goods_requests(user):
        return DriverFarmerGoodsRequest.objects.none()
    qs = DriverFarmerGoodsRequest.objects.all().select_related(
        "route",
        "collection_point",
        "from_branch",
        "route__branch",
        "fulfilled_by",
    )
    if with_line_count:
        qs = qs.prefetch_related("lines__product").annotate(
            line_count=Count("lines", distinct=True)
        )
    qs = qs.order_by("-requested_at", "-id")
    if status:
        qs = qs.filter(status=status)
    branch_ids = _branch_ids_for_driver_goods_request_viewer(user)
    if branch_ids is not None:
        if not branch_ids:
            return qs.none()
        qs = qs.filter(
            Q(from_branch_id__in=branch_ids)
            | Q(route__branch_id__in=branch_ids)
            | Q(collection_point__route__branch_id__in=branch_ids)
        )
    return qs


def pending_driver_farmer_goods_requests_for_user(user):
    from .models import DriverFarmerGoodsRequest

    return driver_farmer_goods_requests_for_user(
        user, status=DriverFarmerGoodsRequest.Status.PENDING
    )


@transaction.atomic
def create_driver_farmer_goods_request(*, route, collection_point, line_items, note=""):
    from stock_management.models import Product
    from .models import DriverFarmerGoodsRequest, DriverFarmerGoodsRequestLine

    if collection_point.route_id != route.pk:
        raise ValidationError("Collection point is not on your route.")
    if not line_items:
        raise ValidationError("Add at least one product line.")

    price_branch_id = getattr(route, "branch_id", None)
    if not price_branch_id:
        raise ValidationError("Your route has no branch assigned.")

    cleaned = []
    blocked = []
    for product_id, quantity in line_items:
        qty = _positive(quantity, "quantity")
        try:
            pid = int(product_id)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Invalid product.") from exc
        product = Product.objects.filter(pk=pid, status=Product.Status.ACTIVE).first()
        if not product:
            raise ValidationError("Product not found or inactive.")
        unit_price = latest_farmer_goods_unit_price(product.id, price_branch_id)
        if unit_price <= Decimal("0"):
            blocked.append(f"{product.name} (no price)")
            continue
        branch_available = branch_farmer_goods_available_qty(price_branch_id, product.id)
        global_available = _global_farmer_goods_available_qty(product.id)
        effective = min(branch_available, global_available)
        if effective <= Decimal("0"):
            blocked.append(f"{product.name} (no stock at branch)")
            continue
        cleaned.append((product, qty))

    if blocked:
        names = ", ".join(blocked[:5])
        extra = f" (+{len(blocked) - 5} more)" if len(blocked) > 5 else ""
        raise ValidationError(
            f"Cannot request: {names}{extra}. "
            "Only products with branch stock and an issuing price can be requested."
        )
    if not cleaned:
        raise ValidationError("Add at least one product with stock at your branch.")

    request_obj = DriverFarmerGoodsRequest.objects.create(
        route=route,
        collection_point=collection_point,
        from_branch=getattr(route, "branch", None),
        note=(note or "").strip()[:255],
        status=DriverFarmerGoodsRequest.Status.PENDING,
    )
    DriverFarmerGoodsRequestLine.objects.bulk_create(
        [
            DriverFarmerGoodsRequestLine(request=request_obj, product=product, quantity=qty)
            for product, qty in cleaned
        ]
    )
    return request_obj


@transaction.atomic
def fulfill_driver_farmer_goods_request(
    *, request_obj, user, note="", issue_date=None, issue_quantities=None
):
    """Issue goods for a driver request.

    Caps each line at available stock and the requested quantity (or a
    staff-entered qty that must not exceed the request), skips out-of-stock /
    zero-qty items (recorded), and marks issues ACCEPTED so the driver is not
    asked to accept again.
    """
    from .models import DriverFarmerGoodsRequest

    request_obj = (
        DriverFarmerGoodsRequest.objects.select_for_update()
        .select_related("collection_point", "from_branch", "route")
        .prefetch_related("lines__product")
        .get(pk=request_obj.pk)
    )
    if request_obj.status != DriverFarmerGoodsRequest.Status.PENDING:
        raise ValidationError("This request is no longer pending.")

    lines = list(request_obj.lines.select_related("product").order_by("id"))
    if not lines:
        raise ValidationError("Request has no product lines.")

    qty_overrides = {}
    if issue_quantities:
        for key, raw in issue_quantities.items():
            try:
                line_id = int(key)
            except (TypeError, ValueError) as exc:
                raise ValidationError("Invalid product line.") from exc
            try:
                qty = Decimal(str(raw).strip() or "0")
            except Exception as exc:
                raise ValidationError("Invalid issue quantity.") from exc
            if qty < Decimal("0"):
                raise ValidationError("Issue quantity cannot be negative.")
            qty_overrides[line_id] = qty.quantize(Decimal("0.01"))

    issue_lines = []
    skipped = []
    from_branch_id = request_obj.from_branch_id
    for line in lines:
        global_available = _global_farmer_goods_available_qty(line.product_id)
        if from_branch_id:
            branch_available = min(
                branch_farmer_goods_available_qty(from_branch_id, line.product_id),
                _branch_stock_on_hand(from_branch_id, line.product_id),
            )
            available = min(global_available, branch_available)
        else:
            available = global_available
        if available < Decimal("0"):
            available = Decimal("0")
        requested = line.quantity or Decimal("0")
        if line.id in qty_overrides:
            wanted = qty_overrides[line.id]
            if wanted > requested:
                raise ValidationError(
                    f"{line.product.name}: issue quantity ({wanted}) cannot exceed "
                    f"requested ({requested})."
                )
        else:
            wanted = requested

        if wanted <= Decimal("0"):
            skipped.append(f"{line.product.name} (requested {requested}, issued 0)")
            line.issued_quantity = Decimal("0.00")
            line.save(update_fields=["issued_quantity"])
            continue
        if available <= Decimal("0"):
            skipped.append(f"{line.product.name} (requested {requested}, out of stock)")
            line.issued_quantity = Decimal("0.00")
            line.save(update_fields=["issued_quantity"])
            continue
        issue_qty = min(wanted, available).quantize(Decimal("0.01"))
        if issue_qty <= Decimal("0"):
            skipped.append(f"{line.product.name} (requested {requested}, out of stock)")
            line.issued_quantity = Decimal("0.00")
            line.save(update_fields=["issued_quantity"])
            continue
        if issue_qty < wanted:
            skipped.append(
                f"{line.product.name} (wanted {wanted}, issued {issue_qty} — limited stock)"
            )
        elif issue_qty < requested:
            skipped.append(
                f"{line.product.name} (requested {requested}, issued {issue_qty})"
            )
        issue_lines.append((line.product, issue_qty))
        line.issued_quantity = issue_qty
        line.save(update_fields=["issued_quantity"])

    if not issue_lines:
        raise ValidationError(
            "No products could be issued — enter a quantity greater than zero for "
            "at least one in-stock item. "
            + ("; ".join(skipped) if skipped else "")
        )

    created, batch_ref = issue_goods_batch(
        line_items=issue_lines,
        issue_to_type=FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
        issue_to_id=request_obj.collection_point_id,
        from_branch=request_obj.from_branch or getattr(request_obj.route, "branch", None),
        issue_date=issue_date,
        driver_status=FarmerGoodsIssue.DriverStatus.ACCEPTED,
    )
    request_obj.status = DriverFarmerGoodsRequest.Status.ISSUED
    request_obj.fulfilled_at = timezone.now()
    request_obj.fulfilled_by = user
    request_obj.issued_batch_ref = batch_ref
    request_obj.fulfill_note = (note or "").strip()[:255]
    request_obj.skipped_out_of_stock = "; ".join(skipped)
    request_obj.save(
        update_fields=[
            "status",
            "fulfilled_at",
            "fulfilled_by",
            "issued_batch_ref",
            "fulfill_note",
            "skipped_out_of_stock",
        ]
    )
    return {
        "request": request_obj,
        "issues": created,
        "batch_ref": batch_ref,
        "skipped": skipped,
        "issued_count": len(created),
    }


@transaction.atomic
def cancel_driver_farmer_goods_request(*, request_obj, route=None, user=None, note=""):
    from django.utils import timezone

    from .models import DriverFarmerGoodsRequest

    request_obj = DriverFarmerGoodsRequest.objects.select_for_update().get(pk=request_obj.pk)
    if route is not None and request_obj.route_id != route.pk:
        raise ValidationError("This request does not belong to your route.")
    if request_obj.status != DriverFarmerGoodsRequest.Status.PENDING:
        raise ValidationError("Only pending requests can be cancelled.")
    request_obj.status = DriverFarmerGoodsRequest.Status.CANCELLED
    update_fields = ["status"]
    cleaned_note = (note or "").strip()
    if cleaned_note:
        request_obj.fulfill_note = cleaned_note[:255]
        update_fields.append("fulfill_note")
    if user is not None:
        request_obj.fulfilled_by = user
        request_obj.fulfilled_at = timezone.now()
        update_fields.extend(["fulfilled_by", "fulfilled_at"])
    request_obj.save(update_fields=update_fields)
    return request_obj
