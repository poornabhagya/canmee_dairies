"""Helpers for explaining why a farmer cannot be deleted."""

from __future__ import annotations

from collections import Counter

from django.db.models import ProtectedError
from django.urls import reverse
from django.utils.html import escape, format_html
from django.utils.safestring import mark_safe

BLOCKER_RECORD_LIMIT = 50


def _record(kind, pk, title, subtitle, view_url, can_delete=False):
    return {
        "kind": kind,
        "id": pk,
        "title": title,
        "subtitle": subtitle or "",
        "view_url": view_url,
        "can_delete": bool(can_delete),
    }


def farmer_delete_blockers(farmer, *, can_delete=False) -> list[dict]:
    """Return related PROTECT blockers with list links and optional record rows."""
    from farmer_loans.models import FarmerLoan
    from milk_collections.models import MilkCollection, MilkFactor
    from reports.models import FarmerPeriodPayment

    blockers: list[dict] = []

    milk_qs = (
        MilkCollection.objects.filter(farmer=farmer)
        .select_related("route")
        .order_by("-date", "-id")
    )
    milk_count = milk_qs.count()
    if milk_count:
        milk_url = f"{reverse('milk-collection-list')}?tab=farmer&farmer={farmer.pk}"
        records = [
            _record(
                "milk_collection",
                row.pk,
                row.date.isoformat() if row.date else f"#{row.pk}",
                f"{row.route.name if row.route_id else '—'} · {row.kg} kg",
                milk_url,
                can_delete=can_delete,
            )
            for row in milk_qs[:BLOCKER_RECORD_LIMIT]
        ]
        blockers.append(
            {
                "key": "milk_collections",
                "label": "milk collection" if milk_count == 1 else "milk collections",
                "count": milk_count,
                "url": milk_url,
                "records": records,
            }
        )

    factor_qs = (
        MilkFactor.objects.filter(farmer=farmer)
        .select_related("route")
        .order_by("-date", "-id")
    )
    factor_count = factor_qs.count()
    if factor_count:
        factor_list_url = f"{reverse('milk-factor-list')}?farmer={farmer.pk}"
        records = [
            _record(
                "milk_factor",
                row.pk,
                row.date.isoformat() if row.date else f"#{row.pk}",
                f"{row.route.name if row.route_id else '—'} · FAT {row.fat} / SNF {row.snf}",
                reverse("milk-factor-edit", kwargs={"pk": row.pk}),
                can_delete=can_delete,
            )
            for row in factor_qs[:BLOCKER_RECORD_LIMIT]
        ]
        blockers.append(
            {
                "key": "milk_factors",
                "label": "milk factor" if factor_count == 1 else "milk factors",
                "count": factor_count,
                "url": factor_list_url,
                "records": records,
            }
        )

    loan_qs = FarmerLoan.objects.filter(farmer=farmer).order_by("-id")
    loan_count = loan_qs.count()
    if loan_count:
        loan_list_url = f"{reverse('farmer-loan-list')}?farmer={farmer.pk}"
        records = [
            _record(
                "loan",
                row.pk,
                f"Loan #{row.pk}",
                f"{row.get_status_display()} · {row.loan_amount}",
                reverse("farmer-loan-detail", kwargs={"pk": row.pk}),
                can_delete=can_delete,
            )
            for row in loan_qs[:BLOCKER_RECORD_LIMIT]
        ]
        blockers.append(
            {
                "key": "loans",
                "label": "loan" if loan_count == 1 else "loans",
                "count": loan_count,
                "url": loan_list_url,
                "records": records,
            }
        )

    payment_qs = FarmerPeriodPayment.objects.filter(farmer=farmer).order_by(
        "-period_end", "-id"
    )
    payment_count = payment_qs.count()
    if payment_count:
        detail_url = reverse("farmer-detail", kwargs={"pk": farmer.pk})
        records = [
            _record(
                "period_payment",
                row.pk,
                f"{row.period_start} – {row.period_end}",
                f"Paid {row.amount}",
                detail_url,
                can_delete=can_delete,
            )
            for row in payment_qs[:BLOCKER_RECORD_LIMIT]
        ]
        blockers.append(
            {
                "key": "period_payments",
                "label": "period payment" if payment_count == 1 else "period payments",
                "count": payment_count,
                "url": detail_url,
                "records": records,
            }
        )

    return blockers


