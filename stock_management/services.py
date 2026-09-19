from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from branches.utils import get_user_branch_ids

from .models import (
    BranchStock,
    MainStock,
    Product,
    StockIssue,
    StockLog,
    StockTransfer,
    StockTransferLine,
    StockTransferNote,
)


def _validate_positive_quantity(quantity):
    if quantity is None or Decimal(str(quantity)) <= 0:
        raise ValidationError("Quantity must be greater than zero.")


def _validate_branch_access(actor, branch):
    if actor is None or not getattr(actor, "is_authenticated", False) or actor.is_superuser:
        return
    allowed_ids = get_user_branch_ids(actor) or []
    if branch.id not in allowed_ids:
        raise ValidationError("You do not have access to this branch.")


def assert_source_branch_can_dispatch(*, branch, product, quantity, on_hand):
    """
    Block transfers/issues that would push movement Available or on-hand below zero.

    Caps at min(on-hand, movement Available) so pending farmer-goods soft-reserves
    are respected — not only raw BranchStock.
    """
    qty = Decimal(str(quantity))
    _validate_positive_quantity(qty)
    on_hand_qty = Decimal(str(on_hand if on_hand is not None else "0"))
    from suppliers.services import branch_farmer_goods_available_qty

    movement = Decimal(str(branch_farmer_goods_available_qty(branch.id, product.id)))
    effective = min(on_hand_qty, movement)
    if effective < Decimal("0"):
        effective = Decimal("0")
    if qty > effective:
        raise ValidationError(
            f"Insufficient available stock at {branch.name} for {product.name}: "
            f"need {qty}, available {effective} "
            f"(on-hand {on_hand_qty}, movement available {movement}). "
            "Stock cannot go negative."
        )
    return qty


def grn_unit_price_for_product(product_id, branch_id=None):
    """Unit price from the latest related confirmed GRN (issuing price, else purchase)."""
    from suppliers.services import _latest_grn_selling_unit_price

    return _latest_grn_selling_unit_price(
        product_id, branch_id, allow_transfer_fallback=False
    )


def transfer_note_priced_lines(note):
    """
    Lines for print/display with GRN unit price.
    Uses stored line.unit_price when set; otherwise looks up GRN at the source branch.
    """
    rows = []
    from_branch_id = note.from_branch_id
    for line in note.lines.all():
        unit_price = line.unit_price or Decimal("0")
        if unit_price <= Decimal("0"):
            unit_price = grn_unit_price_for_product(line.product_id, from_branch_id)
        qty = line.quantity or Decimal("0")
        amount = (qty * unit_price).quantize(Decimal("0.01"))
        rows.append(
            {
                "line": line,
                "product": line.product,
                "quantity": qty,
                "unit_price": unit_price.quantize(Decimal("0.01")),
                "amount": amount,
            }
        )
    return rows


@transaction.atomic
def transfer_from_main_to_branch(*, product, quantity, to_branch, created_by=None, actor=None):
    qty = Decimal(str(quantity))
    _validate_positive_quantity(qty)
    _validate_branch_access(actor, to_branch)

    main_stock, _ = MainStock.objects.select_for_update().get_or_create(
        product=product,
        defaults={"quantity": Decimal("0.00")},
    )
    if main_stock.quantity < qty:
        raise ValidationError("Cannot transfer more than available main stock.")

    branch_stock, _ = BranchStock.objects.select_for_update().get_or_create(
        branch=to_branch,
        product=product,
        defaults={"quantity": Decimal("0.00")},
    )

    main_stock.quantity -= qty
    branch_stock.quantity += qty

    main_stock.save(update_fields=["quantity", "updated_at"])
    branch_stock.save(update_fields=["quantity", "updated_at"])

    transfer = StockTransfer.objects.create(
        product=product,
        quantity=qty,
        from_location="Main",
        to_branch=to_branch,
        created_by=created_by,
    )

    StockLog.objects.create(
        product=product,
        quantity=qty,
        action_type=StockLog.ActionType.TRANSFER,
        source="Main",
        destination=f"Branch: {to_branch.name}",
        reference=f"st-legacy:{transfer.pk}",
    )
    return transfer


