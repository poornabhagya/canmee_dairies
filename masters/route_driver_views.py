from datetime import date
from decimal import Decimal
from urllib.parse import urlencode

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.utils.timesince import timesince
from django.views import View
from django.views.generic import TemplateView

from masters.models import CollectionPoint
from suppliers.models import DriverFarmerGoodsRequest
from suppliers.services import (
    accept_farmer_goods_issue_batch,
    cancel_driver_farmer_goods_request,
    create_driver_farmer_goods_request,
    farmer_goods_accept_notifications_enabled,
    farmer_goods_branch_check_rows,
    fifo_price_map_for_issues,
    pending_collection_point_batches_for_route,
    reject_farmer_goods_issue_batch,
)

from .route_driver_access import (
    RouteDriverRequiredMixin,
    authenticate_route_driver,
    get_route_driver_session_route,
    login_route_driver,
    logout_route_driver,
)
from .route_driver_reports import (
    build_farmer_goods_context,
    build_point_summary_context,
    build_route_summary_context,
)


def _default_period():
    today = date.today()
    start = today.replace(day=1)
    return start, today


def _parse_period(request):
    from_raw = request.GET.get("from")
    to_raw = request.GET.get("to")
    if from_raw and to_raw:
        from_date = parse_date(from_raw)
        to_date = parse_date(to_raw)
        if from_date and to_date and from_date <= to_date:
            return from_date, to_date
    return _default_period()


def _parse_point_id(request, route):
    raw = (request.GET.get("point") or "").strip()
    if not raw:
        return None
    try:
        point_id = int(raw)
    except (TypeError, ValueError):
        return None
    exists = route.collection_points.filter(pk=point_id).exists()
    return point_id if exists else None


def _parse_detail_kind(request):
    kind = (request.GET.get("kind") or "all").strip().lower()
    return kind if kind in {"all", "milk", "goods"} else "all"


def _driver_dashboard_url(from_date, to_date, point_id=None, detail_kind="all"):
    params = {"from": from_date.isoformat(), "to": to_date.isoformat()}
    if point_id:
        params["point"] = str(point_id)
    if detail_kind and detail_kind != "all":
        params["kind"] = detail_kind
    return f"{reverse('route-driver-dashboard')}?{urlencode(params)}"


def _serialize_pending_goods_batch(batch):
    lines = batch["lines"]
    price_map = fifo_price_map_for_issues(lines)
    point = batch["point"]
    line_rows = []
    total = Decimal("0")
    for issue in lines:
        amount = price_map.get(issue.id, Decimal("0")) or Decimal("0")
        total += amount
        line_rows.append(
            {
                "product_name": issue.product.name if issue.product_id else "—",
                "quantity": str(issue.quantity),
                "amount": str(amount.quantize(Decimal("0.01"))),
            }
        )
    branch_name = batch["from_branch"].name if batch.get("from_branch") else "—"
    created = batch["date"]
    return {
        "batch_ref": batch["batch_ref"],
        "title": f"Goods for {point.number} — {point.name}",
        "body": (
            f"{len(lines)} line(s) from {branch_name}. "
            "Accept to apply stock & payment sheet, or reject to cancel."
        ),
        "point_label": f"{point.number} — {point.name}",
        "branch_label": branch_name,
        "line_count": len(lines),
        "total_amount": str(total.quantize(Decimal("0.01"))),
        "lines": line_rows,
        "created_ago": f"{timesince(created)} ago" if created else "",
        "accept_url": reverse(
            "route-driver-farmer-goods-accept", kwargs={"batch_ref": batch["batch_ref"]}
        ),
        "reject_url": reverse(
            "route-driver-farmer-goods-reject", kwargs={"batch_ref": batch["batch_ref"]}
        ),
    }


def _pending_goods_batches_for_driver(route):
    if not farmer_goods_accept_notifications_enabled():
        return []
    return pending_collection_point_batches_for_route(route)


def _pending_goods_feed(route):
    batches = _pending_goods_batches_for_driver(route)
    items = [_serialize_pending_goods_batch(batch) for batch in batches]
    return {"ok": True, "unread": len(items), "items": items}


class RouteDriverLoginView(View):
    template_name = "masters/route_driver_login.html"

    def get(self, request):
        if get_route_driver_session_route(request):
            return redirect("route-driver-dashboard")
        return render(request, self.template_name, {"error": None})

    def post(self, request):
        if get_route_driver_session_route(request):
            return redirect("route-driver-dashboard")
        access_code = request.POST.get("access_code", "")
        route = authenticate_route_driver(access_code)
        if not route:
            return render(
                request,
                self.template_name,
                {"error": "Invalid access code. Check the code and try again."},
            )
        login_route_driver(request, route)
        return redirect("route-driver-dashboard")


