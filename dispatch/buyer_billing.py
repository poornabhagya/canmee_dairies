"""Buyer dispatch billing, account ledger, and payment allocation."""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from canmee_dairies.constants import MILK_LITER_FACTOR
from masters.buyer_rates import buyer_rate_at_date
from masters.models import Buyer

from .models import (
    BuyerAccount,
    BuyerPayment,
    BuyerPaymentAllocation,
    BuyerTransaction,
    MilkDistribution,
)


def _money(value):
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _positive(value, field_name="amount"):
    amount = _money(value)
    if amount <= 0:
        raise ValidationError(
            {field_name: f"{field_name.replace('_', ' ').title()} must be greater than zero."}
        )
    return amount


def compute_dispatch_billing(distribution, *, rate_info=None):
    """Return unit_rate, rate_unit, billable_qty, total_amount for a buyer dispatch."""
    if not distribution.buyer_id:
        return {
            "unit_rate": Decimal("0.00"),
            "rate_unit": "",
            "billable_qty": Decimal("0.00"),
            "total_amount": Decimal("0.00"),
        }
    if getattr(distribution, "is_rate_manual", False) and distribution.unit_rate:
        rate = _money(distribution.unit_rate)
        unit = distribution.rate_unit or Buyer.RateUnit.LITER
    else:
        info = rate_info or buyer_rate_at_date(distribution.buyer, distribution.date)
        rate = _money(info.get("rate"))
        unit = info.get("rate_unit") or Buyer.RateUnit.LITER
    kg = _money(distribution.kg)
    if unit == Buyer.RateUnit.KG:
        qty = kg
    else:
        qty = (kg * MILK_LITER_FACTOR).quantize(Decimal("0.01"))
    total = (qty * rate).quantize(Decimal("0.01")) if rate > 0 else Decimal("0.00")
    return {
        "unit_rate": rate,
        "rate_unit": unit,
        "billable_qty": qty,
        "total_amount": total,
    }


def _upsert_buyer_balance(buyer, transaction_type, amount):
    account, _ = BuyerAccount.objects.select_for_update().get_or_create(
        buyer=buyer, defaults={"balance": Decimal("0.00")}
    )
    if transaction_type == BuyerTransaction.TransactionType.CREDIT:
        account.balance = _money(account.balance) + amount
    else:
        account.balance = _money(account.balance) - amount
    account.save(update_fields=["balance", "updated_at"])
    return account


def _post_ledger(*, buyer, transaction_type, amount, reference, description):
    amount = _money(amount)
    if amount <= 0:
        return None
    tx = BuyerTransaction.objects.create(
        buyer=buyer,
        transaction_type=transaction_type,
        amount=amount,
        reference=reference or "",
        description=description or "",
    )
    _upsert_buyer_balance(buyer, transaction_type, amount)
    return tx


def dispatch_ledger_reference(distribution):
    return f"DISPATCH:{distribution.pk}"


def _dispatch_billing_net(buyer_id, distribution_id):
    """Net CREDIT−DEBIT already posted for this dispatch (payments use PAYMENT: refs)."""
    prefix = f"DISPATCH:{distribution_id}"
    net = Decimal("0.00")
    rows = BuyerTransaction.objects.filter(buyer_id=buyer_id).filter(
        Q(reference=prefix) | Q(reference__startswith=f"{prefix}:")
    )
    for tx in rows:
        if tx.transaction_type == BuyerTransaction.TransactionType.CREDIT:
            net += tx.amount
        else:
            net -= tx.amount
    return _money(net)


def _align_dispatch_ledger(dist, desired, *, credit_description, debit_description, debit_suffix):
    """Post only the difference so repeat saves cannot duplicate bill/cancel rows."""
    desired = _money(desired)
    net = _dispatch_billing_net(dist.buyer_id, dist.pk)
    delta = desired - net
    if delta > 0:
        _post_ledger(
            buyer=dist.buyer,
            transaction_type=BuyerTransaction.TransactionType.CREDIT,
            amount=delta,
            reference=dispatch_ledger_reference(dist),
            description=credit_description,
        )
    elif delta < 0:
        _post_ledger(
            buyer=dist.buyer,
            transaction_type=BuyerTransaction.TransactionType.DEBIT,
            amount=abs(delta),
            reference=f"{dispatch_ledger_reference(dist)}:{debit_suffix}",
            description=debit_description,
        )
    return delta


