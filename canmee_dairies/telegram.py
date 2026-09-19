import html
import json
import logging
import ssl
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)


def telegram_is_configured():
    return bool(getattr(settings, "TELEGRAM_BOT_TOKEN", "")) and bool(
        getattr(settings, "TELEGRAM_NOTIFICATION_GROUP_ID", "")
    )


def _telegram_urlopen(request, *, timeout=15):
    kwargs = {"timeout": timeout}
    if not getattr(settings, "TELEGRAM_SSL_VERIFY", True):
        kwargs["context"] = ssl._create_unverified_context()
    return urllib.request.urlopen(request, **kwargs)


def send_telegram_message(text, *, chat_id=None, parse_mode="HTML"):
    """Send a message to Telegram. Returns True on success."""
    token = getattr(settings, "TELEGRAM_BOT_TOKEN", "") or ""
    chat_id = chat_id or getattr(settings, "TELEGRAM_NOTIFICATION_GROUP_ID", "") or ""
    if not token or not chat_id:
        logger.warning(
            "Telegram not configured (token=%s, chat_id=%s); skipping notification.",
            "set" if token else "missing",
            "set" if chat_id else "missing",
        )
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with _telegram_urlopen(request, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))
        if not payload.get("ok"):
            logger.warning("Telegram API error: %s", payload)
            return False
        return True
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        logger.warning("Telegram HTTP error %s: %s", exc.code, detail)
        return False
    except Exception:
        logger.exception("Failed to send Telegram notification.")
        return False


def telegram_escape(value):
    if value is None:
        return "—"
    return html.escape(str(value).strip() or "—")
