from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Max, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce

from collection_point_loans.models import CollectionPointLoan, CollectionPointLoanRepaymentSchedule
from farmer_loans.models import FarmerLoan, FarmerLoanRepaymentSchedule
from masters.models import CollectionPointAdvancePayment, Farmer, FarmerAdvancePayment
from milk_collections.models import CollectionSource, MilkCollection
from reports.models import CollectionPointPeriodPayment, CollectionPointPreviousOutstanding
from suppliers.models import FarmerGoodsIssue
from suppliers.services import priced_farmer_goods_issue_lines
from reports.farmer_goods_summary import _product_rows_for_issues

from .farmer_financials import (
    _farmer_goods_receipt_rows,
    _goods_issue_datetime_range,
    parse_statement_period,
)

__all__ = [
    "parse_statement_period",
    "build_collection_point_financial_context",
    "build_collection_point_statement_context",
    "build_point_milk_collection_context",
    "build_point_additional_farmer_totals",
]


def _point_goods_issue_ids(point):
    id_candidates = {point.pk}
    try:
        id_candidates.add(int(str(point.number).strip()))
    except (ValueError, TypeError):
        pass
    return id_candidates


def build_collection_point_financial_context(point, start=None, end=None):
    farmer_ids = list(
        Farmer.objects.filter(collection_point=point).values_list("id", flat=True)
    )

    goods_qs = FarmerGoodsIssue.objects.filter(
        issue_to_type=FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
        issue_to_id__in=_point_goods_issue_ids(point),
    ).exclude(driver_status=FarmerGoodsIssue.DriverStatus.REJECTED)
    if start and end:
        start_dt, end_dt = _goods_issue_datetime_range(start, end)
        goods_qs = goods_qs.filter(date__gte=start_dt, date__lt=end_dt)
    goods_issues = list(
        goods_qs.select_related("product", "from_branch").order_by("-date", "-id")
    )
    point_goods_receipts = _farmer_goods_receipt_rows(goods_issues)
    priced_lines, issued_total = priced_farmer_goods_issue_lines(goods_issues)
    issued_products = []
    for row in priced_lines:
        issue = row["issue"]
        issued_products.append(
            {
                "issue_id": issue.id,
                "batch_ref": issue.batch_ref or "",
                "date": issue.date,
                "product_name": issue.product.name if issue.product_id else "—",
                "unit": (issue.product.unit if issue.product_id else "") or "",
                "quantity": row["quantity"],
                "unit_price": row["unit_price"],
                "line_amount": row["line_amount"],
                "is_settled": issue.is_settled,
            }
        )
    summary_rows, summary_total = _product_rows_for_issues(goods_issues)
    receipt_count = len({issue.batch_ref for issue in goods_issues if issue.batch_ref})
    summary_qty = sum((row["quantity"] for row in summary_rows), Decimal("0")).quantize(
        Decimal("0.01")
    )

    advance_qs = FarmerAdvancePayment.objects.filter(farmer_id__in=farmer_ids)
    if start and end:
        advance_qs = advance_qs.filter(date__gte=start, date__lte=end)
    linked_advances = list(
        advance_qs.select_related("farmer", "created_by").order_by("-date", "-id")
    )

    point_advance_qs = CollectionPointAdvancePayment.objects.filter(collection_point=point)
    if start and end:
        point_advance_qs = point_advance_qs.filter(date__gte=start, date__lte=end)
    point_advances = list(
        point_advance_qs.select_related("created_by").order_by("-date", "-id")
    )

    linked_loans = list(
        FarmerLoan.objects.filter(farmer_id__in=farmer_ids)
        .select_related("farmer")
        .annotate(
            paid_total=Coalesce(
                Sum("repayment_schedules__paid_amount"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )
        .annotate(balance_total=F("loan_amount") - F("paid_total"))
        .prefetch_related(
            Prefetch(
                "repayment_schedules",
                queryset=FarmerLoanRepaymentSchedule.objects.order_by("installment_number"),
            )
        )
        .order_by("-loan_date", "-id")
    )

    instalment_qs = FarmerLoanRepaymentSchedule.objects.filter(
        loan__farmer_id__in=farmer_ids,
        loan__status=FarmerLoan.Status.APPROVED,
    )
    if start and end:
        instalment_qs = instalment_qs.filter(due_date__gte=start, due_date__lte=end)
    linked_loan_instalments = list(
        instalment_qs.select_related("loan", "loan__farmer").order_by(
            "-due_date", "-installment_number", "-id"
        )
    )

    money_field = DecimalField(max_digits=14, decimal_places=2)
    cp_loan_qs = CollectionPointLoan.objects.filter(collection_point=point)
    if start and end:
        cp_loan_qs = cp_loan_qs.filter(loan_date__gte=start, loan_date__lte=end)
    point_cp_loans = list(
        cp_loan_qs.annotate(
            paid_total=Coalesce(
                Sum("repayment_schedules__paid_amount"),
                Value(Decimal("0.00")),
                output_field=money_field,
            )
        )
        .annotate(balance_total=F("loan_amount") - F("paid_total"))
        .prefetch_related(
            Prefetch(
                "repayment_schedules",
                queryset=CollectionPointLoanRepaymentSchedule.objects.order_by(
                    "installment_number"
                ),
            )
        )
        .order_by("-loan_date", "-id")
    )
    cp_instalment_qs = CollectionPointLoanRepaymentSchedule.objects.filter(
        loan__collection_point=point,
        loan__status=CollectionPointLoan.Status.APPROVED,
    )
    if start and end:
        cp_instalment_qs = cp_instalment_qs.filter(due_date__gte=start, due_date__lte=end)
    point_cp_loan_instalments = list(
        cp_instalment_qs.select_related("loan").order_by(
            "-due_date", "-installment_number", "-id"
        )
    )
    point_cp_loans_balance = sum(
        (getattr(loan, "balance_total", None) or Decimal("0.00") for loan in point_cp_loans),
        Decimal("0.00"),
    )

    payment_qs = CollectionPointPeriodPayment.objects.filter(collection_point=point)
    if start and end:
        payment_qs = payment_qs.filter(period_start__lte=end, period_end__gte=start)
    period_payments = list(
        payment_qs.select_related("recorded_by").order_by("-paid_at", "-id")
    )

    outstanding_qs = CollectionPointPreviousOutstanding.objects.filter(
        collection_point=point
    )
    if start and end:
        outstanding_qs = outstanding_qs.filter(
            Q(available_on__gte=start, available_on__lte=end)
            | Q(
                originated_period_start__lte=end,
                originated_period_end__gte=start,
            )
            | Q(
                recovered_period_start__isnull=False,
                recovered_period_start__lte=end,
                recovered_period_end__gte=start,
            )
        )
    point_previous_outstanding = list(
        outstanding_qs.select_related("created_by").order_by("-available_on", "-id")
    )
    point_previous_outstanding_open = (
        CollectionPointPreviousOutstanding.objects.filter(
            collection_point=point,
            is_recovered=False,
        ).aggregate(
            total=Coalesce(
                Sum("amount"),
                Value(Decimal("0.00")),
                output_field=DecimalField(max_digits=14, decimal_places=2),
            )
        )["total"]
        or Decimal("0.00")
    )

    return {
        "point_goods_receipts": point_goods_receipts,
        "point_goods_issued_products": issued_products,
        "point_goods_issued_total": issued_total,
        "point_goods_summary_rows": summary_rows,
        "point_goods_summary_total": summary_total,
        "point_goods_summary_qty": summary_qty,
        "point_goods_summary_receipt_count": receipt_count,
        "point_goods_receipt_line_count": sum(
            (row.get("line_count") or 0) for row in point_goods_receipts
        ),
        "point_linked_advances": linked_advances,
        "point_advances": point_advances,
        "point_linked_loans": linked_loans,
        "point_linked_loans_balance": sum(
            (getattr(loan, "balance_total", None) or Decimal("0.00") for loan in linked_loans),
            Decimal("0.00"),
        ),
        "point_linked_loan_instalments": linked_loan_instalments,
        "point_cp_loans": point_cp_loans,
        "point_cp_loans_balance": point_cp_loans_balance,
        "point_loans_kpi_balance": (
            sum(
                (getattr(loan, "balance_total", None) or Decimal("0.00") for loan in linked_loans),
                Decimal("0.00"),
            )
            + point_cp_loans_balance
        ),
        "point_cp_loan_instalments": point_cp_loan_instalments,
        "point_period_payments": period_payments,
        "point_period_payments_total": sum(
            (payment.amount or Decimal("0.00") for payment in period_payments),
            Decimal("0.00"),
        ),
        "point_previous_outstanding": point_previous_outstanding,
        "point_previous_outstanding_open": point_previous_outstanding_open,
    }


def build_collection_point_statement_context(point, start=None, end=None, full=False):
    period_start = start
    period_end = end
    if full:
        period_start = None
        period_end = None
    ctx = {
        "point": point,
        "statement_full": full,
        "statement_start": period_start,
        "statement_end": period_end,
    }
    ctx.update(build_point_milk_collection_context(point, period_start, period_end))
    ctx.update(build_collection_point_financial_context(point, period_start, period_end))
    return ctx


def build_point_milk_collection_context(point, start=None, end=None):
    qs = MilkCollection.objects.filter(
        collection_point=point,
        source=CollectionSource.POINT,
        is_deleted=False,
    )
    if start and end:
        qs = qs.filter(date__gte=start, date__lte=end)
    summary = qs.aggregate(
        total_entries=Count("id"),
        total_kg=Sum("kg"),
        total_liters=Sum("liters"),
        latest_date=Max("date"),
        paid_entries=Count("id", filter=Q(is_paid=True)),
        unpaid_entries=Count("id", filter=Q(is_paid=False)),
    )
    return {
        "point_collection": {
            "total_entries": summary["total_entries"] or 0,
            "total_kg": summary["total_kg"] or 0,
            "total_liters": summary["total_liters"] or 0,
            "latest_date": summary["latest_date"],
            "paid_entries": summary["paid_entries"] or 0,
            "unpaid_entries": summary["unpaid_entries"] or 0,
        },
        "point_milk_collections": qs.select_related(
            "route", "branch", "collection_point"
        ).order_by("-date"),
    }


def build_point_additional_farmer_totals(point, farmer_ids=None, start=None, end=None):
    """Milk totals for Additional basis farmers (empty selection = all at point)."""
    all_ids = list(
        Farmer.objects.filter(collection_point=point).values_list("id", flat=True)
    )
    stored = list(point.additional_farmers.values_list("id", flat=True))
    if farmer_ids is None:
        farmer_ids = stored or all_ids
        uses_all_farmers = not stored
    else:
        allowed = set(all_ids)
        farmer_ids = [int(fid) for fid in farmer_ids if str(fid).isdigit() and int(fid) in allowed]
        uses_all_farmers = (not farmer_ids) or (set(farmer_ids) == allowed)
        if not farmer_ids:
            farmer_ids = all_ids

    qs = MilkCollection.objects.filter(
        source=CollectionSource.FARMER,
        farmer_id__in=farmer_ids,
    )
    if start and end:
        qs = qs.filter(date__gte=start, date__lte=end)
    summary = qs.aggregate(
        total_entries=Count("id"),
        total_kg=Sum("kg"),
        total_liters=Sum("liters"),
        latest_date=Max("date"),
    )
    rate = (point.additional or Decimal("0")).quantize(Decimal("0.01"))
    fee_rate = (point.collector_fee or Decimal("0")).quantize(Decimal("0.01"))
    total_kg = (summary["total_kg"] or Decimal("0")).quantize(Decimal("0.01"))
    return {
        "farmer_ids": farmer_ids,
        "uses_all_farmers": uses_all_farmers,
        "selected_count": len(farmer_ids),
        "all_count": len(all_ids),
        "total_entries": summary["total_entries"] or 0,
        "total_kg": total_kg,
        "total_liters": (summary["total_liters"] or Decimal("0")).quantize(Decimal("0.01")),
        "latest_date": summary["latest_date"],
        "additional_rate": rate,
        "additional_amount": (total_kg * rate).quantize(Decimal("0.01")),
        "collector_fee_rate": fee_rate,
        "collector_fee_amount": (total_kg * fee_rate).quantize(Decimal("0.01")),
    }
