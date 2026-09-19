"""Move collection point milk rows to another branch's collection point (superuser)."""

from __future__ import annotations

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from masters.models import CollectionPoint

from .models import CollectionSource, MilkCollection


def _quantize_kg(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


@transaction.atomic
def transfer_point_collections(*, user, record_ids, target_point_id) -> dict:
    if not record_ids:
        raise ValidationError("Select at least one collection point record to transfer.")

    target = CollectionPoint.objects.select_related("route", "route__branch").get(pk=target_point_id)
    if not target.route_id:
        raise ValidationError("Target collection point has no route.")

    records = list(
        MilkCollection.objects.select_for_update()
        .filter(pk__in=record_ids, source=CollectionSource.POINT)
        .select_related("collection_point", "route", "branch")
    )
    if len(records) != len(set(record_ids)):
        raise ValidationError("Some selected rows are not collection point entries or no longer exist.")

    moved = 0
    merged = 0
    skipped_paid = 0
    skipped_same = 0

    for mc in records:
        if mc.is_paid:
            skipped_paid += 1
            continue
        if mc.collection_point_id == target.pk and mc.route_id == target.route_id:
            skipped_same += 1
            continue

        existing = (
            MilkCollection.objects.select_for_update()
            .filter(
                date=mc.date,
                route_id=target.route_id,
                collection_point_id=target.pk,
                source=CollectionSource.POINT,
            )
            .exclude(pk=mc.pk)
            .first()
        )

        if existing:
            if existing.is_paid:
                raise ValidationError(
                    f"A paid collection already exists at {target.number} — {target.name} on {mc.date}."
                )
            existing.kg = _quantize_kg(existing.kg + mc.kg)
            existing.save(update_fields=["kg", "liters", "updated_at"])
            mc.delete()
            merged += 1
        else:
            mc.collection_point = target
            mc.route = target.route
            mc.branch = target.route.branch
            mc.save(update_fields=["collection_point", "route", "branch", "kg", "liters", "updated_at"])
            moved += 1

    if moved == 0 and merged == 0:
        if skipped_paid:
            raise ValidationError("Paid collection rows cannot be transferred.")
        if skipped_same:
            raise ValidationError("Selected rows are already at the target collection point.")
        raise ValidationError("No collection rows were transferred.")

    return {
        "moved": moved,
        "merged": merged,
        "skipped_paid": skipped_paid,
        "skipped_same": skipped_same,
        "target_label": f"{target.number} — {target.name}",
    }