class RouteDriverLogoutView(View):
    def get(self, request):
        logout_route_driver(request)
        return redirect("route-driver-login")

    def post(self, request):
        logout_route_driver(request)
        return redirect("route-driver-login")


class RouteDriverDashboardView(RouteDriverRequiredMixin, TemplateView):
    template_name = "masters/route_driver_dashboard.html"
    permission_flag = None

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        route = self.route_driver
        from_date, to_date = _parse_period(self.request)
        selected_point_id = _parse_point_id(self.request, route)
        detail_kind = _parse_detail_kind(self.request)
        point_options = route.collection_points.order_by("number", "name").values(
            "id", "number", "name"
        )
        report_params = {"from": from_date.isoformat(), "to": to_date.isoformat()}
        if selected_point_id:
            report_params["point"] = str(selected_point_id)
        report_query = urlencode(report_params)
        pending_batches = _pending_goods_batches_for_driver(route)
        ctx.update(
            {
                "route": route,
                "from_date": from_date,
                "to_date": to_date,
                "driver_name": route.driver_name or "",
                "vehicle_label": route.vehicle_name or "—",
                "point_options": point_options,
                "selected_point_id": selected_point_id,
                "detail_kind": detail_kind,
                "report_query": report_query,
                "show_milk_reports": detail_kind != "goods",
                "show_goods_reports": detail_kind != "milk",
                "pending_goods_count": len(pending_batches),
                "pending_goods_items": [
                    _serialize_pending_goods_batch(batch) for batch in pending_batches[:8]
                ],
            }
        )
        return ctx


def _driver_request_products_for_route(route):
    price_branch_id = getattr(route, "branch_id", None)
    request_products = []
    if not price_branch_id:
        return request_products
    for row in farmer_goods_branch_check_rows(price_branch_id):
        available = row.get("effective_available") or Decimal("0")
        if available <= Decimal("0"):
            continue
        unit_price = row.get("unit_price") or Decimal("0")
        if unit_price <= Decimal("0"):
            continue
        unit_price = unit_price.quantize(Decimal("0.01"))
        request_products.append(
            {
                "id": row["product_id"],
                "name": row["product_name"],
                "unit": row.get("unit") or "",
                "unit_price": str(unit_price),
                "has_price": True,
                "available": str(available.quantize(Decimal("0.01"))),
            }
        )
    return request_products


class RouteDriverGoodsRequestView(RouteDriverRequiredMixin, TemplateView):
    template_name = "masters/route_driver_goods_request.html"
    permission_flag = "driver_can_view_farmer_goods"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        route = self.route_driver
        point_options = route.collection_points.order_by("number", "name").values(
            "id", "number", "name"
        )
        my_requests = list(
            DriverFarmerGoodsRequest.objects.filter(route=route)
            .exclude(status=DriverFarmerGoodsRequest.Status.CANCELLED)
            .select_related("collection_point")
            .prefetch_related("lines__product")
            .order_by("-requested_at")[:10]
        )
        pending_batches = _pending_goods_batches_for_driver(route)
        ctx.update(
            {
                "route": route,
                "point_options": point_options,
                "driver_goods_requests": my_requests,
                "driver_request_products_json": _driver_request_products_for_route(route),
                "back_url": reverse("route-driver-dashboard"),
                "pending_goods_count": len(pending_batches),
                "pending_goods_items": [
                    _serialize_pending_goods_batch(batch) for batch in pending_batches[:8]
                ],
            }
        )
        return ctx


class RouteDriverFarmerGoodsRequestCreateView(RouteDriverRequiredMixin, View):
    permission_flag = "driver_can_view_farmer_goods"

    def post(self, request):
        route = self.route_driver
        point_raw = (request.POST.get("collection_point") or "").strip()
        note = (request.POST.get("note") or "").strip()
        product_ids = request.POST.getlist("product_id")
        quantities = request.POST.getlist("quantity")
        try:
            point_id = int(point_raw)
        except (TypeError, ValueError):
            messages.error(request, "Select a collection point.")
            return redirect("route-driver-goods-request")
        point = get_object_or_404(
            CollectionPoint.objects.filter(route=route), pk=point_id
        )
        line_items = []
        for pid, qty in zip(product_ids, quantities):
            if not str(qty).strip():
                continue
            line_items.append((pid, qty))
        try:
            create_driver_farmer_goods_request(
                route=route,
                collection_point=point,
                line_items=line_items,
                note=note,
            )
        except ValidationError as exc:
            message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            messages.error(request, message)
            return redirect("route-driver-goods-request")
        messages.success(
            request,
            "Goods request sent. Branch staff will be notified and can issue without asking you to accept.",
        )
        return redirect("route-driver-goods-request")


