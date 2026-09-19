from decimal import Decimal

from django.db.models import Sum

from dispatch.models import MilkDistribution
from milk_collections.models import MilkCollection
from suppliers.models import RawMilkSupplierCollection

from .models import Branch, BranchMilkStock, BranchMilkStockAdjustment


def _sum_or_zero(qs, field):
    return qs.aggregate(total=Sum(field)).get("total") or Decimal("0")


def _quantize_kg(value):
    return (value or Decimal("0")).quantize(Decimal("0.01"))


def _quantize_liters(value):
    return (value or Decimal("0")).quantize(Decimal("0.01"))


def branch_stock_adjustment_totals(branch):
    adjustment_qs = BranchMilkStockAdjustment.objects.filter(branch=branch)
    shortage_kg = _sum_or_zero(
        adjustment_qs.filter(adjustment_type=BranchMilkStockAdjustment.AdjustmentType.SHORTAGE),
        "kg",
    )
    excess_kg = _sum_or_zero(
        adjustment_qs.filter(adjustment_type=BranchMilkStockAdjustment.AdjustmentType.EXCESS),
        "kg",
    )
    shortage_liters = _sum_or_zero(
        adjustment_qs.filter(adjustment_type=BranchMilkStockAdjustment.AdjustmentType.SHORTAGE),
        "liters",
    )
    excess_liters = _sum_or_zero(
        adjustment_qs.filter(adjustment_type=BranchMilkStockAdjustment.AdjustmentType.EXCESS),
        "liters",
    )
    net_kg = excess_kg - shortage_kg
    net_liters = excess_liters - shortage_liters
    return {
        "shortage_kg": _quantize_kg(shortage_kg),
        "excess_kg": _quantize_kg(excess_kg),
        "net_adjustment_kg": _quantize_kg(net_kg),
        "shortage_liters": _quantize_liters(shortage_liters),
        "excess_liters": _quantize_liters(excess_liters),
        "net_adjustment_liters": _quantize_liters(net_liters),
    }


def branch_stock_snapshot(branch):
    """Live branch stock figures (kg) from opening, collections, dispatch, returns, transfers."""
    stock = BranchMilkStock.objects.filter(branch=branch).only("opening_kg").first()
    opening = stock.opening_kg if stock else Decimal("0")
    direct_collected = _sum_or_zero(
        MilkCollection.objects.route_collections().filter(branch=branch),
        "kg",
    )
    raw_supplier_collected = _sum_or_zero(
        RawMilkSupplierCollection.objects.filter(branch=branch).exclude(
            status=RawMilkSupplierCollection.CollectionStatus.CANCELLED
        ),
        "kg",
    )
    collected = direct_collected + raw_supplier_collected
    dispatched = _sum_or_zero(
        MilkDistribution.objects.filter(branch=branch).exclude(
            status=MilkDistribution.DistributionStatus.CANCELLED
        ),
        "kg",
    )
    returns_received = _sum_or_zero(
        MilkDistribution.objects.filter(
            returned_branch=branch,
            buyer_result_quantity__gt=0,
        ).exclude(status=MilkDistribution.DistributionStatus.CANCELLED),
        "buyer_result_quantity",
    )
    branch_transfers_in = _sum_or_zero(
        MilkDistribution.objects.filter(
            destination_branch=branch,
            branch_destination_response=MilkDistribution.BranchDestinationResponse.COLLECTED,
        ).exclude(status=MilkDistribution.DistributionStatus.CANCELLED),
        "kg",
    )
    adjustments = branch_stock_adjustment_totals(branch)
    net_adjustment_kg = adjustments["net_adjustment_kg"]
    current = opening + collected - dispatched + returns_received + branch_transfers_in + net_adjustment_kg
    return {
        "opening_kg": _quantize_kg(opening),
        "collected_kg": _quantize_kg(collected),
        "dispatched_kg": _quantize_kg(dispatched),
        "returns_received_kg": _quantize_kg(returns_received),
        "branch_transfers_in_kg": _quantize_kg(branch_transfers_in),
        "shortage_kg": adjustments["shortage_kg"],
        "excess_kg": adjustments["excess_kg"],
        "net_adjustment_kg": net_adjustment_kg,
        "net_adjustment_liters": adjustments["net_adjustment_liters"],
        "current_kg": _quantize_kg(current),
    }


def sum_stock_snapshots(branches):
    totals = {
        "opening_kg": Decimal("0.00"),
        "collected_kg": Decimal("0.00"),
        "dispatched_kg": Decimal("0.00"),
        "net_adjustment_kg": Decimal("0.00"),
        "current_kg": Decimal("0.00"),
    }
    for branch in branches:
        snap = branch_stock_snapshot(branch)
        for key in totals:
            totals[key] += snap[key]
    for key in totals:
        totals[key] = _quantize_kg(totals[key])
    return totals


def recalculate_branch_stock(branch):
    snap = branch_stock_snapshot(branch)
    BranchMilkStock.objects.update_or_create(
        branch=branch,
        defaults={
            "opening_kg": snap["opening_kg"],
            "collected_kg": snap["collected_kg"],
            "dispatched_kg": snap["dispatched_kg"],
            "current_kg": snap["current_kg"],
        },
    )


def recalculate_all_branch_stocks():
    for branch in Branch.objects.all():
        recalculate_branch_stock(branch)
