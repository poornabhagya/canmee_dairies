"""Keep each collection point's daily POINT row equal to the sum of farmer milk at that point."""

from __future__ import annotations

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from masters.models import CollectionPoint, Farmer

from .models import CollectionSource, MilkCollection


def _quantize_kg(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def _farmer_milk_qs_at_point(date, route_id: int, collection_point_id: int, *, base_qs=None):
    """
    Farmer milk rows that roll up to a collection point for this date/route.

    Single-point routes: all farmer lines on the route (farmers may lack a master assignment).
    Multi-point routes: only farmers assigned to this collection point in master data.
    """
    qs = (base_qs or MilkCollection.objects.all()).filter(
        date=date,
        route_id=route_id,
        source=CollectionSource.FARMER,
    )
    if CollectionPoint.objects.filter(route_id=route_id).count() > 1:
        qs = qs.filter(farmer__collection_point_id=collection_point_id)
    return qs


def sum_farmer_kg_at_point(date, route_id: int, collection_point_id: int, *, base_qs=None) -> Decimal:
    agg = _farmer_milk_qs_at_point(
        date, route_id, collection_point_id, base_qs=base_qs
    ).aggregate(total=Sum("kg"))
    return _quantize_kg(agg["total"] or 0)


def sum_farmer_milk_at_point(date, route_id: int, collection_point_id: int, *, base_qs=None):
    agg = _farmer_milk_qs_at_point(
        date, route_id, collection_point_id, base_qs=base_qs
    ).aggregate(total_kg=Sum("kg"), total_liters=Sum("liters"))
    return (
        _quantize_kg(agg["total_kg"]),
        _quantize_kg(agg["total_liters"]),
    )


def sync_collection_point_total_from_farmers(
    date, route_id: int, collection_point_id: int, user, *, save_snapshot: bool = False
) -> None:
    """
    Set the POINT MilkCollection row kg to the sum of all farmer milk for farmers assigned to this point
    (same date and route). Removes the POINT row when that sum is zero.

    When save_snapshot=True (manual Sync button), store the previous point kg so a superuser can reverse.
    """
    from .models import CollectionPointMilkReconcileSyncSnapshot

    total = sum_farmer_kg_at_point(date, route_id, collection_point_id)

    with transaction.atomic():
        row = (
            MilkCollection.objects.select_for_update()
            .filter(
                date=date,
                route_id=route_id,
                collection_point_id=collection_point_id,
                source=CollectionSource.POINT,
            )
            .first()
        )

        if save_snapshot:
            CollectionPointMilkReconcileSyncSnapshot.objects.update_or_create(
                collection_point_id=collection_point_id,
                route_id=route_id,
                date=date,
                defaults={
                    "previous_kg": _quantize_kg(row.kg) if row else None,
                    "had_point_row": bool(row),
                    "synced_by": user if getattr(user, "is_authenticated", False) else None,
                },
            )

        if total == 0:
            if row:
                row.delete()
            return

        cp = CollectionPoint.objects.get(pk=collection_point_id)
        if row is None:
            MilkCollection.objects.create(
                date=date,
                route_id=route_id,
                collection_point=cp,
                source=CollectionSource.POINT,
                kg=total,
                created_by=user,
            )
        else:
            row.kg = total
            row.save()


def reverse_collection_point_sync_from_snapshot(date, route_id: int, collection_point_id: int, user):
    """
    Restore the point row from the last Sync snapshot and clear the snapshot.
    Returns the restored kg (0 means the point row was removed), or None if no snapshot.
    """
    from .models import CollectionPointMilkReconcileSyncSnapshot

    with transaction.atomic():
        snapshot = (
            CollectionPointMilkReconcileSyncSnapshot.objects.select_for_update()
            .filter(
                collection_point_id=collection_point_id,
                route_id=route_id,
                date=date,
            )
            .first()
        )
        if snapshot is None:
            return None

        row = (
            MilkCollection.objects.select_for_update()
            .filter(
                date=date,
                route_id=route_id,
                collection_point_id=collection_point_id,
                source=CollectionSource.POINT,
            )
            .first()
        )

        if not snapshot.had_point_row:
            if row:
                row.delete()
            restored = Decimal("0.00")
        else:
            previous_kg = _quantize_kg(snapshot.previous_kg)
            cp = CollectionPoint.objects.get(pk=collection_point_id)
            if row is None:
                MilkCollection.objects.create(
                    date=date,
                    route_id=route_id,
                    collection_point=cp,
                    source=CollectionSource.POINT,
                    kg=previous_kg,
                    created_by=user,
                )
            else:
                row.kg = previous_kg
                row.save()
            restored = previous_kg

        snapshot.delete()
        return restored


def reconcile_sync_snapshot_map(collection_point_ids, start, end):
    """Map (collection_point_id, route_id, date) → snapshot for reverse-sync UI."""
    from .models import CollectionPointMilkReconcileSyncSnapshot

    result = {}
    if not collection_point_ids or not start or not end:
        return result
    for snap in CollectionPointMilkReconcileSyncSnapshot.objects.filter(
        collection_point_id__in=collection_point_ids,
        date__gte=start,
        date__lte=end,
    ).only("collection_point_id", "route_id", "date", "previous_kg", "had_point_row"):
        result[(snap.collection_point_id, snap.route_id, snap.date)] = {
            "previous_kg": _quantize_kg(snap.previous_kg) if snap.had_point_row else Decimal("0.00"),
            "had_point_row": snap.had_point_row,
            "can_reverse": True,
        }
    return result


def attach_reconcile_sync_snapshots(rows):
    """Attach reverse-sync snapshot fields onto reconcile row dicts."""
    if not rows:
        return rows
    point_ids = {r["collection_point_id"] for r in rows}
    dates = [r["date"] for r in rows]
    snap_map = reconcile_sync_snapshot_map(point_ids, min(dates), max(dates))
    for row in rows:
        snap = snap_map.get((row["collection_point_id"], row["route_id"], row["date"]))
        if snap:
            row["can_reverse_sync"] = True
            row["sync_previous_kg"] = snap["previous_kg"]
            row["sync_had_point_row"] = snap["had_point_row"]
        else:
            row["can_reverse_sync"] = False
            row["sync_previous_kg"] = None
            row["sync_had_point_row"] = False
    return rows


def _sync_keys_for_farmer_row(mc: MilkCollection, previous: dict | None):
    """(date, route_id, collection_point_id) tuples that need recomputation."""
    keys = set()
    if previous:
        if previous.get("source") == CollectionSource.FARMER and previous.get("farmer_id"):
            pf = (
                Farmer.objects.select_related("collection_point")
                .filter(pk=previous["farmer_id"])
                .first()
            )
            if (
                pf
                and pf.collection_point_id
                and previous.get("date") is not None
                and previous.get("route_id") is not None
            ):
                keys.add((previous["date"], int(previous["route_id"]), int(pf.collection_point_id)))
    if mc.source == CollectionSource.FARMER and mc.farmer_id:
        nf = Farmer.objects.select_related("collection_point").filter(pk=mc.farmer_id).first()
        if nf and nf.collection_point_id:
            keys.add((mc.date, int(mc.route_id), int(nf.collection_point_id)))
    return keys


def sync_farmer_assigned_point_totals(mc: MilkCollection, previous: dict | None, user) -> None:
    """
    After create/update of a milk collection row, recompute POINT totals for every
    affected collection point so POINT kg == sum of farmer rows for that point/day/route.
    """
    for date, route_id, cp_id in _sync_keys_for_farmer_row(mc, previous):
        sync_collection_point_total_from_farmers(date, route_id, cp_id, user)


def sync_point_after_farmer_adjust(mc: MilkCollection, user) -> None:
    """After add/subtract on a FARMER row, recompute the assigned point's total."""
    if mc.source != CollectionSource.FARMER or not mc.farmer_id:
        return
    farmer = Farmer.objects.select_related("collection_point").filter(pk=mc.farmer_id).first()
    if not farmer or not farmer.collection_point_id:
        return
    sync_collection_point_total_from_farmers(
        mc.date, int(mc.route_id), int(farmer.collection_point_id), user
    )
