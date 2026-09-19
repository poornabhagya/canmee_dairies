from django.conf import settings
from django.core.management.base import BaseCommand

from canmee_dairies.telegram import send_telegram_message, telegram_is_configured


class Command(BaseCommand):
    help = "Send a test Telegram notification using current settings."

    def handle(self, *args, **options):
        token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or ""
        chat_id = getattr(settings, "TELEGRAM_NOTIFICATION_GROUP_ID", "") or ""
        ssl_verify = getattr(settings, "TELEGRAM_SSL_VERIFY", True)

        self.stdout.write(f"Configured: {telegram_is_configured()}")
        self.stdout.write(f"Token: {'set (' + str(len(token)) + ' chars)' if token else 'MISSING'}")
        self.stdout.write(f"Chat ID: {chat_id or 'MISSING'}")
        self.stdout.write(f"SSL verify: {ssl_verify}")

        if not telegram_is_configured():
            self.stderr.write(
                self.style.ERROR(
                    "Telegram is not configured. Set TELEGRAM_BOT_TOKEN and "
                    "TELEGRAM_NOTIFICATION_GROUP_ID in the server environment or "
                    "in telegram.env at the project root."
                )
            )
            return

        ok = send_telegram_message(
            "<b>Canmee Dairies</b>\nTest notification from <code>test_telegram</code>."
        )
        if ok:
            self.stdout.write(self.style.SUCCESS("Test message sent successfully."))
        else:
            self.stderr.write(
                self.style.ERROR(
                    "Send failed. Check application logs for Telegram HTTP/SSL errors."
                )
            )