def format_farmer_delete_blocked_payload(
    farmer, exc: ProtectedError | None = None, *, can_delete=False
) -> dict:
    """Plain + HTML delete-blocked message with listed related records."""
    name = getattr(farmer, "common_name", None) or getattr(farmer, "full_name", None) or "this farmer"
    blockers = farmer_delete_blockers(farmer, can_delete=can_delete)

    if not blockers:
        counts: Counter[str] = Counter()
        for obj in getattr(exc, "protected_objects", None) or []:
            meta = getattr(obj, "_meta", None)
            label = (
                str(meta.verbose_name_plural or meta.verbose_name or meta.model_name)
                if meta
                else "related records"
            )
            counts[label] += 1
        blockers = [
            {
                "key": label,
                "label": label,
                "count": count,
                "url": "",
                "records": [],
            }
            for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0].lower()))
        ]

    if not blockers:
        message = (
            f"Could not delete {name}. Related records may still exist "
            "(milk collections, loans, period payments, or milk factors)."
        )
        return {
            "error": message,
            "error_html": escape(message),
            "blockers": [],
            "can_delete": bool(can_delete),
            "farmer_id": farmer.pk,
        }

    plain_bits = [f"{b['count']} {b['label']}" for b in blockers]
    plain = f"Could not delete {name}. Related records still exist: {', '.join(plain_bits)}."

    summary_bits = []
    for blocker in blockers:
        if blocker.get("url"):
            summary_bits.append(
                format_html(
                    '<a href="{}" target="_blank" rel="noopener">{} {}</a>',
                    blocker["url"],
                    blocker["count"],
                    blocker["label"],
                )
            )
        else:
            summary_bits.append(format_html("{} {}", blocker["count"], blocker["label"]))

    rows_html = []
    for blocker in blockers:
        records = blocker.get("records") or []
        if not records:
            continue
        rows_html.append(
            format_html(
                '<div class="farmer-blocker-group"><div class="farmer-blocker-group__title">{}</div>',
                f"{blocker['count']} {blocker['label']}",
            )
        )
        for record in records:
            actions = format_html(
                '<a class="farmer-blocker-link" href="{}" target="_blank" rel="noopener">Open</a>',
                record["view_url"],
            )
            if record.get("can_delete"):
                actions = format_html(
                    '{} <button type="button" class="farmer-blocker-delete" data-kind="{}" data-id="{}">Delete</button>',
                    actions,
                    record["kind"],
                    record["id"],
                )
            rows_html.append(
                format_html(
                    '<div class="farmer-blocker-row">'
                    '<div class="farmer-blocker-row__main">'
                    '<div class="farmer-blocker-row__title">{}</div>'
                    '<div class="farmer-blocker-row__sub">{}</div>'
                    "</div>"
                    '<div class="farmer-blocker-row__actions">{}</div>'
                    "</div>",
                    record["title"],
                    record["subtitle"],
                    actions,
                )
            )
        if blocker["count"] > len(records):
            more = blocker["count"] - len(records)
            if blocker.get("url"):
                rows_html.append(
                    format_html(
                        '<div class="farmer-blocker-more"><a href="{}" target="_blank" rel="noopener">View {} more</a></div>',
                        blocker["url"],
                        more,
                    )
                )
            else:
                rows_html.append(format_html('<div class="farmer-blocker-more">+{} more</div>', more))
        rows_html.append(mark_safe("</div>"))

    hint = (
        "<div class=\"farmer-blocker-hint\">Delete the related records below, then try deleting the farmer again.</div>"
        if can_delete
        else ""
    )
    html = format_html(
        "Could not delete <strong>{}</strong>. Related records still exist: {}."
        "{}"
        '<div class="farmer-blocker-list">{}</div>',
        name,
        mark_safe(", ".join(str(bit) for bit in summary_bits)),
        mark_safe(hint),
        mark_safe("".join(str(row) for row in rows_html)),
    )
    return {
        "error": plain,
        "error_html": str(html),
        "blockers": blockers,
        "can_delete": bool(can_delete),
        "farmer_id": farmer.pk,
    }


def format_farmer_delete_blocked_message(
    farmer, exc: ProtectedError | None = None, *, can_delete=False
) -> str:
    """Human-readable reason a farmer delete was rejected."""
    return format_farmer_delete_blocked_payload(farmer, exc, can_delete=can_delete)["error"]


def delete_farmer_blocker_record(farmer, kind: str, record_id: int) -> tuple[bool, str]:
    """Superuser helper: delete one related record that blocks farmer deletion."""
    from farmer_loans.models import FarmerLoan
    from milk_collections.models import MilkCollection, MilkFactor
    from reports.models import FarmerPeriodPayment

    kind = (kind or "").strip()
    if kind == "milk_collection":
        obj = MilkCollection.objects.filter(pk=record_id, farmer=farmer).first()
        label = "milk collection"
    elif kind == "milk_factor":
        obj = MilkFactor.objects.filter(pk=record_id, farmer=farmer).first()
        label = "milk factor"
    elif kind == "loan":
        obj = FarmerLoan.objects.filter(pk=record_id, farmer=farmer).first()
        label = "loan"
    elif kind == "period_payment":
        obj = FarmerPeriodPayment.objects.filter(pk=record_id, farmer=farmer).first()
        label = "period payment"
    else:
        return False, "Unsupported related record type."

    if not obj:
        return False, "Related record not found for this farmer."

    try:
        obj.delete()
    except ProtectedError:
        return False, f"Could not delete this {label}; it is still referenced by other records."
    except Exception:
        return False, f"Could not delete this {label}."
    return True, f"Deleted {label}."