def _merge_line_quantities(lines):
    """lines: iterable of (Product, quantity). Merges duplicate products by id."""
    merged = {}
    for product, qty in lines:
        pid = product.pk
        q = Decimal(str(qty))
        merged[pid] = merged.get(pid, Decimal("0")) + q
    return merged


@transaction.atomic
def create_stock_transfer_note(
    *,
    from_branch,
    to_branch,
    line_items,
    date=None,
    remarks="",
    created_by=None,
    actor=None,
):
    """
    Branch → branch transfer with multiple products.
    line_items: list of (product, quantity) or dicts with keys product / quantity.
    """
    if from_branch.pk == to_branch.pk:
        raise ValidationError("Source and destination branch must be different.")

    _validate_branch_access(actor, from_branch)

    normalized = []
    for row in line_items:
        if isinstance(row, dict):
            product = row["product"]
            qty = row["quantity"]
        else:
            product, qty = row
        _validate_positive_quantity(Decimal(str(qty)))
        normalized.append((product, Decimal(str(qty))))

    merged = _merge_line_quantities(normalized)
    if not merged:
        raise ValidationError("Add at least one product line with quantity.")

    note = StockTransferNote(
        from_branch=from_branch,
        to_branch=to_branch,
        date=date,
        remarks=remarks or "",
        created_by=created_by,
    )
    note.save()

    for product_id, qty in merged.items():
        product = Product.objects.get(pk=product_id)
        from_stock, _ = BranchStock.objects.select_for_update().get_or_create(
            branch=from_branch,
            product=product,
            defaults={"quantity": Decimal("0.00")},
        )
        if from_stock.quantity < qty:
            raise ValidationError(
                f"Insufficient stock at {from_branch.name} for {product.name}: "
                f"need {qty}, have {from_stock.quantity}."
            )
        assert_source_branch_can_dispatch(
            branch=from_branch,
            product=product,
            quantity=qty,
            on_hand=from_stock.quantity,
        )
        to_stock, _ = BranchStock.objects.select_for_update().get_or_create(
            branch=to_branch,
            product=product,
            defaults={"quantity": Decimal("0.00")},
        )
        from_stock.quantity -= qty
        to_stock.quantity += qty
        from_stock.save(update_fields=["quantity", "updated_at"])
        to_stock.save(update_fields=["quantity", "updated_at"])

        StockTransferLine.objects.create(
            note=note,
            product=product,
            quantity=qty,
            unit_price=grn_unit_price_for_product(product.id, from_branch.id),
        )
        StockLog.objects.create(
            product=product,
            quantity=qty,
            action_type=StockLog.ActionType.TRANSFER,
            source=f"Branch: {from_branch.name}",
            destination=f"Branch: {to_branch.name}",
            reference=f"st-note:{note.pk}",
        )
    return note


