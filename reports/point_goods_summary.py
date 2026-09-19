from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.utils import timezone

from branches.models import Branch
from masters.models import CollectionPoint
from reports.payslip_labels import collection_point_payslip_display_name
from suppliers.models import FarmerGoodsIssue

from .farmer_goods_summary import (
    _goods_issue_datetime_range,
    _product_rows_for_issues,
    _quantize_money,
)


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


class _CollectionPointResolver:
    """Resolve FarmerGoodsIssue.issue_to_id to a CollectionPoint."""

    def __init__(self, branch_ids):
        self.by_pk = {}
        self.by_branch_number = {}
        self.by_number = defaultdict(list)
        points = (
            CollectionPoint.objects.filter(route__branch_id__in=branch_ids)
            .select_related("route", "route__branch")
            .order_by("route__branch__code", "route__code", "number")
        )
        for point in points:
            self.by_pk[point.pk] = point
            try:
                number_key = int(str(point.number).strip())
            except (TypeError, ValueError):
                continue
            self.by_branch_number[(point.route.branch_id, number_key)] = point
            self.by_number[number_key].append(point)

    def resolve(self, issue_to_id, from_branch_id):
        point = self.by_pk.get(issue_to_id)
        if point:
            return point
        try:
            number_key = int(issue_to_id)
        except (TypeError, ValueError):
            return None
        point = self.by_branch_number.get((from_branch_id, number_key))
        if point:
            return point
        candidates = self.by_number.get(number_key) or []
        if len(candidates) == 1:
            return candidates[0]
        branch_matches = [p for p in candidates if p.route.branch_id == from_branch_id]
        if len(branch_matches) == 1:
            return branch_matches[0]
        return None