@transaction.atomic
def sync_dispatch_billing(distribution):
    """
    Apply buyer rate to a dispatch and keep the receivable ledger in sync.

    Locked once any payment has been allocated (paid_amount > 0).
    """
    if not distribution.pk or not distribution.buyer_id:
        return distribution

    dist = (
        MilkDistribution.objects.select_for_update()
        .select_related("buyer")
        .filter(pk=distribution.pk)
        .first()
    )
    if not dist or not dist.buyer_id:
        return distribution

    # Cancelled buyer dispatches: ledger net for this dispatch must be 0 if unpaid.
    if dist.status == MilkDistribution.DistributionStatus.CANCELLED:
        if _money(dist.paid_amount) <= 0:
            _align_dispatch_ledger(
                dist,
                Decimal("0.00"),
                credit_description=f"Dispatch billed: {dist.dispatch_no}",
                debit_description=f"Dispatch cancelled: {dist.dispatch_no}",
                debit_suffix="CANCEL",
            )
            dist.total_amount = Decimal("0.00")
            dist.billable_qty = Decimal("0.00")
            dist.unit_rate = Decimal("0.00")
            dist.is_billed = False
            dist.refresh_payment_status(save=False)
            dist.save(
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
        return dist

    if _money(dist.paid_amount) > 0:
        # Payments lock the rate, but the receivable still has to exist on the ledger.
        desired = _money(dist.total_amount)
        _align_dispatch_ledger(
            dist,
            desired,
            credit_description=f"Dispatch billed: {dist.dispatch_no}",
            debit_description=f"Dispatch amount decrease: {dist.dispatch_no}",
            debit_suffix="ADJ",
        )
        dist.refresh_payment_status(save=False)
        update_fields = ["payment_status", "updated_at"]
        billed = desired > 0
        if dist.is_billed != billed:
            dist.is_billed = billed
            update_fields.append("is_billed")
        dist.save(update_fields=update_fields, skip_billing_sync=True)
        return dist

    billing = compute_dispatch_billing(dist)
    new_total = billing["total_amount"]

    dist.unit_rate = billing["unit_rate"]
    dist.rate_unit = billing["rate_unit"]
    dist.billable_qty = billing["billable_qty"]
    dist.total_amount = new_total
    dist.refresh_payment_status(save=False)

    update_fields = [
        "unit_rate",
        "rate_unit",
        "billable_qty",
        "total_amount",
        "payment_status",
        "updated_at",
    ]

    _align_dispatch_ledger(
        dist,
        new_total,
        credit_description=f"Dispatch billed: {dist.dispatch_no}",
        debit_description=f"Dispatch amount decrease: {dist.dispatch_no}",
        debit_suffix="ADJ",
    )
    billed = new_total > 0
    if dist.is_billed != billed:
        dist.is_billed = billed
        update_fields.append("is_billed")

    dist.save(update_fields=update_fields, skip_billing_sync=True)

    # Keep caller's instance roughly in sync
    distribution.unit_rate = dist.unit_rate
    distribution.rate_unit = dist.rate_unit
    distribution.billable_qty = dist.billable_qty
    distribution.total_amount = dist.total_amount
    distribution.paid_amount = dist.paid_amount
    distribution.payment_status = dist.payment_status
    distribution.is_billed = dist.is_billed
    return dist


@transaction.atomic
def reverse_dispatch_billing_on_delete(distribution):
    """Reverse unpaid billed receivable when a dispatch is deleted."""
    if not distribution.buyer_id:
        return
    dist = distribution
    if not getattr(dist, "buyer", None):
        dist.buyer = Buyer.objects.filter(pk=dist.buyer_id).first()
    if not dist.buyer:
        return
    _align_dispatch_ledger(
        dist,
        Decimal("0.00"),
        credit_description=f"Dispatch billed: {dist.dispatch_no}",
        debit_description=f"Dispatch deleted: {dist.dispatch_no}",
        debit_suffix="DEL",
    )


def rebuild_buyer_account_balance(buyer):
    account, _ = BuyerAccount.objects.select_for_update().get_or_create(
        buyer=buyer, defaults={"balance": Decimal("0.00")}
    )
    net = Decimal("0.00")
    for tx in BuyerTransaction.objects.filter(buyer=buyer):
        if tx.transaction_type == BuyerTransaction.TransactionType.CREDIT:
            net += tx.amount
        else:
            net -= tx.amount
    account.balance = _money(net)
    account.save(update_fields=["balance", "updated_at"])
    return account


@transaction.atomic
def repair_dispatch_billing_ledgers(*, buyer=None):
    """
    Remove duplicate DISPATCH ledger rows and re-post one correct bill/cancel.

    Repeat saves used to insert extra billed/cancelled lines for the same dispatch.
    """
    pks = list(
        MilkDistribution.objects.filter(buyer_id__isnull=False).values_list("pk", flat=True)
    )
    if buyer is not None:
        pks = list(
            MilkDistribution.objects.filter(buyer=buyer).values_list("pk", flat=True)
        )
        buyer_ids = {buyer.pk}
    else:
        buyer_ids = set(
            MilkDistribution.objects.filter(buyer_id__isnull=False)
            .values_list("buyer_id", flat=True)
            .distinct()
        )

    for pk in pks:
        prefix = f"DISPATCH:{pk}"
        buyer_id = MilkDistribution.objects.filter(pk=pk).values_list("buyer_id", flat=True).first()
        if not buyer_id:
            continue
        BuyerTransaction.objects.filter(buyer_id=buyer_id).filter(
            Q(reference=prefix) | Q(reference__startswith=f"{prefix}:")
        ).delete()
        MilkDistribution.objects.filter(pk=pk).update(is_billed=False)

    for buyer_id in buyer_ids:
        b = Buyer.objects.filter(pk=buyer_id).first()
        if b:
            rebuild_buyer_account_balance(b)

    deferred = (
        "created_at",
        "updated_at",
        "branch_responded_at",
        "source_return_responded_at",
        "in_time",
        "out_time",
    )
    for pk in pks:
        dist = (
            MilkDistribution.objects.select_related("buyer")
            .defer(*deferred)
            .filter(pk=pk)
            .first()
        )
        if not dist or not dist.buyer_id:
            continue
        dist.is_billed = False
        sync_dispatch_billing(dist)

    repaired = 0
    for buyer_id in buyer_ids:
        b = Buyer.objects.filter(pk=buyer_id).first()
        if b:
            rebuild_buyer_account_balance(b)
            repaired += 1
    return repaired


def billed_dispatches_qs(buyer):
    return (
        MilkDistribution.objects.filter(buyer=buyer, is_billed=True)
        .exclude(status=MilkDistribution.DistributionStatus.CANCELLED)
        .filter(total_amount__gt=0)
        .select_related("branch", "buyer")
        .order_by("date", "id")
    )


def outstanding_dispatches_qs(buyer):
    return billed_dispatches_qs(buyer).filter(total_amount__gt=F("paid_amount"))


@transaction.atomic
def record_buyer_payment(
    *,
    buyer,
    amount,
    date=None,
    reference="",
    note="",
    created_by=None,
    distribution_ids=None,
    allocation_amounts=None,
):
    """
    Record a payment received from a buyer and allocate to dispatches.

    - ``distribution_ids``: selected dispatches (oldest-first waterfill among them).
      If omitted, waterfills across all outstanding dispatches.
    - ``allocation_amounts``: optional ``{distribution_id: amount}`` for explicit
      per-dispatch partial amounts. Sum must equal ``amount``.
    """
    pay_amount = _positive(amount)
    pay_date = date or timezone.localdate()
    allocation_amounts = allocation_amounts or {}

    if allocation_amounts:
        explicit = {}
        total_explicit = Decimal("0.00")
        for raw_id, raw_amt in allocation_amounts.items():
            dist_id = int(raw_id)
            amt = _money(raw_amt)
            if amt <= 0:
                continue
            explicit[dist_id] = amt
            total_explicit += amt
        if total_explicit != pay_amount:
            raise ValidationError(
                {"amount": "Payment amount must equal the sum of dispatch allocations."}
            )
        selected_ids = list(explicit.keys())
        qs = (
            MilkDistribution.objects.select_for_update()
            .filter(buyer=buyer, pk__in=selected_ids)
            .order_by("date", "id")
        )
        dists = list(qs)
        if len(dists) != len(selected_ids):
            raise ValidationError("One or more selected dispatches were not found for this buyer.")
        plan = []
        for dist in dists:
            outstanding = dist.outstanding_amount
            alloc = explicit.get(dist.pk, Decimal("0.00"))
            if alloc > outstanding:
                raise ValidationError(
                    {
                        "allocations": (
                            f"Allocation for {dist.dispatch_no} exceeds outstanding "
                            f"{outstanding}."
                        )
                    }
                )
            if alloc > 0:
                plan.append((dist, alloc))
    else:
        qs = outstanding_dispatches_qs(buyer).select_for_update()
        if distribution_ids is not None:
            ids = [int(x) for x in distribution_ids]
            if not ids:
                raise ValidationError({"distributions": "Select at least one dispatch."})
            qs = qs.filter(pk__in=ids)
            dists = list(qs.order_by("date", "id"))
            if len(dists) != len(set(ids)):
                raise ValidationError("One or more selected dispatches have no outstanding balance.")
        else:
            dists = list(qs)
        if not dists:
            raise ValidationError("No outstanding dispatches to allocate this payment.")

        remaining = pay_amount
        plan = []
        for dist in dists:
            if remaining <= 0:
                break
            take = min(dist.outstanding_amount, remaining)
            if take > 0:
                plan.append((dist, take))
                remaining -= take
        if remaining > 0:
            raise ValidationError(
                {
                    "amount": (
                        f"Payment exceeds selected outstanding by {remaining}. "
                        "Lower the amount or select more dispatches."
                    )
                }
            )

    payment = BuyerPayment.objects.create(
        buyer=buyer,
        date=pay_date,
        amount=pay_amount,
        reference=reference or "",
        note=note or "",
        created_by=created_by if getattr(created_by, "is_authenticated", False) else None,
    )
    for dist, alloc in plan:
        BuyerPaymentAllocation.objects.create(
            payment=payment, distribution=dist, amount=alloc
        )
        dist.paid_amount = _money(dist.paid_amount) + alloc
        dist.refresh_payment_status(save=False)
        dist.save(
            update_fields=["paid_amount", "payment_status", "updated_at"],
            skip_billing_sync=True,
        )
        # Keep the bill on the ledger even if this dispatch was not flagged billed yet.
        sync_dispatch_billing(dist)

    tx = _post_ledger(
        buyer=buyer,
        transaction_type=BuyerTransaction.TransactionType.DEBIT,
        amount=pay_amount,
        reference=reference or f"PAYMENT:{payment.pk}",
        description=note or f"Payment received ({pay_date})",
    )
    if tx:
        payment.ledger_transaction = tx
        payment.save(update_fields=["ledger_transaction"])
    return payment


def buyer_account_summary(buyer):
    account, _ = BuyerAccount.objects.get_or_create(
        buyer=buyer, defaults={"balance": Decimal("0.00")}
    )
    outstanding = (
        outstanding_dispatches_qs(buyer).aggregate(
            total=Sum(F("total_amount") - F("paid_amount"))
        )["total"]
        or Decimal("0.00")
    )
    billed = (
        billed_dispatches_qs(buyer).aggregate(total=Sum("total_amount"))["total"]
        or Decimal("0.00")
    )
    paid = (
        billed_dispatches_qs(buyer).aggregate(total=Sum("paid_amount"))["total"]
        or Decimal("0.00")
    )
    return {
        "balance": _money(account.balance),
        "outstanding": _money(outstanding),
        "billed": _money(billed),
        "paid": _money(paid),
        "account": account,
    }
