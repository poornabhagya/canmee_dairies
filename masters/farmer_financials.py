from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.db.models import Count, DecimalField, F, Max, Prefetch, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.dateparse import parse_date

from farmer_loans.models import FarmerLoan, FarmerLoanRepaymentSchedule
from masters.models import FarmerAdvancePayment
from milk_collections.models import CollectionSource, MilkCollection
from reports.models import FarmerPeriodPayment
from suppliers.models import FarmerGoodsIssue
from suppliers.services import (
    goods_issue_batch_settlement_summary,
    priced_farmer_goods_issue_lines,
    summarize_priced_farmer_goods_issues,
)
from reports.farmer_goods_summary import _product_rows_for_issues


def parse_statement_period(request):
    """Return (start, end, full_history) from GET params."""
    if request.GET.get("full") == "1":
        return None, None, True
    from_raw = (request.GET.get("from") or "").strip()
    to_raw = (request.GET.get("to") or "").strip()
    if not from_raw or not to_raw:
        return None, None, False
    start = parse_date(from_raw)
    end = parse_date(to_raw)
    if not start or not end:
        return None, None, False
    if start > end:
        start, end = end, start
    return start, end, False


def _goods_issue_datetime_range(start, end):
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)
    return start_dt, end_dt


def _farmer_goods_receipt_rows(goods_issues):
    by_ref = defaultdict(list)
    for issue in goods_issues:
        by_ref[issue.batch_ref or ""].append(issue)

    receipts = []
    for batch_ref, batch_issues in by_ref.items():
        if not batch_issues:
            continue
        priced_items, total, *_ = summarize_priced_farmer_goods_issues(batch_issues)
        settlement = goods_issue_batch_settlement_summary(batch_issues)
        receipts.append(
            {
                "batch_ref": batch_ref,
                "date": batch_issues[0].date,
                "line_count": len(batch_issues),
                "amount": total,
                "items": priced_items,
                **settlement,
            }
        )
    receipts.sort(key=lambda row: row["date"], reverse=True)
    return receipts


def build_farmer_financial_context(farmer, start=None, end=None):
    advance_qs = FarmerAdvancePayment.objects.filter(farmer=farmer)
    if start and end:
        advance_qs = advance_qs.filter(date__gte=start, date__lte=end)
    advances = list(advance_qs.select_related("created_by").order_by("-date", "-id"))

    goods_qs = FarmerGoodsIssue.objects.filter(
        issue_to_type=FarmerGoodsIssue.IssueToType.FARMER,
        issue_to_id=farmer.id,
    ).exclude(driver_status=FarmerGoodsIssue.DriverStatus.REJECTED)
    if start and end:
        start_dt, end_dt = _goods_issue_datetime_range(start, end)
        goods_qs = goods_qs.filter(date__gte=start_dt, date__lt=end_dt)
    goods_issues = list(goods_qs.select_related("product", "from_branch").order_by("-date", "-id"))
    goods_receipts = _farmer_goods_receipt_rows(goods_issues)
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
    receipt_line_count = sum((receipt.get("line_count") or 0) for receipt in goods_receipts)
    summary_qty = sum((row["quantity"] for row in summary_rows), Decimal("0")).quantize(Decimal("0.01"))

    loans = list(
        FarmerLoan.objects.filter(farmer=farmer)
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
        loan__farmer=farmer,
        loan__status=FarmerLoan.Status.APPROVED,
    )
    if start and end:
        instalment_qs = instalment_qs.filter(due_date__gte=start, due_date__lte=end)
    loan_instalments = list(
        instalment_qs.select_related("loan").order_by("-due_date", "-installment_number", "-id")
    )

    payment_qs = FarmerPeriodPayment.objects.filter(farmer=farmer)
    if start and end:
        payment_qs = payment_qs.filter(period_start__lte=end, period_end__gte=start)
    period_payments = list(payment_qs.select_related("recorded_by").order_by("-paid_at", "-id"))
    loans_kpi_balance = sum(
        (loan.balance_total for loan in loans if loan.balance_total is not None),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    period_payments_total = sum(
        (payment.amount for payment in period_payments if payment.amount is not None),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))

    return {
        "farmer_advances": advances,
        "farmer_goods_receipts": goods_receipts,
        "farmer_goods_receipt_line_count": receipt_line_count,
        "farmer_goods_issued_products": issued_products,
        "farmer_goods_issued_total": issued_total,
        "farmer_goods_summary_rows": summary_rows,
        "farmer_goods_summary_total": summary_total,
        "farmer_goods_summary_qty": summary_qty,
        "farmer_goods_summary_receipt_count": receipt_count,
        "farmer_loans": loans,
        "farmer_loan_instalments": loan_instalments,
        "farmer_loans_kpi_balance": loans_kpi_balance,
        "farmer_period_payments": period_payments,
        "farmer_period_payments_total": period_payments_total,
    }


def bulk_farmer_avg_daily_milk_kg(farmer_ids):
    """Return {farmer_id: avg_kg_per_collection_day} from farmer milk collection rows."""
    if not farmer_ids:
        return {}
    rows = (
        MilkCollection.objects.filter(
            source=CollectionSource.FARMER,
            farmer_id__in=farmer_ids,
            is_deleted=False,
        )
        .values("farmer_id")
        .annotate(
            total_kg=Coalesce(Sum("kg"), Value(0), output_field=DecimalField()),
            day_count=Count("date", distinct=True),
        )
    )
    result = {}
    for row in rows:
        days = row["day_count"] or 0
        if days:
            result[row["farmer_id"]] = (row["total_kg"] / days).quantize(Decimal("0.01"))
    return result


def build_farmer_milk_collection_context(farmer, start=None, end=None):
    qs = MilkCollection.objects.filter(farmer=farmer, source=CollectionSource.FARMER)
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
        "farmer_collection": {
            "total_entries": summary["total_entries"] or 0,
            "total_kg": summary["total_kg"] or 0,
            "total_liters": summary["total_liters"] or 0,
            "latest_date": summary["latest_date"],
            "paid_entries": summary["paid_entries"] or 0,
            "unpaid_entries": summary["unpaid_entries"] or 0,
        },
        "farmer_milk_collections": qs.select_related("route", "branch", "farmer").order_by("-date"),
    }


def build_farmer_statement_context(farmer, start=None, end=None, full=False):
    period_start = start
    period_end = end
    if full:
        period_start = None
        period_end = None
    ctx = {
        "farmer": farmer,
        "statement_full": full,
        "statement_start": period_start,
        "statement_end": period_end,
    }
    ctx.update(build_farmer_milk_collection_context(farmer, period_start, period_end))
    ctx.update(build_farmer_financial_context(farmer, period_start, period_end))
    return ctx
