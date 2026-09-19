from decimal import Decimal

from collection_point_loans.models import (
    CollectionPointLoanPayment,
    CollectionPointLoanPaymentAllocation,
)


def record_collection_point_loan_payment(
    *,
    loan,
    allocations,
    payment_date,
    method,
    note="",
    created_by=None,
    source_period_payment=None,
):
    """Create one payment transaction and its instalment allocations."""
    normalized = []
    total = Decimal("0.00")
    for schedule, amount in allocations:
        value = Decimal(amount or 0).quantize(Decimal("0.01"))
        if value == Decimal("0.00"):
            continue
        normalized.append((schedule, value))
        total += value
    total = total.quantize(Decimal("0.01"))
    if not normalized or total == Decimal("0.00"):
        return None

    payment = CollectionPointLoanPayment.objects.create(
        loan=loan,
        payment_date=payment_date,
        method=method,
        amount=total,
        note=(note or "")[:255],
        created_by=created_by,
        source_period_payment=source_period_payment,
    )
    CollectionPointLoanPaymentAllocation.objects.bulk_create(
        [
            CollectionPointLoanPaymentAllocation(
                payment=payment,
                schedule=schedule,
                amount=value,
            )
            for schedule, value in normalized
        ]
    )
    return payment
