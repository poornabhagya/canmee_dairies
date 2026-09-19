from .constants import MILK_LITER_FACTOR


def milk_conversion(request):
    return {
        "MILK_LITER_FACTOR": MILK_LITER_FACTOR,
        "MILK_LITER_FACTOR_JS": format(MILK_LITER_FACTOR, "f"),
    }


def payment_sheet_nav(request):
    """Branch/period defaults for the sidebar Payment sheet picker."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"payment_sheet_nav_config": None}

    from datetime import date

    from branches.models import Branch
    from branches.utils import get_allowed_branches_qs

    today = date.today()
    default_start = today.replace(day=1).isoformat()
    default_end = today.isoformat()

    if user.is_superuser:
        branches_qs = Branch.objects.order_by("code", "name")
    else:
        branches_qs = get_allowed_branches_qs(user).order_by("code", "name")

    branches = [
        {"id": branch.id, "label": f"{branch.code} — {branch.name}"}
        for branch in branches_qs.only("id", "code", "name")
    ]
    all_ids = [b["id"] for b in branches]

    def _period_defaults(session_key):
        stored = request.session.get(session_key) or {}
        start = (stored.get("start") or "").strip() or default_start
        end = (stored.get("end") or "").strip() or default_end
        stored_branches = [
            int(b) for b in (stored.get("branches") or []) if str(b).isdigit()
        ]
        selected = [b for b in stored_branches if b in all_ids] or list(all_ids)
        return {"start": start, "end": end, "branches": selected}

    return {
        "payment_sheet_nav_config": {
            "branches": branches,
            "defaults": {"start": default_start, "end": default_end},
            "farmer": _period_defaults("farmer_payment_period"),
            "collection_point": _period_defaults("collection_point_payment_period"),
        }
    }



def _serialize_pending_goods_batch_for_context(batch, price_map=None):
    from decimal import Decimal

    from django.urls import reverse
    from django.utils.timesince import timesince

    lines = batch["lines"]
    price_map = price_map or {}
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
        "review_url": reverse(
            "farmer-goods-issue-print", kwargs={"batch_ref": batch["batch_ref"]}
        ),
        "accept_url": reverse(
            "farmer-goods-staff-accept",
            kwargs={"batch_ref": batch["batch_ref"]},
        ),
        "reject_url": reverse(
            "farmer-goods-staff-reject",
            kwargs={"batch_ref": batch["batch_ref"]},
        ),
    }


def _serialize_driver_goods_request_for_context(req, *, can_reject=False):
    from django.urls import reverse
    from django.utils.timesince import timesince

    lines = list(req.lines.all())
    point = req.collection_point
    data = {
        "id": req.pk,
        "title": f"Driver request · {point.number} — {point.name}",
        "body": (
            f"{len(lines)} product line(s) on {req.route.name}. "
            "Open to issue (skips out-of-stock items; no driver accept needed)."
        ),
        "point_label": f"{point.number} — {point.name}",
        "route_label": req.route.name,
        "line_count": len(lines),
        "created_ago": f"{timesince(req.requested_at)} ago" if req.requested_at else "",
        "fulfill_url": reverse(
            "farmer-goods-driver-request-fulfill", kwargs={"pk": req.pk}
        ),
    }
    if can_reject:
        data["reject_url"] = reverse(
            "farmer-goods-driver-request-reject", kwargs={"pk": req.pk}
        )
    return data


def dispatch_notifications(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {}
    from dispatch.inbox import actionable_dispatch_notifications_qs
    from suppliers.services import (
        approx_price_map_for_issues,
        count_pending_collection_point_batches_for_user,
        pending_collection_point_batches_for_user,
        pending_driver_farmer_goods_requests_for_user,
        user_can_view_driver_farmer_goods_requests,
    )
    from user_management.templatetags.user_tags import (
        user_can_manage_farmer_goods_notifications,
    )

    qs = actionable_dispatch_notifications_qs(user)
    unread_qs = qs.filter(is_read=False)

    pending_goods_count = 0
    pending_goods_items = []
    can_manage_goods = user_can_manage_farmer_goods_notifications(user)
    fg_accept_enabled = True
    try:
        from suppliers.services import farmer_goods_accept_notifications_enabled

        fg_accept_enabled = farmer_goods_accept_notifications_enabled()
    except Exception:
        fg_accept_enabled = True
    show_fg_accept_notifications = can_manage_goods and fg_accept_enabled
    if can_manage_goods:
        # Count is cheap; only hydrate the newest few batches for the topbar.
        pending_goods_count = count_pending_collection_point_batches_for_user(user)
        if show_fg_accept_notifications:
            batches = pending_collection_point_batches_for_user(user, limit=8)
            all_lines = []
            for batch in batches:
                all_lines.extend(batch.get("lines") or [])
            price_map = approx_price_map_for_issues(all_lines)
            pending_goods_items = [
                _serialize_pending_goods_batch_for_context(batch, price_map) for batch in batches
            ]

    pending_request_count = 0
    pending_request_items = []
    can_view_requests = user_can_view_driver_farmer_goods_requests(user)
    if can_view_requests:
        can_reject_requests = user.has_perm("suppliers.add_farmergoodsissue")
        request_qs = pending_driver_farmer_goods_requests_for_user(user)
        pending_request_count = request_qs.count()
        pending_request_items = [
            _serialize_driver_goods_request_for_context(
                req, can_reject=can_reject_requests
            )
            for req in request_qs.select_related(
                "collection_point", "route"
            ).prefetch_related("lines")[:8]
        ]

    unread_dispatch = unread_qs.count()
    notif_goods_count = pending_goods_count if show_fg_accept_notifications else 0
    return {
        "unread_dispatch_notification_count": unread_dispatch,
        "recent_dispatch_notifications": unread_qs[:8],
        "can_manage_farmer_goods_notifications": can_manage_goods,
        "farmer_goods_accept_notifications_enabled": fg_accept_enabled,
        "show_farmer_goods_accept_notifications": show_fg_accept_notifications,
        "pending_farmer_goods_count": pending_goods_count,
        "pending_farmer_goods_items": pending_goods_items,
        "can_view_driver_farmer_goods_requests": can_view_requests,
        "pending_driver_goods_request_count": pending_request_count,
        "pending_driver_goods_request_items": pending_request_items,
        "topbar_notification_badge_count": (
            unread_dispatch + notif_goods_count + pending_request_count
        ),
    }


def asset_module_nav(request):
    """Expose optional Asset Management flags for sidebar."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return {"asset_depreciation_enabled": False}
    try:
        from assets.services import depreciation_enabled

        enabled = depreciation_enabled()
    except Exception:
        enabled = False
    return {"asset_depreciation_enabled": enabled}
