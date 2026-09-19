from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from .inbox import (
    mark_incoming_dispatch_notifications_read,
    mark_return_requested_notifications_read,
    notify_destination_response_to_source,
    notify_incoming_branch_dispatch,
    notify_return_requested_to_source,
    notify_return_resolved_to_destination,
)
from .models import MilkDistribution


def _apply_response_quantity(dispatch, quantity_kg):
    """Optionally reduce dispatch kg to the responded quantity."""
    if quantity_kg is None:
        return
    qty = Decimal(str(quantity_kg)).quantize(Decimal("0.01"))
    if qty <= Decimal("0"):
        raise ValueError("Quantity must be greater than zero.")
    if qty > dispatch.kg:
        raise ValueError("Quantity cannot exceed dispatched quantity.")
    dispatch.kg = qty


@transaction.atomic
def collect_branch_dispatch(dispatch, *, user, notes="", quantity_kg=None):
    dispatch = MilkDistribution.objects.select_for_update().get(pk=dispatch.pk)
    if not dispatch.is_branch_dispatch():
        raise ValueError("Not a branch dispatch.")
    if dispatch.branch_destination_response != MilkDistribution.BranchDestinationResponse.PENDING:
        raise ValueError("This dispatch was already handled.")
    _apply_response_quantity(dispatch, quantity_kg)
    dispatch.branch_destination_response = MilkDistribution.BranchDestinationResponse.COLLECTED
    dispatch.status = MilkDistribution.DistributionStatus.COMPLETED
    dispatch.branch_response_notes = notes or ""
    dispatch.branch_responded_at = timezone.now()
    dispatch.branch_responded_by = user
    dispatch.save()
    mark_incoming_dispatch_notifications_read(dispatch)
    notify_destination_response_to_source(dispatch, action_label="Collected", actor=user)
    return dispatch


@transaction.atomic
def request_branch_return(dispatch, *, user, notes="", quantity_kg=None):
    dispatch = MilkDistribution.objects.select_for_update().get(pk=dispatch.pk)
    if not dispatch.is_branch_dispatch():
        raise ValueError("Not a branch dispatch.")
    if dispatch.branch_destination_response != MilkDistribution.BranchDestinationResponse.PENDING:
        raise ValueError("This dispatch was already handled.")
    qty = quantity_kg if quantity_kg is not None else dispatch.kg
    if qty <= Decimal("0"):
        raise ValueError("Return quantity must be greater than zero.")
    if qty > dispatch.kg:
        raise ValueError("Return quantity cannot exceed dispatched quantity.")
    dispatch.branch_destination_response = MilkDistribution.BranchDestinationResponse.RETURN_REQUESTED
    dispatch.branch_source_return_response = MilkDistribution.BranchSourceReturnResponse.PENDING
    dispatch.branch_return_quantity_kg = qty
    dispatch.branch_response_notes = notes or ""
    dispatch.branch_responded_at = timezone.now()
    dispatch.branch_responded_by = user
    dispatch.save()
    mark_incoming_dispatch_notifications_read(dispatch)
    notify_return_requested_to_source(dispatch, actor=user)
    return dispatch


@transaction.atomic
def divert_branch_dispatch_to_branch(dispatch, *, user, target_branch, notes="", quantity_kg=None):
    dispatch = MilkDistribution.objects.select_for_update().get(pk=dispatch.pk)
    if not dispatch.is_branch_dispatch():
        raise ValueError("Not a branch dispatch.")
    if dispatch.branch_destination_response != MilkDistribution.BranchDestinationResponse.PENDING:
        raise ValueError("This dispatch was already handled.")
    if dispatch.branch_id == target_branch.pk:
        raise ValueError("Cannot divert to the source branch.")
    if dispatch.destination_branch_id == target_branch.pk:
        raise ValueError("Cannot divert to the same destination branch.")
    _apply_response_quantity(dispatch, quantity_kg)
    old_destination = dispatch.destination_branch
    dispatch.branch_destination_response = MilkDistribution.BranchDestinationResponse.DIVERTED
    dispatch.branch_response_notes = notes or ""
    dispatch.branch_responded_at = timezone.now()
    dispatch.branch_responded_by = user
    dispatch.diverted_by_branch = old_destination
    dispatch.divert_to_branch = target_branch
    dispatch.divert_to_buyer = None
    dispatch.save()
    mark_incoming_dispatch_notifications_read(dispatch)
    notify_destination_response_to_source(
        dispatch,
        action_label=f"Diverted to branch {target_branch.name}",
        actor=user,
    )

    dispatch.destination_branch = target_branch
    dispatch.branch_destination_response = MilkDistribution.BranchDestinationResponse.PENDING
    dispatch.branch_source_return_response = ""
    dispatch.branch_return_quantity_kg = None
    dispatch.branch_responded_at = None
    dispatch.branch_responded_by = None
    dispatch.status = MilkDistribution.DistributionStatus.PENDING
    dispatch.save()
    notify_incoming_branch_dispatch(dispatch, actor=user)
    return dispatch


