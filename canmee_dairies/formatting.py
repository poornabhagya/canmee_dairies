from decimal import Decimal, InvalidOperation


def format_money(value, *, empty="—"):
    """Format a numeric value as money with thousand separators and 2 decimals."""
    if value is None or value == "":
        return empty
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return value
    return f"{amount:,.2f}"
