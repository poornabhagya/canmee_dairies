"""Buyer rate lookup helpers (effective_from … next change)."""

from decimal import Decimal

from django.utils import timezone

from .models import Buyer, BuyerRateHistory


def buyer_rate_at_date(buyer, on_date=None):
    """
    Return {"rate": Decimal, "rate_unit": str} effective on ``on_date``.

    Falls back to the buyer's current rate fields when no history row exists.
    """
    if buyer is None:
        return {"rate": Decimal("0.00"), "rate_unit": Buyer.RateUnit.LITER}
    on_date = on_date or timezone.localdate()
    buyer_id = buyer.pk if hasattr(buyer, "pk") else buyer
    row = (
        BuyerRateHistory.objects.filter(buyer_id=buyer_id, effective_from__lte=on_date)
        .order_by("-effective_from", "-id")
        .first()
    )
    if row:
        return {"rate": row.rate or Decimal("0.00"), "rate_unit": row.rate_unit}
    if hasattr(buyer, "rate"):
        return {
            "rate": buyer.rate or Decimal("0.00"),
            "rate_unit": buyer.rate_unit or Buyer.RateUnit.LITER,
        }
    current = Buyer.objects.filter(pk=buyer_id).values("rate", "rate_unit").first()
    if current:
        return {
            "rate": current["rate"] or Decimal("0.00"),
            "rate_unit": current["rate_unit"] or Buyer.RateUnit.LITER,
        }
    return {"rate": Decimal("0.00"), "rate_unit": Buyer.RateUnit.LITER}


def annotate_rate_history_windows(rows):
    """
    Attach effective_to on an already-fetched newest-first history list
    without N+1 queries.
    """
    ordered = sorted(rows, key=lambda r: (r.effective_from, r.id))
    for idx, row in enumerate(ordered):
        if idx + 1 < len(ordered):
            from datetime import timedelta

            row.effective_until = ordered[idx + 1].effective_from - timedelta(days=1)
        else:
            row.effective_until = None
    for row in rows:
        if not hasattr(row, "effective_until"):
            row.effective_until = None
    return rows
