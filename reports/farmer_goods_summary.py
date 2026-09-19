from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from branches.models import Branch
from masters.models import Farmer
from reports.payslip_labels import farmer_payslip_display_name
from suppliers.models import FarmerGoodsIssue
from suppliers.services import summarize_priced_farmer_goods_issues


def _goods_issue_datetime_range(start, end):
    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start, time.min), tz)
    end_dt = timezone.make_aware(datetime.combine(end + timedelta(days=1), time.min), tz)
    return start_dt, end_dt


def _quantize_money(value):
    return (value or Decimal("0")).quantize(Decimal("0.01"))


def _empty_totals():
    return {
        "receipt_count": 0,
        "line_count": 0,
        "product_count": 0,
        "quantity": Decimal("0.00"),
        "amount": Decimal("0.00"),
    }


def _finalize_totals(target):
    target["quantity"] = _quantize_money(target["quantity"])
    target["amount"] = _quantize_money(target["amount"])
    return target


def _add_issue_totals(target, issues, product_rows):
    batch_refs = {issue.batch_ref for issue in issues if issue.batch_ref}
    target["receipt_count"] += len(batch_refs)
    target["line_count"] += len(issues)
    target["product_count"] += len(product_rows)
    for row in product_rows:
        target["quantity"] += row["quantity"]
        target["amount"] += row["amount"]


def _farmer_label(farmer):
    if not farmer:
        return "—"
    reg = (farmer.registration_number or "").strip()
    name = farmer_payslip_display_name(farmer) or (farmer.common_name or farmer.full_name or "").strip()
    if reg and name:
        return f"{reg} — {name}"
    return reg or name or f"Farmer #{farmer.pk}"


def _farmer_route_label(farmer):
    if not farmer:
        return "—"
    route = farmer.route
    if not route and farmer.collection_point_id:
        route = getattr(farmer.collection_point, "route", None)
    if not route:
        return "—"
    return f"{route.code} — {route.name}"


def product_price_group_key(product_name, unit_price):
    """Group summary lines by product and actual issue price (not a blended average)."""
    name = (product_name or "").strip() or "—"
    price = (unit_price or Decimal("0")).quantize(Decimal("0.01"))
    return (name, price)


def _product_rows_for_issues(issues):
    priced_items, total, *_rest = summarize_priced_farmer_goods_issues(issues)
    by_key = defaultdict(
        lambda: {
            "quantity": Decimal("0"),
            "amount": Decimal("0"),
            "product_name": "",
            "unit_price": Decimal("0.00"),
            "category_bucket": "",
            "receipt_lines": [],
        }
    )
    for item in priced_items:
        name = (item.get("product_name") or "").strip() or "—"
        unit_price = (item.get("unit_price") or Decimal("0")).quantize(Decimal("0.01"))
        key = product_price_group_key(name, unit_price)
        by_key[key]["product_name"] = name
        by_key[key]["unit_price"] = unit_price
        by_key[key]["quantity"] += item.get("quantity") or Decimal("0")
        by_key[key]["amount"] += item.get("amount") or Decimal("0")
        by_key[key]["category_bucket"] = item.get("category_bucket") or ""
        by_key[key]["receipt_lines"].append(
            {
                "issue_id": item.get("issue_id"),
                "batch_ref": item.get("batch_ref") or "",
                "date": item.get("date"),
                "quantity": item.get("quantity") or Decimal("0"),
                "unit_price": unit_price,
                "line_amount": item.get("amount") or Decimal("0"),
                "is_settled": bool(item.get("is_settled")),
            }
        )

    rows = []
    for data in by_key.values():
        receipt_lines = data.get("receipt_lines") or []
        unsettled_lines = [line for line in receipt_lines if not line.get("is_settled")]
        rows.append(
            {
                "product_name": data["product_name"],
                "category_bucket": data.get("category_bucket") or "",
                "quantity": data["quantity"].quantize(Decimal("0.01")),
                "unit_price": data["unit_price"],
                "amount": data["amount"].quantize(Decimal("0.01")),
                "receipt_lines": receipt_lines,
                "receipt_line_count": len(receipt_lines),
                "unsettled_lines": unsettled_lines,
                "unsettled_count": len(unsettled_lines),
            }
        )
    rows.sort(key=lambda row: (row["product_name"].lower(), row["unit_price"]))
    return rows, total.quantize(Decimal("0.01"))


