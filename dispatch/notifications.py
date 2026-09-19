from django.conf import settings
from django.utils.formats import date_format

from canmee_dairies.formatting import format_money
from canmee_dairies.telegram import send_telegram_message, telegram_escape

from .models import MilkDistribution


def _format_time(value):
    if not value:
        return "—"
    return value.strftime("%H:%M")


def build_dispatch_created_message(dispatch):
    branch = dispatch.branch
    destination = dispatch.destination_label()
    liters = dispatch.liters_equivalent

    lines = [
        "<b>🚚 New dispatch</b>",
        "",
        f"<b>Dispatch #:</b> {telegram_escape(dispatch.dispatch_no)}",
        f"<b>Date:</b> {telegram_escape(date_format(dispatch.date, 'd-m-Y'))}",
        f"<b>From:</b> {telegram_escape(branch)}",
        f"<b>To:</b> {telegram_escape(destination)}",
        f"<b>Quantity:</b> {telegram_escape(format_money(dispatch.kg))} kg "
        f"({telegram_escape(format_money(liters))} L)",
    ]

    if dispatch.driver:
        lines.append(f"<b>Driver:</b> {telegram_escape(dispatch.driver)}")
    if dispatch.browser_number:
        lines.append(f"<b>Browser:</b> {telegram_escape(dispatch.browser_number)}")
    if dispatch.temperature is not None:
        lines.append(f"<b>Temperature:</b> {telegram_escape(dispatch.temperature)} °C")
    if dispatch.sealing_numbers:
        lines.append(f"<b>Sealing:</b> {telegram_escape(dispatch.sealing_numbers)}")

    quality_bits = []
    if dispatch.fat:
        quality_bits.append(f"Fat {dispatch.fat}")
    if dispatch.snf:
        quality_bits.append(f"SNF {dispatch.snf}")
    if dispatch.lr:
        quality_bits.append(f"LR {dispatch.lr}")
    if quality_bits:
        lines.append(f"<b>Quality:</b> {telegram_escape(', '.join(quality_bits))}")

    if dispatch.in_time or dispatch.out_time:
        lines.append(
            f"<b>In/Out:</b> {telegram_escape(_format_time(dispatch.in_time))} / "
            f"{telegram_escape(_format_time(dispatch.out_time))}"
        )
    if dispatch.remarks:
        lines.append(f"<b>Remarks:</b> {telegram_escape(dispatch.remarks)}")

    lines.append("")
    lines.append(f"<i>Status: {telegram_escape(dispatch.get_status_display())}</i>")
    return "\n".join(lines)


def notify_dispatch_created(instance):
    if not getattr(settings, "TELEGRAM_DISPATCH_NOTIFICATIONS_ENABLED", False):
        return False
    dispatch = (
        MilkDistribution.objects.select_related("branch", "buyer", "destination_branch")
        .filter(pk=instance.pk)
        .first()
    )
    if not dispatch:
        return False
    return send_telegram_message(build_dispatch_created_message(dispatch))
