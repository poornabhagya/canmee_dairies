from django.contrib.auth import get_user_model
from django.db.models import Q
from django.urls import reverse
from django.utils.timesince import timesince
from decimal import Decimal

from branches.utils import get_user_branch_ids

from canmee_dairies.constants import MILK_LITER_FACTOR

from .models import DispatchNotification, MilkDistribution

User = get_user_model()


def actionable_dispatch_notifications_qs(user):
    """Hide alerts once the related dispatch action is no longer pending."""
    qs = DispatchNotification.objects.filter(recipient=user).select_related("dispatch")
    pending_incoming = Q(
        kind=DispatchNotification.Kind.INCOMING_DISPATCH,
        dispatch__destination_branch_id__isnull=False,
        dispatch__branch_destination_response=MilkDistribution.BranchDestinationResponse.PENDING,
    ) & ~Q(dispatch__status=MilkDistribution.DistributionStatus.CANCELLED)
    pending_return = Q(
        kind=DispatchNotification.Kind.RETURN_REQUESTED,
        dispatch__branch_destination_response=MilkDistribution.BranchDestinationResponse.RETURN_REQUESTED,
        dispatch__branch_source_return_response=MilkDistribution.BranchSourceReturnResponse.PENDING,
    ) & ~Q(dispatch__status=MilkDistribution.DistributionStatus.CANCELLED)
    return qs.filter(
        (~Q(kind=DispatchNotification.Kind.INCOMING_DISPATCH) | pending_incoming),
        (~Q(kind=DispatchNotification.Kind.RETURN_REQUESTED) | pending_return),
    )


def mark_notification_read_for_branch(*, dispatch_id, kind):
    """Mark every copy of a branch notification read (all recipients)."""
    if not dispatch_id:
        return 0
    return DispatchNotification.objects.filter(
        dispatch_id=dispatch_id,
        kind=kind,
        is_read=False,
    ).update(is_read=True)


def mark_notification_read(notification):
    """Branch notifications are shared: one user marking read clears for all branch users."""
    if notification.dispatch_id:
        return mark_notification_read_for_branch(
            dispatch_id=notification.dispatch_id,
            kind=notification.kind,
        )
    return DispatchNotification.objects.filter(
        pk=notification.pk,
        is_read=False,
    ).update(is_read=True)


def mark_all_actionable_notifications_read(user):
    """Mark all actionable unread notifications read, branch-wide where applicable."""
    qs = actionable_dispatch_notifications_qs(user).filter(is_read=False)
    marked_pairs = set()
    total = 0
    for notification in qs.only("id", "dispatch_id", "kind"):
        if notification.dispatch_id:
            pair = (notification.dispatch_id, notification.kind)
            if pair in marked_pairs:
                continue
            marked_pairs.add(pair)
            total += mark_notification_read_for_branch(
                dispatch_id=notification.dispatch_id,
                kind=notification.kind,
            )
        else:
            total += DispatchNotification.objects.filter(
                pk=notification.pk,
                is_read=False,
            ).update(is_read=True)
    return total


def mark_incoming_dispatch_notifications_read(dispatch):
    return mark_notification_read_for_branch(
        dispatch_id=dispatch.pk,
        kind=DispatchNotification.Kind.INCOMING_DISPATCH,
    )


def mark_return_requested_notifications_read(dispatch):
    return mark_notification_read_for_branch(
        dispatch_id=dispatch.pk,
        kind=DispatchNotification.Kind.RETURN_REQUESTED,
    )


def _branch_user_qs(branch):
    if not branch:
        return User.objects.none()
    return branch.users.filter(is_active=True).distinct()


def _notification_recipient_qs(branch):
    """Branch-assigned users plus active superusers (global oversight)."""
    if not branch:
        return User.objects.none()
    branch_users = branch.users.filter(is_active=True)
    superusers = User.objects.filter(is_active=True, is_superuser=True)
    return (branch_users | superusers).distinct()


def _branch_name(branch):
    return branch.name if branch else "—"


def notify_users(users, *, kind, title, body, dispatch=None, action_url=""):
    rows = []
    for user in users.distinct():
        rows.append(
            DispatchNotification(
                recipient=user,
                dispatch=dispatch,
                kind=kind,
                title=title,
                body=body,
                action_url=action_url,
            )
        )
    if rows:
        DispatchNotification.objects.bulk_create(rows)