@transaction.atomic
def divert_branch_dispatch_to_buyer(dispatch, *, user, target_buyer, notes="", quantity_kg=None):
    dispatch = MilkDistribution.objects.select_for_update().get(pk=dispatch.pk)
    if not dispatch.is_branch_dispatch():
        raise ValueError("Not a branch dispatch.")
    if dispatch.branch_destination_response != MilkDistribution.BranchDestinationResponse.PENDING:
        raise ValueError("This dispatch was already handled.")
    _apply_response_quantity(dispatch, quantity_kg)
    diverted_from = dispatch.destination_branch
    dispatch.branch_destination_response = MilkDistribution.BranchDestinationResponse.DIVERTED
    dispatch.branch_response_notes = notes or ""
    dispatch.branch_responded_at = timezone.now()
    dispatch.branch_responded_by = user
    dispatch.diverted_by_branch = diverted_from
    dispatch.divert_to_buyer = target_buyer
    dispatch.divert_to_branch = None
    dispatch.destination_branch = None
    dispatch.buyer = target_buyer
    dispatch.branch_destination_response = ""
    dispatch.branch_source_return_response = ""
    dispatch.status = MilkDistribution.DistributionStatus.PENDING
    dispatch.save()
    mark_incoming_dispatch_notifications_read(dispatch)
    notify_destination_response_to_source(
        dispatch,
        action_label=f"Diverted to buyer {target_buyer}",
        actor=user,
    )
    return dispatch


@transaction.atomic
def accept_branch_return(dispatch, *, user, notes=""):
    dispatch = MilkDistribution.objects.select_for_update().get(pk=dispatch.pk)
    if not dispatch.needs_source_return_acceptance():
        raise ValueError("No pending return to accept.")
    qty = dispatch.branch_return_quantity_kg or dispatch.kg
    dispatch.branch_source_return_response = MilkDistribution.BranchSourceReturnResponse.ACCEPTED
    dispatch.source_return_responded_at = timezone.now()
    dispatch.source_return_responded_by = user
    dispatch.status = MilkDistribution.DistributionStatus.RETURN
    dispatch.returned_branch = dispatch.branch
    dispatch.buyer_result_quantity = qty
    if notes:
        dispatch.branch_response_notes = notes
    dispatch.save()
    mark_return_requested_notifications_read(dispatch)
    notify_return_resolved_to_destination(dispatch, accepted=True, actor=user)
    return dispatch


@transaction.atomic
def reject_branch_return(dispatch, *, user, notes=""):
    dispatch = MilkDistribution.objects.select_for_update().get(pk=dispatch.pk)
    if not dispatch.needs_source_return_acceptance():
        raise ValueError("No pending return to reject.")
    dispatch.branch_source_return_response = MilkDistribution.BranchSourceReturnResponse.REJECTED
    dispatch.source_return_responded_at = timezone.now()
    dispatch.source_return_responded_by = user
    dispatch.branch_destination_response = MilkDistribution.BranchDestinationResponse.PENDING
    dispatch.branch_return_quantity_kg = None
    if notes:
        dispatch.branch_response_notes = notes
    dispatch.save()
    mark_return_requested_notifications_read(dispatch)
    notify_return_resolved_to_destination(dispatch, accepted=False, actor=user)
    return dispatch
