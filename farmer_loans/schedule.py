import calendar
from datetime import date
from decimal import Decimal
from typing import Iterator, List

METHOD_SEMI_MONTHLY = "semi_monthly"
METHOD_MONTHLY = "monthly"
INSTALLMENT_METHOD_CHOICES = (
    (METHOD_SEMI_MONTHLY, "15th & last day (about 2 per month)"),
    (METHOD_MONTHLY, "Monthly instalments"),
)


def month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def add_months(d: date, months: int) -> date:
    """Advance a date by N months, clamping the day to the target month length."""
    year = d.year + (d.month - 1 + months) // 12
    month = (d.month - 1 + months) % 12 + 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def iter_standard_repayment_dates(after: date) -> Iterator[date]:
    """Yield 15th and month-end dates strictly after the given date."""
    year, month = after.year, after.month
    while True:
        candidates = sorted({date(year, month, 15), month_end(year, month)})
        for candidate in candidates:
            if candidate > after:
                yield candidate
        if month == 12:
            year += 1
            month = 1
        else:
            month += 1


def build_repayment_due_dates(
    first_repayment_date: date,
    installment_count: int,
    method: str = METHOD_SEMI_MONTHLY,
) -> List[date]:
    if installment_count < 1:
        return []
    due_dates = [first_repayment_date]
    if installment_count == 1:
        return due_dates

    if method == METHOD_MONTHLY:
        for i in range(1, installment_count):
            due_dates.append(add_months(first_repayment_date, i))
        return due_dates

    generator = iter_standard_repayment_dates(first_repayment_date)
    for _ in range(installment_count - 1):
        due_dates.append(next(generator))
    return due_dates


def default_first_repayment_date(from_date: date, method: str = METHOD_SEMI_MONTHLY) -> date:
    if method == METHOD_MONTHLY:
        return add_months(from_date, 1)
    if from_date.day <= 15:
        return from_date.replace(day=15)
    return month_end(from_date.year, from_date.month)


def installment_method_help_text(method: str = METHOD_SEMI_MONTHLY) -> str:
    if method == METHOD_MONTHLY:
        return (
            "Instalments fall on the same day each month after the first repayment date "
            "(or the last day of shorter months)."
        )
    return (
        "Instalments follow the 15th and last day of each month after the first repayment date "
        "(about 2 per month)."
    )


def installment_count_from_requested_amount(loan_amount: Decimal, requested_installment: Decimal) -> int:
    """Instalments at `requested_installment` each, plus one final instalment for any remainder."""
    if loan_amount <= 0 or requested_installment <= 0:
        return 1
    full_payments = loan_amount // requested_installment
    remainder = loan_amount - full_payments * requested_installment
    if remainder == 0:
        return max(1, int(full_payments))
    return max(1, int(full_payments) + 1)


def requested_installment_breakdown(loan_amount: Decimal, requested_installment: Decimal):
    """Return (count, regular_amount, last_amount)."""
    count = installment_count_from_requested_amount(loan_amount, requested_installment)
    req = requested_installment.quantize(Decimal("0.01"))
    remainder = (loan_amount - (req * (count - 1))).quantize(Decimal("0.01"))
    if count == 1:
        return count, loan_amount.quantize(Decimal("0.01")), loan_amount.quantize(Decimal("0.01"))
    return count, req, remainder