def build_branch_farmer_goods_summary_report(branch_ids, start, end):
    """Branch summary with farmers and all products issued to each farmer."""
    start_dt, end_dt = _goods_issue_datetime_range(start, end)
    issues_qs = (
        FarmerGoodsIssue.objects.filter(
            issue_to_type=FarmerGoodsIssue.IssueToType.FARMER,
            from_branch_id__in=branch_ids,
            batch_ref__gt="",
            date__gte=start_dt,
            date__lt=end_dt,
        )
        .exclude(driver_status=FarmerGoodsIssue.DriverStatus.REJECTED)
        .select_related("product", "from_branch")
        .order_by("from_branch__code", "-date", "batch_ref", "id")
    )
    all_issues = list(issues_qs)
    branch_map = {b.id: b for b in Branch.objects.filter(id__in=branch_ids).order_by("code", "name")}

    farmer_ids = {issue.issue_to_id for issue in all_issues}
    farmer_map = {
        f.id: f
        for f in Farmer.objects.filter(id__in=farmer_ids).select_related(
            "route", "branch", "collection_point", "collection_point__route"
        )
    }

    by_branch_farmer = defaultdict(lambda: defaultdict(list))
    unknown_by_branch = defaultdict(list)
    for issue in all_issues:
        farmer = farmer_map.get(issue.issue_to_id)
        if farmer:
            by_branch_farmer[issue.from_branch_id][farmer.id].append(issue)
        else:
            unknown_by_branch[issue.from_branch_id].append(issue)

    branch_sections = []
    grand_totals = _empty_totals()

    for branch_id in branch_ids:
        branch = branch_map.get(branch_id)
        if not branch:
            continue
        branch_totals = _empty_totals()
        farmer_sections = []
        farmer_issue_map = by_branch_farmer.get(branch_id, {})

        farmer_ids_sorted = sorted(
            farmer_issue_map.keys(),
            key=lambda fid: (
                (farmer_map[fid].route.code if farmer_map[fid].route_id else ""),
                (farmer_map[fid].registration_number or ""),
                (farmer_map[fid].common_name or farmer_map[fid].full_name or ""),
            ),
        )
        for farmer_id in farmer_ids_sorted:
            farmer_issues = farmer_issue_map[farmer_id]
            farmer = farmer_map[farmer_id]
            product_rows, farmer_amount = _product_rows_for_issues(farmer_issues)
            farmer_totals = _empty_totals()
            _add_issue_totals(farmer_totals, farmer_issues, product_rows)
            _finalize_totals(farmer_totals)
            farmer_sections.append(
                {
                    "farmer": farmer,
                    "farmer_label": _farmer_label(farmer),
                    "route_label": _farmer_route_label(farmer),
                    "product_rows": product_rows,
                    "totals": farmer_totals,
                    "amount": farmer_amount,
                }
            )
            for key in ("receipt_count", "line_count", "product_count"):
                branch_totals[key] += farmer_totals[key]
            branch_totals["quantity"] += farmer_totals["quantity"]
            branch_totals["amount"] += farmer_totals["amount"]

        unknown_issues = unknown_by_branch.get(branch_id, [])
        if unknown_issues:
            product_rows, unknown_amount = _product_rows_for_issues(unknown_issues)
            unknown_totals = _empty_totals()
            _add_issue_totals(unknown_totals, unknown_issues, product_rows)
            _finalize_totals(unknown_totals)
            farmer_sections.append(
                {
                    "farmer": None,
                    "farmer_label": "Unmapped farmer",
                    "route_label": "—",
                    "product_rows": product_rows,
                    "totals": unknown_totals,
                    "amount": unknown_amount,
                }
            )
            for key in ("receipt_count", "line_count", "product_count"):
                branch_totals[key] += unknown_totals[key]
            branch_totals["quantity"] += unknown_totals["quantity"]
            branch_totals["amount"] += unknown_totals["amount"]

        _finalize_totals(branch_totals)
        branch_sections.append(
            {
                "branch": branch,
                "branch_label": f"{branch.code} — {branch.name}",
                "farmer_sections": farmer_sections,
                "totals": branch_totals,
            }
        )
        for key in ("receipt_count", "line_count", "product_count"):
            grand_totals[key] += branch_totals[key]
        grand_totals["quantity"] += branch_totals["quantity"]
        grand_totals["amount"] += branch_totals["amount"]

    _finalize_totals(grand_totals)
    return branch_sections, grand_totals


