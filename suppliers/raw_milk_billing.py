"""Raw-milk collection billing, supplier ledger, and payment allocation."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone

from .models import (
    RawMilkSupplierCollection,
    RawMilkSupplierPayment,
    RawMilkSupplierPaymentAllocation,
    Supplier,
    SupplierAccount,
    SupplierTransaction,
)
from .supplier_rates import supplier_rate_at_date


def _money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _positive(value, field_name="amount"):
    amount = _money(value)
    if amount <= 0:
        raise ValidationError(
            {field_name: f"{field_name.replace('_', ' ').title()} must be greater than zero."}
        )
    return amount


def compute_collection_billing(collection, *, rate_info=None):
    if not collection.supplier_id:
        return {
            "unit_rate": Decimal("0.00"),
            "rate_unit": "",
            "billable_qty": Decimal("0.00"),
            "total_amount": Decimal("0.00"),
        }
    info = rate_info or supplier_rate_at_date(collection.supplier, collection.date)
    rate = _money(info.get("rate"))
    unit = info.get("rate_unit") or Supplier.RateUnit.LITER
    if unit == Supplier.RateUnit.KG:
        qty = _money(collection.kg)
    else:
        qty = _money(collection.liters)
    total = (qty * rate).quantize(Decimal("0.01")) if rate > 0 else Decimal("0.00")
    return {
        "unit_rate": rate,
        "rate_unit": unit,
        "billable_qty": qty,
        "total_amount": total,
    }


def _upsert_supplier_balance(supplier, transaction_type, amount):
    account, _ = SupplierAccount.objects.select_for_update().get_or_create(
        supplier=supplier, defaults={"balance": Decimal("0.00")}
    )
    if transaction_type == SupplierTransaction.TransactionType.CREDIT:
        account.balance = _money(account.balance) + amount
    else:
        account.balance = _money(account.balance) - amount
    account.save(update_fields=["balance", "updated_at"])
    return account


def _post_ledger(*, supplier, transaction_type, amount, reference, description):
    amount = _money(amount)
    if amount <= 0:
        return None
    tx = SupplierTransaction.objects.create(
        supplier=supplier,
        transaction_type=transaction_type,
        amount=amount,
        reference=(reference or "")[:60],
        description=description or "",
    )
    _upsert_supplier_balance(supplier, transaction_type, amount)
    return tx


def collection_ledger_reference(collection):
    return f"RAW:{collection.pk}"


@transaction.atomic
def sync_raw_milk_collection_billing(collection):
    """Apply supplier rate and keep payable ledger in sync (locked after payments)."""
    if not collection.pk or not collection.supplier_id:
        return collection

    row = (
        RawMilkSupplierCollection.objects.select_for_update()
        .select_related("supplier")
        .filter(pk=collection.pk)
        .first()
    )
    if not row or not row.supplier_id:
        return collection

    if row.status == RawMilkSupplierCollection.CollectionStatus.CANCELLED:
        if row.is_billed and _money(row.paid_amount) <= 0 and _money(row.total_amount) > 0:
            _post_ledger(
                supplier=row.supplier,
                transaction_type=SupplierTransaction.TransactionType.DEBIT,
                amount=_money(row.total_amount),
                reference=f"{collection_ledger_reference(row)}:CANCEL",
                description=f"Raw milk collection cancelled: {row.dispatch_no}",
            )
            row.total_amount = Decimal("0.00")
            row.billable_qty = Decimal("0.00")
            row.unit_rate = Decimal("0.00")
            row.is_billed = False
            row.refresh_payment_status(save=False)
            row.save(
                update_fields=[
                    "total_amount",
                    "billable_qty",
                    "unit_rate",
                    "is_billed",
                    "payment_status",
                    "updated_at",
                ],
                skip_billing_sync=True,
            )
        return row

    if _money(row.paid_amount) > 0:
        row.refresh_payment_status(save=False)
        row.save(update_fields=["payment_status", "updated_at"], skip_billing_sync=True)
        return row

    billing = compute_collection_billing(row)
    old_total = _money(row.total_amount)
    new_total = billing["total_amount"]

    row.unit_rate = billing["unit_rate"]
    row.rate_unit = billing["rate_unit"]
    row.billable_qty = billing["billable_qty"]
    row.total_amount = new_total
    row.refresh_payment_status(save=False)

    update_fields = [
        "unit_rate",
        "rate_unit",
        "billable_qty",
        "total_amount",
        "payment_status",
        "updated_at",
    ]

    if not row.is_billed and new_total > 0:
        _post_ledger(
            supplier=row.supplier,
            transaction_type=SupplierTransaction.TransactionType.CREDIT,
            amount=new_total,
            reference=collection_ledger_reference(row),
            description=f"Raw milk billed: {row.dispatch_no}",
        )
        row.is_billed = True
        update_fields.append("is_billed")
    elif row.is_billed and new_total != old_total:
        delta = new_total - old_total
        if delta > 0:
            _post_ledger(
                supplier=row.supplier,
                transaction_type=SupplierTransaction.TransactionType.CREDIT,
                amount=delta,
                reference=f"{collection_ledger_reference(row)}:ADJ",
                description=f"Raw milk amount increase: {row.dispatch_no}",
            )
        elif delta < 0:
            _post_ledger(
                supplier=row.supplier,
                transaction_type=SupplierTransaction.TransactionType.DEBIT,
                amount=abs(delta),
                reference=f"{collection_ledger_reference(row)}:ADJ",
                description=f"Raw milk amount decrease: {row.dispatch_no}",
            )
        if new_total <= 0:
            row.is_billed = False
            update_fields.append("is_billed")

    row.save(update_fields=update_fields, skip_billing_sync=True)

    collection.unit_rate = row.unit_rate
    collection.rate_unit = row.rate_unit
    collection.billable_qty = row.billable_qty
    collection.total_amount = row.total_amount
    collection.paid_amount = row.paid_amount
    collection.payment_status = row.payment_status
    collection.is_billed = row.is_billed
    return row


@transaction.atomic
def reverse_raw_milk_billing_on_delete(collection):
    if not collection.supplier_id or not collection.is_billed:
        return
    unpaid = _money(collection.total_amount) - _money(collection.paid_amount)
    if unpaid <= 0:
        return
    _post_ledger(
        supplier=collection.supplier,
        transaction_type=SupplierTransaction.TransactionType.DEBIT,
        amount=unpaid,
        reference=f"{collection_ledger_reference(collection)}:DEL",
        description=f"Raw milk collection deleted: {collection.dispatch_no}",
    )


def outstanding_collections_qs(supplier):
    return (
        RawMilkSupplierCollection.objects.filter(supplier=supplier)
        .exclude(status=RawMilkSupplierCollection.CollectionStatus.CANCELLED)
        .filter(total_amount__gt=0)
        .filter(total_amount__gt=F("paid_amount"))
        .select_related("branch", "supplier")
        .order_by("date", "id")
    )


@transaction.atomic
def record_raw_milk_payment(
    *,
    supplier,
    amount,
    date=None,
    reference="",
    note="",
    created_by=None,
    collection_ids=None,
    allocation_amounts=None,
):
    if supplier.category != Supplier.Category.RAW_MILK_SUPPLIER:
        raise ValidationError("Payments with collection allocation are only for RAW_MILK_SUPPLIER.")
    if supplier.status != Supplier.Status.ACTIVE:
        raise ValidationError("Cannot record payment for an inactive supplier.")

    pay_amount = _positive(amount)
    pay_date = date or timezone.localdate()
    allocation_amounts = allocation_amounts or {}

    if allocation_amounts:
        explicit = {}
        total_explicit = Decimal("0.00")
        for raw_id, raw_amt in allocation_amounts.items():
            coll_id = int(raw_id)
            amt = _money(raw_amt)
            if amt <= 0:
                continue
            explicit[coll_id] = amt
            total_explicit += amt
        if total_explicit != pay_amount:
            raise ValidationError(
                {"amount": "Payment amount must equal the sum of collection allocations."}
            )
        selected_ids = list(explicit.keys())
        qs = (
            RawMilkSupplierCollection.objects.select_for_update()
            .filter(supplier=supplier, pk__in=selected_ids)
            .order_by("date", "id")
        )
        rows = list(qs)
        if len(rows) != len(selected_ids):
            raise ValidationError("One or more selected collections were not found for this supplier.")
        plan = []
        for coll in rows:
            outstanding = coll.outstanding_amount
            alloc = explicit.get(coll.pk, Decimal("0.00"))
            if alloc > outstanding:
                raise ValidationError(
                    {
                        "allocations": (
                            f"Allocation for {coll.dispatch_no} exceeds outstanding {outstanding}."
                        )
                    }
                )
            if alloc > 0:
                plan.append((coll, alloc))
    else:
        qs = outstanding_collections_qs(supplier).select_for_update()
        if collection_ids is not None:
            ids = [int(x) for x in collection_ids]
            if not ids:
                raise ValidationError({"collections": "Select at least one collection."})
            qs = qs.filter(pk__in=ids)
            rows = list(qs.order_by("date", "id"))
            if len(rows) != len(set(ids)):
                raise ValidationError(
                    "One or more selected collections have no outstanding balance."
                )
        else:
            rows = list(qs)
        if not rows:
            raise ValidationError("No outstanding collections to allocate this payment.")

        remaining = pay_amount
        plan = []
        for coll in rows:
            if remaining <= 0:
                break
            take = min(coll.outstanding_amount, remaining)
            if take > 0:
                plan.append((coll, take))
                remaining -= take
        if remaining > 0:
            raise ValidationError(
                {
                    "amount": (
                        f"Payment exceeds selected outstanding by {remaining}. "
                        "Lower the amount or select more collections."
                    )
                }
            )

    payment = RawMilkSupplierPayment.objects.create(
        supplier=supplier,
        date=pay_date,
        amount=pay_amount,
        reference=reference or "",
        note=note or "",
        created_by=created_by if getattr(created_by, "is_authenticated", False) else None,
    )
    for coll, alloc in plan:
        RawMilkSupplierPaymentAllocation.objects.create(
            payment=payment, collection=coll, amount=alloc
        )
        coll.paid_amount = _money(coll.paid_amount) + alloc
        coll.refresh_payment_status(save=False)
        coll.save(
            update_fields=["paid_amount", "payment_status", "updated_at"],
            skip_billing_sync=True,
        )

    tx = _post_ledger(
        supplier=supplier,
        transaction_type=SupplierTransaction.TransactionType.DEBIT,
        amount=pay_amount,
        reference=reference or f"PAY:{payment.pk}",
        description=note or f"Raw milk payment ({pay_date})",
    )
    if tx:
        payment.ledger_transaction = tx
        payment.save(update_fields=["ledger_transaction"])
    return payment


def raw_milk_supplier_account_summary(supplier):
    account, _ = SupplierAccount.objects.get_or_create(
        supplier=supplier, defaults={"balance": Decimal("0.00")}
    )
    outstanding = (
        outstanding_collections_qs(supplier).aggregate(
            total=Sum(F("total_amount") - F("paid_amount"))
        )["total"]
        or Decimal("0.00")
    )
    billed = (
        RawMilkSupplierCollection.objects.filter(supplier=supplier, is_billed=True)
        .exclude(status=RawMilkSupplierCollection.CollectionStatus.CANCELLED)
        .aggregate(total=Sum("total_amount"))["total"]
        or Decimal("0.00")
    )
    paid = (
        RawMilkSupplierCollection.objects.filter(supplier=supplier)
        .exclude(status=RawMilkSupplierCollection.CollectionStatus.CANCELLED)
        .aggregate(total=Sum("paid_amount"))["total"]
        or Decimal("0.00")
    )
    return {
        "balance": _money(account.balance),
        "outstanding": _money(outstanding),
        "billed": _money(billed),
        "paid": _money(paid),
        "account": account,
    }