def notify_branch_users(branch, *, exclude_user=None, **kwargs):
    users = _notification_recipient_qs(branch)
    if exclude_user:
        users = users.exclude(pk=exclude_user.pk)
    notify_users(users, **kwargs)


def dispatch_list_url(dispatch):
    if dispatch.is_branch_dispatch():
        return reverse("distribution-list") + "?filter_tab=branch&branch_scope=incoming"
    if (
        dispatch.buyer_id
        and dispatch.status == MilkDistribution.DistributionStatus.RETURN
        and dispatch.returned_branch_id
    ):
        return (
            reverse("distribution-list")
            + f"?filter_tab=buyer&branches={dispatch.returned_branch_id}"
        )
    return reverse("distribution-list") + "?filter_tab=buyer"


def dispatch_respond_url(dispatch):
    return reverse("distribution-branch-respond", args=[dispatch.pk])


def dispatch_return_resolve_url(dispatch):
    return reverse("distribution-branch-return", args=[dispatch.pk])


def notification_action_url(dispatch, kind):
    if not dispatch:
        return ""
    if kind == DispatchNotification.Kind.INCOMING_DISPATCH:
        return dispatch_respond_url(dispatch)
    if kind == DispatchNotification.Kind.RETURN_REQUESTED:
        return dispatch_return_resolve_url(dispatch)
    return dispatch_list_url(dispatch)


def notify_incoming_branch_dispatch(dispatch, *, actor=None):
    if not dispatch.destination_branch_id:
        return
    title = f"Incoming dispatch #{dispatch.dispatch_no}"
    body = (
        f"{dispatch.kg} kg from {_branch_name(dispatch.branch)} is on the way to "
        f"{_branch_name(dispatch.destination_branch)}. Please collect, return to source, or divert."
    )
    notify_branch_users(
        dispatch.destination_branch,
        exclude_user=actor,
        kind=DispatchNotification.Kind.INCOMING_DISPATCH,
        title=title,
        body=body,
        dispatch=dispatch,
        action_url=notification_action_url(dispatch, DispatchNotification.Kind.INCOMING_DISPATCH),
    )


def notify_destination_response_to_source(dispatch, *, action_label, actor=None):
    if not dispatch.branch_id:
        return
    title = f"Dispatch #{dispatch.dispatch_no} — {action_label}"
    body = (
        f"{_branch_name(dispatch.destination_branch)} responded to dispatch #{dispatch.dispatch_no}: "
        f"{action_label}."
    )
    if dispatch.branch_response_notes:
        body += f" Notes: {dispatch.branch_response_notes}"
    notify_branch_users(
        dispatch.branch,
        exclude_user=actor,
        kind=DispatchNotification.Kind.DESTINATION_RESPONDED,
        title=title,
        body=body,
        dispatch=dispatch,
        action_url=notification_action_url(dispatch, DispatchNotification.Kind.DESTINATION_RESPONDED),
    )


def notify_return_requested_to_source(dispatch, *, actor=None):
    if not dispatch.branch_id:
        return
    title = f"Return requested for dispatch #{dispatch.dispatch_no}"
    qty = dispatch.branch_return_quantity_kg or dispatch.kg
    body = (
        f"{_branch_name(dispatch.destination_branch)} requested to return {qty} kg from "
        f"dispatch #{dispatch.dispatch_no}. Please accept or reject."
    )
    notify_branch_users(
        dispatch.branch,
        exclude_user=actor,
        kind=DispatchNotification.Kind.RETURN_REQUESTED,
        title=title,
        body=body,
        dispatch=dispatch,
        action_url=notification_action_url(dispatch, DispatchNotification.Kind.RETURN_REQUESTED),
    )


def notify_return_resolved_to_destination(dispatch, *, accepted, actor=None):
    if not dispatch.destination_branch_id:
        return
    label = "accepted" if accepted else "rejected"
    title = f"Return {label} for dispatch #{dispatch.dispatch_no}"
    body = f"{_branch_name(dispatch.branch)} {label} the return request for dispatch #{dispatch.dispatch_no}."
    notify_branch_users(
        dispatch.destination_branch,
        exclude_user=actor,
        kind=DispatchNotification.Kind.RETURN_RESOLVED,
        title=title,
        body=body,
        dispatch=dispatch,
        action_url=notification_action_url(dispatch, DispatchNotification.Kind.RETURN_RESOLVED),
    )


