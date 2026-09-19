from decimal import Decimal

from canmee_dairies.constants import MILK_LITER_FACTOR

from .models import Branch


def get_user_branch_ids(user):
    if not user.is_authenticated:
        return []
    if user.is_superuser:
        return None
    return list(user.assigned_branches.values_list("id", flat=True))


def get_allowed_branches_qs(user):
    ids = get_user_branch_ids(user)
    if ids is None:
        return Branch.objects.all()
    if not ids:
        return Branch.objects.none()
    return Branch.objects.filter(id__in=ids)


def get_default_branch_id(user):
    if not user.is_authenticated or user.is_superuser:
        return None
    ids = list(user.assigned_branches.values_list("id", flat=True)[:2])
    return ids[0] if len(ids) == 1 else None


def kg_to_branch_quantity(kg_value, branch):
    kg_value = kg_value or Decimal("0.00")
    if branch.collection_unit == Branch.CollectionUnit.LITERS:
        return (kg_value * MILK_LITER_FACTOR).quantize(Decimal("0.01"))
    return kg_value.quantize(Decimal("0.01"))


def branch_quantity_to_kg(quantity, branch):
    quantity = quantity or Decimal("0.00")
    if branch.collection_unit == Branch.CollectionUnit.LITERS:
        return (quantity / MILK_LITER_FACTOR).quantize(Decimal("0.01"))
    return quantity.quantize(Decimal("0.01"))


def filter_by_user_branches(queryset, user, branch_lookup="branch_id"):
    ids = get_user_branch_ids(user)
    if ids is None:
        return queryset
    if not ids:
        return queryset.none()
    return queryset.filter(**{f"{branch_lookup}__in": ids})


def filter_m2m_by_user_branches(queryset, user, m2m_lookup="branches"):
    ids = get_user_branch_ids(user)
    if ids is None:
        return queryset
    if not ids:
        return queryset.none()
    return queryset.filter(**{f"{m2m_lookup}__id__in": ids}).distinct()


def filter_m2m_by_branch(queryset, branch_id, m2m_lookup="branches"):
    if not branch_id:
        return queryset
    return queryset.filter(**{f"{m2m_lookup}__id": branch_id}).distinct()


def resolve_form_branch_id(form):
    raw = form.data.get("branch") if form.data else None
    if raw and str(raw).isdigit():
        return int(raw)
    branch_id = getattr(form.instance, "branch_id", None)
    if branch_id:
        return branch_id
    init = form.initial.get("branch")
    if init and str(init).isdigit():
        return int(init)
    return None


def master_branch_option_rows(queryset):
    return [
        {
            "id": obj.pk,
            "name": str(obj),
            "branches": list(obj.branches.values_list("id", flat=True)),
        }
        for obj in queryset
    ]


def queryset_with_required_pk(queryset, pk):
    """Rebuild queryset by pk list so OR-combines are not needed after distinct()."""
    if pk is None:
        return queryset.distinct()
    model = queryset.model
    pks = list(queryset.values_list("pk", flat=True))
    if pk not in pks:
        pks.append(pk)
    if not pks:
        return model.objects.none()
    return model.objects.filter(pk__in=pks)