@transaction.atomic
def update_stock_transfer_note(
    *,
    note,
    from_branch,
    to_branch,
    line_items,
    date=None,
    remarks="",
    actor=None,
):
    """
    Edit an existing transfer note by reversing old movement then applying new movement.
    """
    if from_branch.pk == to_branch.pk:
        raise ValidationError("Source and destination branch must be different.")

    _validate_branch_access(actor, from_branch)

    normalized = []
    for row in line_items:
        if isinstance(row, dict):
            product = row["product"]
            qty = row["quantity"]
        else:
            product, qty = row
        _validate_positive_quantity(Decimal(str(qty)))
        normalized.append((product, Decimal(str(qty))))
    merged = _merge_line_quantities(normalized)
    if not merged:
        raise ValidationError("Add at least one product line with quantity.")

    note = (
        StockTransferNote.objects.select_for_update()
        .select_related("from_branch", "to_branch")
        .prefetch_related("lines__product")
        .get(pk=note.pk)
    )

    # 1) Reverse old stock effect from saved note.
    old_from = note.from_branch
    old_to = note.to_branch
    old_lines = list(note.lines.all())
    for line in old_lines:
        qty = line.quantity
        to_stock, _ = BranchStock.objects.select_for_update().get_or_create(
            branch=old_to,
            product=line.product,
            defaults={"quantity": Decimal("0.00")},
        )
        if to_stock.quantity < qty:
            raise ValidationError(
                f"Cannot edit transfer {note.transfer_number}: "
                f"{old_to.name} has only {to_stock.quantity} of {line.product.name}, "
                f"but {qty} must be reversed."
            )
        from_stock, _ = BranchStock.objects.select_for_update().get_or_create(
            branch=old_from,
            product=line.product,
            defaults={"quantity": Decimal("0.00")},
        )
        to_stock.quantity -= qty
        from_stock.quantity += qty
        to_stock.save(update_fields=["quantity", "updated_at"])
        from_stock.save(update_fields=["quantity", "updated_at"])

    # 2) Replace note header and lines with new values.
    note.from_branch = from_branch
    note.to_branch = to_branch
    if date is not None:
        note.date = date
    note.remarks = remarks or ""
    note.save(update_fields=["from_branch", "to_branch", "date", "remarks"])
    note.lines.all().delete()

    # 3) Apply new movement.
    for product_id, qty in merged.items():
        product = Product.objects.get(pk=product_id)
        from_stock, _ = BranchStock.objects.select_for_update().get_or_create(
            branch=from_branch,
            product=product,
            defaults={"quantity": Decimal("0.00")},
        )
        if from_stock.quantity < qty:
            raise ValidationError(
                f"Insufficient stock at {from_branch.name} for {product.name}: "
                f"need {qty}, have {from_stock.quantity}."
            )
        assert_source_branch_can_dispatch(
            branch=from_branch,
            product=product,
            quantity=qty,
            on_hand=from_stock.quantity,
        )
        to_stock, _ = BranchStock.objects.select_for_update().get_or_create(
            branch=to_branch,
            product=product,
            defaults={"quantity": Decimal("0.00")},
        )
        from_stock.quantity -= qty
        to_stock.quantity += qty
        from_stock.save(update_fields=["quantity", "updated_at"])
        to_stock.save(update_fields=["quantity", "updated_at"])

        StockTransferLine.objects.create(
            note=note,
            product=product,
            quantity=qty,
            unit_price=grn_unit_price_for_product(product.id, from_branch.id),
        )
        StockLog.objects.create(
            product=product,
            quantity=qty,
            action_type=StockLog.ActionType.TRANSFER,
            source=f"Branch: {from_branch.name}",
            destination=f"Branch: {to_branch.name}",
            reference=f"st-note:{note.pk}",
        )
    return note


@transaction.atomic
def delete_stock_transfer_note(*, note, actor=None):
    """
    Delete a transfer note after reversing its stock movement.
    """
    note = (
        StockTransferNote.objects.select_for_update()
        .select_related("from_branch", "to_branch")
        .prefetch_related("lines__product")
        .get(pk=note.pk)
    )
    _validate_branch_access(actor, note.from_branch)
    _validate_branch_access(actor, note.to_branch)

    old_from = note.from_branch
    old_to = note.to_branch
    old_lines = list(note.lines.all())
    for line in old_lines:
        qty = line.quantity
        to_stock, _ = BranchStock.objects.select_for_update().get_or_create(
            branch=old_to,
            product=line.product,
            defaults={"quantity": Decimal("0.00")},
        )
        if to_stock.quantity < qty:
            raise ValidationError(
                f"Cannot delete transfer {note.transfer_number}: "
                f"{old_to.name} has only {to_stock.quantity} of {line.product.name}, "
                f"but {qty} must be reversed."
            )
        from_stock, _ = BranchStock.objects.select_for_update().get_or_create(
            branch=old_from,
            product=line.product,
            defaults={"quantity": Decimal("0.00")},
        )
        to_stock.quantity -= qty
        from_stock.quantity += qty
        to_stock.save(update_fields=["quantity", "updated_at"])
        from_stock.save(update_fields=["quantity", "updated_at"])

    ref = note.transfer_number
    note.delete()
    return ref


