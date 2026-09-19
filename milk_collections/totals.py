from decimal import Decimal

from django.db.models import Sum


def kg_liters_totals(queryset):
    totals = queryset.aggregate(total_kg=Sum("kg"), total_liters=Sum("liters"))
    return {
        "total_kg": totals["total_kg"] or Decimal("0.00"),
        "total_liters": totals["total_liters"] or Decimal("0.00"),
        "count": queryset.count(),
    }
