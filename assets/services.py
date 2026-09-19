from calendar import monthrange
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (
    Asset,
    AssetCategory,
    AssetDepreciationEntry,
    AssetLabelBatch,
    AssetLocation,
    AssetModuleSettings,
    AssetTransfer,
    AssetType,
    AssetValuation,
    AssetVerification,
    DepreciationPolicy,
)


TWOPLACES = Decimal("0.01")


def money(value):
    return Decimal(value or 0).quantize(TWOPLACES, rounding=ROUND_HALF_UP)


def depreciation_enabled():
    return AssetModuleSettings.get_solo().enable_depreciation


def next_asset_tag(prefix=None):
    settings = AssetModuleSettings.get_solo()
    prefix = (prefix or settings.default_label_prefix or "AST").strip().upper()
    last = (
        Asset.objects.filter(asset_tag__startswith=prefix)
        .order_by("-asset_tag")
        .values_list("asset_tag", flat=True)
        .first()
    )
    seq = 1
    if last:
        suffix = last[len(prefix) :].lstrip("-")
        if suffix.isdigit():
            seq = int(suffix) + 1
    return f"{prefix}-{seq:05d}"


@transaction.atomic
def transfer_asset(*, asset, to_branch, to_location=None, user=None, note=""):
    from_branch = asset.branch
    from_location = asset.location
    asset.branch = to_branch
    if to_location is not None:
        asset.location = to_location
    asset.save(update_fields=["branch", "location", "updated_at"])
    return AssetTransfer.objects.create(
        asset=asset,
        from_branch=from_branch,
        to_branch=to_branch,
        from_location=from_location,
        to_location=to_location,
        note=(note or "")[:255],
        transferred_by=user,
    )


@transaction.atomic
def record_verification(*, asset, user=None, result="found", condition="", location=None, assessed_value=None, note=""):
    verification = AssetVerification.objects.create(
        asset=asset,
        verified_by=user,
        result=result,
        condition=condition or asset.condition,
        location=location or asset.location,
        assessed_value=money(assessed_value) if assessed_value is not None else None,
        note=note or "",
    )
    update_fields = ["updated_at"]
    if condition:
        asset.condition = condition
        update_fields.append("condition")
    if location is not None:
        asset.location = location
        update_fields.append("location")
    if result == AssetVerification.Result.MISSING:
        asset.status = Asset.Status.LOST
        update_fields.append("status")
    elif result == AssetVerification.Result.DAMAGED:
        asset.condition = Asset.Condition.DAMAGED
        if "condition" not in update_fields:
            update_fields.append("condition")
    if assessed_value is not None:
        previous = money(asset.current_value)
        new_val = money(assessed_value)
        AssetValuation.objects.create(
            asset=asset,
            previous_value=previous,
            new_value=new_val,
            reason=f"Verification ({result})",
            valued_by=user,
        )
        asset.current_value = new_val
        update_fields.append("current_value")
    asset.save(update_fields=update_fields)
    return verification


@transaction.atomic
def record_valuation(*, asset, new_value, user=None, reason=""):
    previous = money(asset.current_value)
    new_val = money(new_value)
    valuation = AssetValuation.objects.create(
        asset=asset,
        previous_value=previous,
        new_value=new_val,
        reason=(reason or "")[:255],
        valued_by=user,
    )
    asset.current_value = new_val
    asset.save(update_fields=["current_value", "updated_at"])
    return valuation


def _period_end(period_start: date) -> date:
    last_day = monthrange(period_start.year, period_start.month)[1]
    return date(period_start.year, period_start.month, last_day)


def calculate_monthly_depreciation(asset: Asset) -> Decimal:
    policy = asset.depreciation_policy
    if not policy:
        return Decimal("0.00")
    cost = money(asset.purchase_cost)
    salvage = money(asset.salvage_value)
    book = money(asset.current_value)
    if book <= salvage:
        return Decimal("0.00")
    life = asset.useful_life_months or policy.useful_life_months or 1
    if policy.method == DepreciationPolicy.Method.STRAIGHT_LINE:
        depreciable = max(cost - salvage, Decimal("0.00"))
        monthly = depreciable / Decimal(life)
    else:
        rate = money(policy.annual_rate_percent or 0) / Decimal("100")
        monthly = book * (rate / Decimal("12"))
    monthly = money(monthly)
    return min(monthly, money(book - salvage))