@transaction.atomic
def issue_from_branch_stock(*, product, quantity, branch, issued_to_type, issued_to_id, actor=None):
    qty = Decimal(str(quantity))
    _validate_positive_quantity(qty)
    _validate_branch_access(actor, branch)

    branch_stock, _ = BranchStock.objects.select_for_update().get_or_create(
        branch=branch,
        product=product,
        defaults={"quantity": Decimal("0.00")},
    )
    if branch_stock.quantity < qty:
        raise ValidationError("Cannot issue more than available branch stock.")
    assert_source_branch_can_dispatch(
        branch=branch,
        product=product,
        quantity=qty,
        on_hand=branch_stock.quantity,
    )

    branch_stock.quantity -= qty
    branch_stock.save(update_fields=["quantity", "updated_at"])

    issue = StockIssue.objects.create(
        product=product,
        quantity=qty,
        branch=branch,
        issued_to_type=issued_to_type,
        issued_to_id=issued_to_id,
    )

    StockLog.objects.create(
        product=product,
        quantity=qty,
        action_type=StockLog.ActionType.ISSUE,
        source=f"Branch: {branch.name}",
        destination=f"{issue.get_issued_to_type_display()} ID: {issued_to_id}",
        reference=f"stock-issue:{issue.pk}",
    )
    return issue


def _stock_count_book_qty(branch_id, product_id):
    from suppliers.negative_branch_stock import branch_product_available

    available = Decimal(str(branch_product_available(branch_id, product_id)))
    if available < Decimal("0"):
        available = Decimal("0.00")
    return available.quantize(Decimal("0.01"))


@transaction.atomic
def create_stock_count(*, branch, date=None, remarks="", created_by=None, actor=None):
    """Create a draft stocktake with book Available snapped for products on hand."""
    from .models import StockCount, StockCountLine

    _validate_branch_access(actor, branch)
    count = StockCount(
        branch=branch,
        date=date or timezone.now().date(),
        remarks=remarks or "",
        created_by=created_by,
        status=StockCount.Status.DRAFT,
    )
    count.save()

    product_ids = set(
        BranchStock.objects.filter(branch=branch)
        .exclude(quantity=0)
        .values_list("product_id", flat=True)
    )
    # Also include products with non-zero movement Available even if ledger is 0.
    for product in Product.objects.filter(status=Product.Status.ACTIVE).order_by("name"):
        book = _stock_count_book_qty(branch.id, product.id)
        if book == 0 and product.id not in product_ids:
            continue
        StockCountLine.objects.create(
            count=count,
            product=product,
            book_qty=book,
            physical_qty=None,
        )
    return count


@transaction.atomic
def refresh_stock_count_book_quantities(*, count, actor=None):
    from .models import StockCount

    count = StockCount.objects.select_for_update().select_related("branch").get(pk=count.pk)
    if count.status != StockCount.Status.DRAFT:
        raise ValidationError("Only draft stock counts can refresh book quantities.")
    _validate_branch_access(actor, count.branch)
    for line in count.lines.select_related("product"):
        line.book_qty = _stock_count_book_qty(count.branch_id, line.product_id)
        line.save(update_fields=["book_qty"])
    return count


@transaction.atomic
def save_stock_count_physical_quantities(*, count, line_quantities, actor=None):
    """
    line_quantities: dict {line_id: physical_qty or None}
    """
    from .models import StockCount, StockCountLine

    count = StockCount.objects.select_for_update().select_related("branch").get(pk=count.pk)
    if count.status != StockCount.Status.DRAFT:
        raise ValidationError("Only draft stock counts can be edited.")
    _validate_branch_access(actor, count.branch)

    lines = {
        line.id: line
        for line in StockCountLine.objects.select_for_update().filter(count=count)
    }
    for line_id, raw_qty in (line_quantities or {}).items():
        line = lines.get(int(line_id))
        if line is None:
            continue
        if raw_qty is None or str(raw_qty).strip() == "":
            line.physical_qty = None
        else:
            qty = Decimal(str(raw_qty))
            if qty < 0:
                raise ValidationError(
                    f"Physical qty for {line.product.name} cannot be negative."
                )
            line.physical_qty = qty.quantize(Decimal("0.01"))
        line.save(update_fields=["physical_qty"])
    return count