class RouteDriverFarmerGoodsRequestCancelView(RouteDriverRequiredMixin, View):
    permission_flag = "driver_can_view_farmer_goods"

    def post(self, request, pk):
        req = get_object_or_404(DriverFarmerGoodsRequest, pk=pk, route=self.route_driver)
        try:
            cancel_driver_farmer_goods_request(request_obj=req, route=self.route_driver)
        except ValidationError as exc:
            message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            messages.error(request, message)
            return redirect("route-driver-goods-request")
        messages.success(request, "Goods request cancelled.")
        return redirect("route-driver-goods-request")

class RouteDriverGoodsNotificationFeedView(RouteDriverRequiredMixin, View):
    permission_flag = None

    def get(self, request):
        return JsonResponse(_pending_goods_feed(self.route_driver))


class RouteDriverFarmerGoodsAcceptView(RouteDriverRequiredMixin, View):
    permission_flag = None

    def post(self, request, batch_ref):
        note = (request.POST.get("note") or "").strip()
        try:
            accept_farmer_goods_issue_batch(
                batch_ref=batch_ref, route=self.route_driver, note=note
            )
        except ValidationError as exc:
            message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"ok": False, "error": message}, status=400)
            return HttpResponseBadRequest(message)
        feed = _pending_goods_feed(self.route_driver)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": True, "action": "accepted", **feed})
        return redirect("route-driver-dashboard")


class RouteDriverFarmerGoodsRejectView(RouteDriverRequiredMixin, View):
    permission_flag = None

    def post(self, request, batch_ref):
        note = (request.POST.get("note") or "").strip()
        try:
            reject_farmer_goods_issue_batch(
                batch_ref=batch_ref, route=self.route_driver, note=note
            )
        except ValidationError as exc:
            message = "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc)
            if request.headers.get("x-requested-with") == "XMLHttpRequest":
                return JsonResponse({"ok": False, "error": message}, status=400)
            return HttpResponseBadRequest(message)
        feed = _pending_goods_feed(self.route_driver)
        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({"ok": True, "action": "rejected", **feed})
        return redirect("route-driver-dashboard")


class RouteDriverRouteSummaryView(RouteDriverRequiredMixin, View):
    permission_flag = "driver_can_view_route_summary"

    def get(self, request):
        from_date, to_date = _parse_period(request)
        point_id = _parse_point_id(request, self.route_driver)
        detail_kind = _parse_detail_kind(request)
        ctx = build_route_summary_context(
            self.route_driver, from_date, to_date, point_id=point_id
        )
        ctx["back_url"] = _driver_dashboard_url(
            from_date, to_date, point_id=point_id, detail_kind=detail_kind
        )
        return render(request, "collections/milk_collection_route_summary_receipt.html", ctx)


class RouteDriverPointSummaryView(RouteDriverRequiredMixin, View):
    permission_flag = "driver_can_view_point_summary"

    def get(self, request):
        from_date, to_date = _parse_period(request)
        point_id = _parse_point_id(request, self.route_driver)
        detail_kind = _parse_detail_kind(request)
        ctx = build_point_summary_context(
            self.route_driver, from_date, to_date, point_id=point_id
        )
        ctx["back_url"] = _driver_dashboard_url(
            from_date, to_date, point_id=point_id, detail_kind=detail_kind
        )
        return render(request, "collections/milk_collection_point_summary_receipt.html", ctx)


class RouteDriverFarmerGoodsView(RouteDriverRequiredMixin, View):
    permission_flag = "driver_can_view_farmer_goods"

    def get(self, request):
        from_date, to_date = _parse_period(request)
        point_id = _parse_point_id(request, self.route_driver)
        detail_kind = _parse_detail_kind(request)
        if not from_date or not to_date:
            return HttpResponseBadRequest("Invalid date range.")
        ctx = build_farmer_goods_context(
            self.route_driver, from_date, to_date, point_id=point_id
        )
        ctx["back_url"] = _driver_dashboard_url(
            from_date, to_date, point_id=point_id, detail_kind=detail_kind
        )
        return render(request, "reports/point_goods_summary_slip_print.html", ctx)