@transaction.atomic
def run_depreciation_for_month(*, period_start: date, user=None, asset_qs=None):
    if not depreciation_enabled():
        raise ValidationError("Depreciation is disabled. Enable it in Asset settings.")
    if period_start.day != 1:
        period_start = period_start.replace(day=1)
    period_end = _period_end(period_start)
    qs = asset_qs if asset_qs is not None else Asset.objects.all()
    qs = qs.filter(
        status=Asset.Status.ACTIVE,
        depreciation_policy__isnull=False,
    ).select_related("depreciation_policy")
    created = []
    skipped = 0
    for asset in qs:
        if AssetDepreciationEntry.objects.filter(
            asset=asset, period_start=period_start, period_end=period_end
        ).exists():
            skipped += 1
            continue
        amount = calculate_monthly_depreciation(asset)
        if amount <= 0:
            skipped += 1
            continue
        before = money(asset.current_value)
        after = money(before - amount)
        entry = AssetDepreciationEntry.objects.create(
            asset=asset,
            period_start=period_start,
            period_end=period_end,
            amount=amount,
            book_value_before=before,
            book_value_after=after,
            policy=asset.depreciation_policy,
            run_by=user,
        )
        asset.current_value = after
        asset.save(update_fields=["current_value", "updated_at"])
        created.append(entry)
    return {"created": len(created), "skipped": skipped, "entries": created}


@transaction.atomic
def create_label_batch(*, assets, user=None, note=""):
    assets = list(assets)
    batch = AssetLabelBatch.objects.create(
        created_by=user,
        label_count=len(assets),
        note=(note or "")[:255],
    )
    if assets:
        batch.assets.set(assets)
    return batch


@transaction.atomic
def reassign_and_delete_asset_type(*, old_type, new_type=None):
    """Move assets and categories off a type, then delete it."""
    remaining = AssetType.objects.exclude(pk=old_type.pk).count()
    asset_count = Asset.objects.filter(asset_type=old_type).count()
    category_count = AssetCategory.objects.filter(asset_type=old_type).count()
    if remaining == 0:
        raise ValidationError("Cannot delete the last asset type.")
    if (asset_count or category_count) and new_type is None:
        raise ValidationError("Choose another type for existing assets and categories.")
    if new_type is not None and new_type.pk == old_type.pk:
        raise ValidationError("Replacement type must be different.")
    if new_type is not None:
        Asset.objects.filter(asset_type=old_type).update(asset_type=new_type)
        AssetCategory.objects.filter(asset_type=old_type).update(asset_type=new_type)
    old_type.delete()
    return {"assets": asset_count, "categories": category_count}


def location_descendant_ids(location):
    ids = set()
    stack = list(AssetLocation.objects.filter(parent=location).values_list("pk", flat=True))
    while stack:
        pk = stack.pop()
        if pk in ids:
            continue
        ids.add(pk)
        stack.extend(AssetLocation.objects.filter(parent_id=pk).values_list("pk", flat=True))
    return ids


@transaction.atomic
def reassign_and_delete_asset_location(*, old_location, new_location=None):
    """Move assets and sub-locations off a location, then delete it."""
    asset_count = Asset.objects.filter(location=old_location).count()
    child_count = AssetLocation.objects.filter(parent=old_location).count()
    descendants = location_descendant_ids(old_location)
    others = AssetLocation.objects.exclude(pk=old_location.pk)
    if (asset_count or child_count) and new_location is None:
        raise ValidationError("Choose another location for existing assets and sub-locations.")
    if new_location is not None and new_location.pk == old_location.pk:
        raise ValidationError("Replacement location must be different.")
    if child_count and not others.exclude(pk__in=descendants).exists():
        raise ValidationError("Move or delete sub-locations first.")
    if asset_count and not others.exists():
        raise ValidationError("Add another location first, then move the assets.")
    if new_location is not None and child_count and new_location.pk in descendants:
        raise ValidationError("Cannot move sub-locations under a location that sits below this one.")
    if new_location is not None:
        Asset.objects.filter(location=old_location).update(location=new_location)
        if child_count:
            AssetLocation.objects.filter(parent=old_location).update(parent=new_location)
    old_location.delete()
    return {"assets": asset_count, "children": child_count}
