"""Raw-milk supplier rate lookup helpers (effective_from … next change)."""

from datetime import timedelta
from decimal import Decimal

from django.utils import timezone

from .models import Supplier, SupplierRateHistory


def supplier_rate_at_date(supplier, on_date=None):
    """Return {"rate": Decimal, "rate_unit": str} effective on ``on_date``."""
    if supplier is None:
        return {"rate": Decimal("0.00"), "rate_unit": Supplier.RateUnit.LITER}
    on_date = on_date or timezone.localdate()
    supplier_id = supplier.pk if hasattr(supplier, "pk") else supplier
    row = (
        SupplierRateHistory.objects.filter(supplier_id=supplier_id, effective_from__lte=on_date)
        .order_by("-effective_from", "-id")
        .first()
    )
    if row:
        return {"rate": row.rate or Decimal("0.00"), "rate_unit": row.rate_unit}
    if hasattr(supplier, "rate"):
        return {
            "rate": supplier.rate or Decimal("0.00"),
            "rate_unit": supplier.rate_unit or Supplier.RateUnit.LITER,
        }
    current = Supplier.objects.filter(pk=supplier_id).values("rate", "rate_unit").first()
    if current:
        return {
            "rate": current["rate"] or Decimal("0.00"),
            "rate_unit": current["rate_unit"] or Supplier.RateUnit.LITER,
        }
    return {"rate": Decimal("0.00"), "rate_unit": Supplier.RateUnit.LITER}


def annotate_rate_history_windows(rows):
    ordered = sorted(rows, key=lambda r: (r.effective_from, r.id))
    for idx, row in enumerate(ordered):
        if idx + 1 < len(ordered):
            row.effective_until = ordered[idx + 1].effective_from - timedelta(days=1)
        else:
            row.effective_until = None
    for row in rows:
        if not hasattr(row, "effective_until"):
            row.effective_until = None
    return rows