@transaction.atomic
def post_stock_count(*, count, actor=None, supplier=None):
    """
    Post variances:
      excess  (physical > book) → STOCK_CORRECTION GRN + confirm
      shortage (physical < book) → StockIssue STOCKTAKE write-off
    Then sync BranchStock to Available.
    """
    from django.utils import timezone as dj_tz

    from suppliers.models import GRN, GRNItem
    from suppliers.negative_branch_stock import (
        branch_product_available,
        resolve_adjustment_supplier,
    )
    from suppliers.services import confirm_grn, finalize_grn_totals, latest_grn_line_prices
    from .models import StockCount, StockIssue

    count = (
        StockCount.objects.select_for_update()
        .select_related("branch")
        .prefetch_related("lines__product")
        .get(pk=count.pk)
    )
    if count.status != StockCount.Status.DRAFT:
        raise ValidationError("This stock count is already posted.")
    _validate_branch_access(actor, count.branch)

    lines = list(count.lines.all())
    if not lines:
        raise ValidationError("Stock count has no product lines.")
    missing = [line.product.name for line in lines if line.physical_qty is None]
    if missing:
        raise ValidationError(
            "Enter physical quantity for all products before posting. "
            f"Missing: {', '.join(missing[:8])}"
            + ("…" if len(missing) > 8 else "")
        )

    # Refresh book qty at post time so variance matches current Available.
    for line in lines:
        line.book_qty = _stock_count_book_qty(count.branch_id, line.product_id)
        line.save(update_fields=["book_qty"])

    excess_lines = []
    shortage_lines = []
    for line in lines:
        variance = (line.physical_qty - line.book_qty).quantize(Decimal("0.01"))
        if variance > 0:
            excess_lines.append((line, variance))
        elif variance < 0:
            shortage_lines.append((line, -variance))

    correction_grn = None
    if excess_lines:
        adj_supplier = supplier or resolve_adjustment_supplier(
            grn_type=GRN.GRNType.STOCK_CORRECTION
        )
        correction_grn = GRN.objects.create(
            supplier=adj_supplier,
            branch=count.branch,
            grn_type=GRN.GRNType.STOCK_CORRECTION,
            date=timezone.localdate(),
            status=GRN.Status.DRAFT,
            discount_scope=GRN.DiscountScope.LINE,
            created_by=actor if getattr(actor, "pk", None) else None,
        )
        for line, excess_qty in excess_lines:
            unit_price, issuing_price = latest_grn_line_prices(
                line.product_id, count.branch_id
            )
            GRNItem.objects.create(
                grn=correction_grn,
                product=line.product,
                quantity=excess_qty,
                free_quantity=Decimal("0.00"),
                unit_price=unit_price or Decimal("0.00"),
                issuing_price=issuing_price or unit_price or Decimal("0.00"),
                line_discount_percent=Decimal("0"),
                line_discount_amount=Decimal("0"),
                total_price=Decimal("0.00"),
            )
        finalize_grn_totals(correction_grn)
        confirm_grn(grn=correction_grn, actor=actor)
        count.correction_grn = correction_grn

    # Write shortages as StockIssue (movement Available), without requiring
    # on-hand to match Available — BranchStock is synced to Available below.
    for line, shortage_qty in shortage_lines:
        issue = StockIssue.objects.create(
            product=line.product,
            quantity=shortage_qty,
            branch=count.branch,
            issued_to_type=StockIssue.IssuedToType.STOCKTAKE,
            issued_to_id=count.pk,
        )
        StockLog.objects.create(
            product=line.product,
            quantity=shortage_qty,
            action_type=StockLog.ActionType.ISSUE,
            source=f"Branch: {count.branch.name}",
            destination=f"Stocktake write-off {count.count_number}",
            reference=f"stock-issue:{issue.pk}",
        )

    # Align on-hand ledger with Available (= physical after variances).
    for line in lines:
        available = branch_product_available(count.branch_id, line.product_id)
        if available < Decimal("0"):
            available = Decimal("0.00")
        target = available.quantize(Decimal("0.01"))
        stock, _ = BranchStock.objects.select_for_update().get_or_create(
            branch_id=count.branch_id,
            product_id=line.product_id,
            defaults={"quantity": target},
        )
        if stock.quantity != target:
            stock.quantity = target
            stock.save(update_fields=["quantity", "updated_at"])

    count.status = StockCount.Status.POSTED
    count.posted_by = actor if getattr(actor, "is_authenticated", False) else None
    count.posted_at = dj_tz.now()
    count.save(
        update_fields=[
            "status",
            "posted_by",
            "posted_at",
            "correction_grn",
        ]
    )
    return count
