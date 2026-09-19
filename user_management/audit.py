"""Central audit trail helpers. Failures never break the request."""

from __future__ import annotations

import logging
from contextvars import ContextVar

from django.utils import timezone

logger = logging.getLogger(__name__)

_request_domain_logged = ContextVar("audit_request_domain_logged", default=False)

SENSITIVE_KEYS = {
    "password",
    "password1",
    "password2",
    "old_password",
    "new_password1",
    "new_password2",
    "csrfmiddlewaretoken",
    "secret",
    "token",
    "api_key",
    "otp",
}

MODULE_FROM_PREFIX = {
    "": "App",
    "admin": "Django admin",
    "assets": "Assets",
    "attendance": "Attendance",
    "auth": "Sign in",
    "branches": "Branches",
    "collections": "Milk collections",
    "deploy": "Deploy",
    "dispatch": "Dispatch",
    "hrm": "People",
    "loans": "Farmer loans",
    "masters": "Masters",
    "point-loans": "Point loans",
    "reports": "Reports",
    "stock": "Stock",
    "suppliers": "Suppliers",
    "users": "Users",
}

SKIP_PATH_PREFIXES = (
    "/static/",
    "/media/",
    "/favicon.ico",
)

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}


def mark_request_audited():
    _request_domain_logged.set(True)


def request_already_audited():
    return bool(_request_domain_logged.get())


def reset_request_audit_flag():
    _request_domain_logged.set(False)


def _user_bits(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return None, "", ""
    username = user.get_username() or ""
    display = (user.get_full_name() or "").strip() or username
    return user, username, display


def log_audit_event(
    *,
    user=None,
    username="",
    user_display="",
    action="",
    module="",
    summary="",
    method="",
    path="",
    status_code=None,
    ip_address=None,
    user_agent="",
    object_type="",
    object_id="",
    source="app",
    details=None,
    created_at=None,
    mark_request=True,
):
    """Persist one audit row. Swallows errors so callers never fail closed."""
    try:
        from .models import AuditEvent

        resolved_user, resolved_username, resolved_display = _user_bits(user)
        username = (username or resolved_username or "")[:150]
        user_display = (user_display or resolved_display or username)[:255]
        event = AuditEvent(
            created_at=created_at or timezone.now(),
            user=resolved_user,
            username=username,
            user_display=user_display,
            action=(action or "update")[:40],
            module=(module or "")[:80],
            summary=(summary or "")[:255],
            method=(method or "")[:10],
            path=(path or "")[:500],
            status_code=status_code,
            ip_address=ip_address or None,
            user_agent=(user_agent or "")[:255],
            object_type=(object_type or "")[:80],
            object_id=str(object_id or "")[:64],
            source=(source or "app")[:40],
            details=details if isinstance(details, dict) else {},
        )
        event.save()
        if mark_request:
            mark_request_audited()
        return event
    except Exception:
        logger.exception("Failed to write audit event")
        return None


def client_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()[:45] or None
    addr = (request.META.get("REMOTE_ADDR") or "").strip()
    return addr[:45] or None


def _path_is_skipped(path):
    if not path:
        return True
    if path == "/favicon.ico":
        return True
    return any(path.startswith(prefix) for prefix in SKIP_PATH_PREFIXES)


def should_audit_request(request):
    path = request.path or ""
    if _path_is_skipped(path):
        return False
    if "audit-logs" in path:
        return False
    method = (request.method or "").upper()
    if method in _MUTATING:
        return True
    match = getattr(request, "resolver_match", None)
    url_name = (match.url_name if match else "") or ""
    if method == "GET" and (url_name == "logout" or path.rstrip("/").endswith("/auth/logout")):
        return True
    return False


def _module_from_request(request):
    path = (request.path or "").lstrip("/")
    prefix = path.split("/", 1)[0] if path else ""
    return MODULE_FROM_PREFIX.get(prefix, prefix.replace("-", " ").replace("_", " ").title() or "App")


def _action_from_request(request, response, user):
    path = (request.path or "").lower()
    method = (request.method or "").upper()
    match = getattr(request, "resolver_match", None)
    url_name = ((match.url_name if match else "") or "").lower()

    if url_name == "logout" or "logout" in path:
        return "logout"
    if url_name == "login" or path.rstrip("/").endswith("/auth/login"):
        if user is not None and getattr(user, "is_authenticated", False):
            return "login"
        return "login_failed"
    if method == "DELETE" or "delete" in url_name or "/delete" in path:
        return "delete"
    if (
        method == "POST"
        and (
            "create" in url_name
            or url_name.endswith("-new")
            or "/new/" in path
            or path.rstrip("/").endswith("/new")
        )
    ):
        return "create"
    if method in {"PUT", "PATCH", "POST"}:
        return "update"
    return method.lower() or "update"


def _summary_from_request(request, action):
    match = getattr(request, "resolver_match", None)
    if match:
        name = (match.url_name or "").replace("-", " ").replace("_", " ").strip()
        kwargs = match.kwargs or {}
        pk = kwargs.get("pk") or kwargs.get("id")
        bits = [name or action]
        if pk is not None:
            bits.append(f"#{pk}")
        return " · ".join(bit for bit in bits if bit)
    return f"{request.method} {request.path}"


def _object_from_request(request):
    match = getattr(request, "resolver_match", None)
    if not match:
        return "", ""
    kwargs = match.kwargs or {}
    pk = kwargs.get("pk") or kwargs.get("id")
    object_type = (match.url_name or "").split("-")[0] if match.url_name else ""
    return object_type[:80], str(pk) if pk is not None else ""


def _http_details(request, response):
    match = getattr(request, "resolver_match", None)
    post_fields = []
    for key in request.POST.keys():
        lowered = key.lower()
        if lowered in SENSITIVE_KEYS or "password" in lowered:
            continue
        post_fields.append(key)
        if len(post_fields) >= 40:
            break
    return {
        "url_name": (match.url_name if match else "") or "",
        "namespace": (match.namespace if match else "") or "",
        "post_fields": post_fields,
        "query": (request.META.get("QUERY_STRING") or "")[:200],
    }


def log_http_event(request, response, *, pre_user=None):
    post_user = getattr(request, "user", None)
    post_ok = post_user is not None and getattr(post_user, "is_authenticated", False)
    pre_ok = pre_user is not None and getattr(pre_user, "is_authenticated", False)
    user = post_user if post_ok else (pre_user if pre_ok else None)

    username = ""
    if user is None:
        username = (request.POST.get("username") or "")[:150]

    action = _action_from_request(request, response, user if post_ok else user)
    object_type, object_id = _object_from_request(request)
    status = getattr(response, "status_code", None)
    log_audit_event(
        user=user,
        username=username,
        action=action,
        module=_module_from_request(request),
        summary=_summary_from_request(request, action),
        method=(request.method or "")[:10],
        path=(request.path or "")[:500],
        status_code=status if isinstance(status, int) else None,
        ip_address=client_ip(request),
        user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:255],
        object_type=object_type,
        object_id=object_id,
        source="http",
        details=_http_details(request, response),
        mark_request=False,
    )
