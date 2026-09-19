from .audit import (
    log_http_event,
    request_already_audited,
    reset_request_audit_flag,
    should_audit_request,
)


class AuditLogMiddleware:
    """Record mutating requests (and logout) with user, time, path, and status."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        reset_request_audit_flag()
        audit = should_audit_request(request)
        pre_user = getattr(request, "user", None) if audit else None
        response = self.get_response(request)
        if not audit or request_already_audited():
            return response
        try:
            log_http_event(request, response, pre_user=pre_user)
        except Exception:
            pass
        return response