def _sum_branch_section_totals(branch_sections):
    grand_totals = _empty_totals()
    for section in branch_sections:
        branch_totals = section.get("totals") or {}
        for key in ("receipt_count", "line_count", "product_count"):
            grand_totals[key] += branch_totals.get(key) or 0
        grand_totals["quantity"] += branch_totals.get("quantity") or Decimal("0.00")
        grand_totals["amount"] += branch_totals.get("amount") or Decimal("0.00")
    return _finalize_totals(grand_totals)


def _branch_section_from_farmer_sections(branch, branch_label, farmer_sections):
    branch_totals = _empty_totals()
    for fs in farmer_sections:
        totals = fs.get("totals") or {}
        for key in ("receipt_count", "line_count", "product_count"):
            branch_totals[key] += totals.get(key) or 0
        branch_totals["quantity"] += totals.get("quantity") or Decimal("0.00")
        branch_totals["amount"] += totals.get("amount") or Decimal("0.00")
    _finalize_totals(branch_totals)
    return {
        "branch": branch,
        "branch_label": branch_label,
        "farmer_sections": farmer_sections,
        "totals": branch_totals,
    }


def filter_farmer_goods_branch_sections(branch_sections, *, route_id=None, farmer_id=None):
    if not route_id and not farmer_id:
        return branch_sections, _sum_branch_section_totals(branch_sections)

    filtered_sections = []
    for section in branch_sections:
        kept_farmers = []
        for fs in section.get("farmer_sections") or []:
            farmer = fs.get("farmer")
            if farmer_id:
                if farmer and farmer.id == farmer_id:
                    kept_farmers.append(fs)
            elif route_id:
                if not farmer:
                    continue
                farmer_route_id = farmer.route_id
                if not farmer_route_id and farmer.collection_point_id:
                    farmer_route_id = getattr(farmer.collection_point, "route_id", None)
                if farmer_route_id == route_id:
                    kept_farmers.append(fs)
        if not kept_farmers:
            continue
        filtered_sections.append(
            _branch_section_from_farmer_sections(
                section["branch"],
                section["branch_label"],
                kept_farmers,
            )
        )
    return filtered_sections, _sum_branch_section_totals(filtered_sections)


def farmer_goods_summary_export_matrix(branch_sections, grand_totals, start, end):
    headers = [
        "Branch",
        "Route",
        "Farmer",
        "Product",
        "Category",
        "Qty",
        "Unit price",
        "Amount",
    ]
    matrix = [[f"Farmer goods summary — {start} to {end}"]]
    matrix.append(headers)
    for section in branch_sections:
        branch_label = section["branch_label"]
        for farmer_section in section["farmer_sections"]:
            for row in farmer_section["product_rows"]:
                matrix.append(
                    [
                        branch_label,
                        farmer_section["route_label"],
                        farmer_section["farmer_label"],
                        row["product_name"],
                        row["category_bucket"],
                        row["quantity"],
                        row["unit_price"],
                        row["amount"],
                    ]
                )
            totals = farmer_section["totals"]
            if totals["line_count"]:
                matrix.append(
                    [
                        branch_label,
                        farmer_section["route_label"],
                        f"{farmer_section['farmer_label']} total",
                        "",
                        "",
                        totals["quantity"],
                        "",
                        totals["amount"],
                    ]
                )
        branch_totals = section["totals"]
        if branch_totals["line_count"]:
            matrix.append(
                [
                    f"{branch_label} total",
                    "",
                    "",
                    "",
                    "",
                    branch_totals["quantity"],
                    "",
                    branch_totals["amount"],
                ]
            )
    if grand_totals["line_count"]:
        matrix.append(
            [
                "Grand total",
                "",
                "",
                "",
                "",
                grand_totals["quantity"],
                "",
                grand_totals["amount"],
            ]
        )
    return matrix