def build_branch_point_goods_summary_report(branch_ids, start, end):
    """Branch summary with collection points and all products issued to each point."""
    start_dt, end_dt = _goods_issue_datetime_range(start, end)
    issues_qs = (
        FarmerGoodsIssue.objects.filter(
            issue_to_type=FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
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
    resolver = _CollectionPointResolver(branch_ids)

    by_branch_point = defaultdict(lambda: defaultdict(list))
    unknown_by_branch = defaultdict(list)
    for issue in all_issues:
        point = resolver.resolve(issue.issue_to_id, issue.from_branch_id)
        if point:
            by_branch_point[issue.from_branch_id][point.id].append(issue)
        else:
            unknown_by_branch[issue.from_branch_id].append(issue)

    point_map = resolver.by_pk
    branch_sections = []
    grand_totals = _empty_totals()

    for branch_id in branch_ids:
        branch = branch_map.get(branch_id)
        if not branch:
            continue
        branch_totals = _empty_totals()
        point_sections = []
        point_issue_map = by_branch_point.get(branch_id, {})

        point_ids_sorted = sorted(
            point_issue_map.keys(),
            key=lambda pid: (
                (point_map[pid].route.code if point_map[pid].route_id else ""),
                str(point_map[pid].number),
            ),
        )
        for point_id in point_ids_sorted:
            point_issues = point_issue_map[point_id]
            point = point_map[point_id]
            product_rows, point_amount = _product_rows_for_issues(point_issues)
            point_totals = _empty_totals()
            _add_issue_totals(point_totals, point_issues, product_rows)
            _finalize_totals(point_totals)
            point_sections.append(
                {
                    "point": point,
                    "point_label": collection_point_payslip_display_name(point),
                    "route_label": f"{point.route.code} — {point.route.name}" if point.route_id else "—",
                    "product_rows": product_rows,
                    "totals": point_totals,
                    "amount": point_amount,
                }
            )
            for key in ("receipt_count", "line_count", "product_count"):
                branch_totals[key] += point_totals[key]
            branch_totals["quantity"] += point_totals["quantity"]
            branch_totals["amount"] += point_totals["amount"]

        unknown_issues = unknown_by_branch.get(branch_id, [])
        if unknown_issues:
            product_rows, unknown_amount = _product_rows_for_issues(unknown_issues)
            unknown_totals = _empty_totals()
            _add_issue_totals(unknown_totals, unknown_issues, product_rows)
            _finalize_totals(unknown_totals)
            point_sections.append(
                {
                    "point": None,
                    "point_label": "Unmapped collection point",
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
                "point_sections": point_sections,
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


def _branch_section_from_point_sections(branch, branch_label, point_sections):
    branch_totals = _empty_totals()
    for ps in point_sections:
        totals = ps.get("totals") or {}
        for key in ("receipt_count", "line_count", "product_count"):
            branch_totals[key] += totals.get(key) or 0
        branch_totals["quantity"] += totals.get("quantity") or Decimal("0.00")
        branch_totals["amount"] += totals.get("amount") or Decimal("0.00")
    _finalize_totals(branch_totals)
    return {
        "branch": branch,
        "branch_label": branch_label,
        "point_sections": point_sections,
        "totals": branch_totals,
    }


def filter_point_goods_branch_sections(branch_sections, *, route_id=None, point_id=None):
    if not route_id and not point_id:
        return branch_sections, _sum_branch_section_totals(branch_sections)

    filtered_sections = []
    for section in branch_sections:
        kept_points = []
        for ps in section.get("point_sections") or []:
            point = ps.get("point")
            if point_id:
                if point and point.id == point_id:
                    kept_points.append(ps)
            elif route_id:
                if point and point.route_id == route_id:
                    kept_points.append(ps)
        if not kept_points:
            continue
        filtered_sections.append(
            _branch_section_from_point_sections(
                section["branch"],
                section["branch_label"],
                kept_points,
            )
        )
    return filtered_sections, _sum_branch_section_totals(filtered_sections)


def build_route_point_goods_summary_report(route, start, end, point_id=None):
    """Issued farmer goods limited to collection points on one route."""
    if not route or not route.branch_id:
        return [], _empty_totals()

    branch_sections, _branch_grand = build_branch_point_goods_summary_report(
        [route.branch_id],
        start,
        end,
    )
    route_points_qs = CollectionPoint.objects.filter(route=route, is_deleted=False)
    if point_id:
        route_points_qs = route_points_qs.filter(pk=point_id)
    route_point_ids = set(route_points_qs.values_list("id", flat=True))
    if not route_point_ids:
        return [], _empty_totals()

    filtered_sections = []
    route_grand = _empty_totals()
    for section in branch_sections:
        point_sections = [
            ps
            for ps in section.get("point_sections") or []
            if ps.get("point") and ps["point"].id in route_point_ids
        ]
        if not point_sections:
            continue
        section_totals = _empty_totals()
        for ps in point_sections:
            totals = ps.get("totals") or {}
            for key in ("receipt_count", "line_count", "product_count"):
                section_totals[key] += totals.get(key) or 0
            section_totals["quantity"] += totals.get("quantity") or Decimal("0.00")
            section_totals["amount"] += totals.get("amount") or Decimal("0.00")
        _finalize_totals(section_totals)
        filtered_sections.append(
            {
                "branch": section["branch"],
                "branch_label": section["branch_label"],
                "point_sections": point_sections,
                "totals": section_totals,
            }
        )
        for key in ("receipt_count", "line_count", "product_count"):
            route_grand[key] += section_totals[key]
        route_grand["quantity"] += section_totals["quantity"]
        route_grand["amount"] += section_totals["amount"]

    _finalize_totals(route_grand)
    return filtered_sections, route_grand


def point_goods_summary_export_matrix(branch_sections, grand_totals, start, end):
    headers = [
        "Branch",
        "Route",
        "Collection point",
        "Product",
        "Category",
        "Qty",
        "Unit price",
        "Amount",
    ]
    matrix = [[f"Point goods summary — {start} to {end}"]]
    matrix.append(headers)
    for section in branch_sections:
        branch_label = section["branch_label"]
        for point_section in section["point_sections"]:
            for row in point_section["product_rows"]:
                matrix.append(
                    [
                        branch_label,
                        point_section["route_label"],
                        point_section["point_label"],
                        row["product_name"],
                        row["category_bucket"],
                        row["quantity"],
                        row["unit_price"],
                        row["amount"],
                    ]
                )
            totals = point_section["totals"]
            if totals["line_count"]:
                matrix.append(
                    [
                        branch_label,
                        point_section["route_label"],
                        f"{point_section['point_label']} total",
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
