import secrets
import string

from django.core.exceptions import PermissionDenied

from .models import Route

ROUTE_DRIVER_SESSION_KEY = "route_driver_route_id"
_ACCESS_ALPHABET = string.ascii_uppercase + string.digits
_DEFAULT_CODE_LENGTH = 8


def normalize_driver_access_code(value):
    return "".join(str(value or "").split()).upper()


def generate_unique_driver_access_code(*, exclude_route_id=None, length=_DEFAULT_CODE_LENGTH):
    for _ in range(200):
        code = "".join(secrets.choice(_ACCESS_ALPHABET) for _ in range(length))
        qs = Route.objects.filter(driver_access_code=code)
        if exclude_route_id:
            qs = qs.exclude(pk=exclude_route_id)
        if not qs.exists():
            return code
    raise RuntimeError("Could not allocate a unique driver access code.")


def ensure_route_driver_access_code(route, *, force=False):
    if route.driver_access_code and not force:
        return route.driver_access_code
    route.driver_access_code = generate_unique_driver_access_code(exclude_route_id=route.pk)
    route.save(update_fields=["driver_access_code", "updated_at"])
    return route.driver_access_code


def authenticate_route_driver(access_code):
    normalized = normalize_driver_access_code(access_code)
    if not normalized:
        return None
    return (
        Route.objects.select_related("branch")
        .filter(driver_access_code=normalized, is_deleted=False)
        .first()
    )


def get_route_driver_session_route(request):
    route_id = request.session.get(ROUTE_DRIVER_SESSION_KEY)
    if not route_id:
        return None
    return (
        Route.objects.select_related("branch")
        .filter(pk=route_id, is_deleted=False)
        .first()
    )


def login_route_driver(request, route):
    request.session[ROUTE_DRIVER_SESSION_KEY] = route.pk
    request.session.modified = True


def logout_route_driver(request):
    request.session.pop(ROUTE_DRIVER_SESSION_KEY, None)
    request.session.modified = True


class RouteDriverRequiredMixin:
    permission_flag = None

    def dispatch(self, request, *args, **kwargs):
        from django.middleware.csrf import get_token

        route = get_route_driver_session_route(request)
        if not route:
            from django.shortcuts import redirect
            from django.urls import reverse

            return redirect(reverse("route-driver-login"))
        if self.permission_flag and not getattr(route, self.permission_flag, False):
            raise PermissionDenied("This report is not enabled for your route.")
        self.route_driver = route
        get_token(request)  # ensure CSRF cookie for AJAX accept/reject
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        route = getattr(self, "route_driver", None)
        if route is None:
            return ctx
        from suppliers.services import (
            farmer_goods_accept_notifications_enabled,
            pending_collection_point_batches_for_route,
        )

        if farmer_goods_accept_notifications_enabled():
            pending_count = len(pending_collection_point_batches_for_route(route))
        else:
            pending_count = 0
        ctx.setdefault("pending_goods_count", pending_count)
        ctx.setdefault("pending_goods_items", ctx.get("pending_goods_items") or [])
        return ctx