def should_notify_buyer_return(prev, dispatch):
    has_return = bool(
        dispatch.returned_branch_id
        and dispatch.buyer_result_quantity
        and dispatch.buyer_result_quantity > Decimal("0")
    )
    if not has_return:
        return False
    if not prev:
        return True
    return (
        prev.get("returned_branch_id") != dispatch.returned_branch_id
        or prev.get("buyer_result_quantity") != dispatch.buyer_result_quantity
    )


def maybe_notify_buyer_return_to_branch(dispatch_id, *, prev=None, actor=None):
    dispatch = MilkDistribution.objects.select_related(
        "returned_branch", "branch", "buyer"
    ).filter(pk=dispatch_id).first()
    if not dispatch or not should_notify_buyer_return(prev, dispatch):
        return
    notify_buyer_return_to_branch(dispatch, actor=actor)


def notify_buyer_return_to_branch(dispatch, *, actor=None):
    """Notify the receiving branch when a buyer dispatch is marked as return."""
    if not dispatch.returned_branch_id:
        return
    qty_kg = (dispatch.buyer_result_quantity or Decimal("0")).quantize(Decimal("0.01"))
    if qty_kg <= 0:
        return
    liters = (qty_kg * MILK_LITER_FACTOR).quantize(Decimal("0.01"))
    buyer_label = str(dispatch.buyer) if dispatch.buyer_id else "buyer"
    source_label = _branch_name(dispatch.branch)
    title = f"Return received for dispatch #{dispatch.dispatch_no}"
    body = (
        f"{liters} L ({qty_kg} kg) from {buyer_label} dispatch #{dispatch.dispatch_no} "
        f"(sent from {source_label}) is credited to {_branch_name(dispatch.returned_branch)}."
    )
    notify_branch_users(
        dispatch.returned_branch,
        kind=DispatchNotification.Kind.RETURN_RECEIVED,
        title=title,
        body=body,
        dispatch=dispatch,
        action_url=notification_action_url(dispatch, DispatchNotification.Kind.RETURN_RECEIVED),
    )


def notification_feed_item(note):
    item = {
        "id": note.pk,
        "kind": note.kind,
        "title": note.title,
        "body": note.body,
        "created_ago": f"{timesince(note.created_at)} ago",
        "link_href": "",
        "modal_type": "list",
        "meta_href": "",
        "meta_label": "",
        "read_url": reverse("dispatch-notification-read", args=[note.pk]),
    }
    if note.kind == DispatchNotification.Kind.INCOMING_DISPATCH and note.dispatch_id:
        href = (
            reverse("distribution-branch-respond", args=[note.dispatch_id])
            + "?filter_tab=branch&branch_scope=incoming"
        )
        item["link_href"] = href
        item["modal_type"] = "respond"
        item["meta_href"] = href
        item["meta_label"] = "Open"
    elif note.kind == DispatchNotification.Kind.RETURN_REQUESTED and note.dispatch_id:
        href = (
            reverse("distribution-branch-return", args=[note.dispatch_id])
            + "?filter_tab=branch&branch_scope=outgoing"
        )
        item["link_href"] = href
        item["modal_type"] = "return"
        item["meta_href"] = href
        item["meta_label"] = "Open"
    elif note.action_url:
        item["link_href"] = note.action_url
        item["modal_type"] = "list"
        item["meta_href"] = note.action_url
        item["meta_label"] = (
            "View return"
            if note.kind == DispatchNotification.Kind.RETURN_RECEIVED
            else "Open"
        )
    return item


def dispatch_notifications_feed(user, *, limit=8):
    unread_qs = (
        actionable_dispatch_notifications_qs(user)
        .filter(is_read=False)
        .select_related("dispatch")
    )
    return {
        "ok": True,
        "unread": unread_qs.count(),
        "notifications": [notification_feed_item(note) for note in unread_qs[:limit]],
    }


def user_can_access_branch(user, branch_id):
    if not branch_id:
        return False
    if user.is_superuser:
        return True
    branch_ids = get_user_branch_ids(user)
    return branch_ids is None or branch_id in branch_ids


def user_can_respond_as_destination(user, dispatch):
    if not dispatch.needs_destination_response():
        return False
    return user_can_access_branch(user, dispatch.destination_branch_id)


def user_can_resolve_return_as_source(user, dispatch):
    if not dispatch.needs_source_return_acceptance():
        return False
    return user_can_access_branch(user, dispatch.branch_id)
