from decimal import Decimal
from itertools import groupby
from xml.sax.saxutils import escape

from django.contrib.staticfiles import finders
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from canmee_dairies.formatting import format_money

INK = colors.HexColor("#111111")
MUTED = colors.HexColor("#333333")
GRID = colors.HexColor("#222222")
HEADER_BG = colors.HexColor("#f3f3f3")
SECTION_BG = colors.HexColor("#e8eef5")
FOOTER = colors.HexColor("#444444")
RULE = colors.HexColor("#888888")
PAGE = landscape(A4)


def _logo_flowable():
    found = finders.find("img/logo.png") or finders.find("img/logo-128.png")
    if found:
        image = Image(str(found), width=12 * mm, height=12 * mm)
        image.hAlign = "LEFT"
        return image
    return Spacer(12 * mm, 12 * mm)


def _p(text, style):
    cleaned = ("" if text is None else str(text)).replace("—", "-").replace("–", "-")
    return Paragraph(escape(cleaned), style)


def _money(value):
    return format_money(value or Decimal("0.00"), empty="0.00")


def _draw_register_footer(canvas, _doc):
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    y = 12 * mm
    canvas.line(12 * mm, y, PAGE[0] - 12 * mm, y)
    canvas.setFillColor(FOOTER)
    canvas.setFont("Helvetica-Oblique", 7)
    canvas.drawString(
        12 * mm,
        8 * mm,
        "This is a computer-generated fixed asset register and does not require a signature.",
    )
    canvas.setFont("Helvetica", 7)
    canvas.drawRightString(PAGE[0] - 12 * mm, 8 * mm, "Canmee Dairies (Pvt) Ltd · Confidential")
    canvas.restoreState()


def classified_asset_groups(assets):
    groups = []
    rows = list(assets)
    for _key, items in groupby(rows, key=lambda a: a.asset_type_id or 0):
        chunk = list(items)
        cost = sum((a.purchase_cost or Decimal("0.00")) for a in chunk)
        book = sum((a.current_value or Decimal("0.00")) for a in chunk)
        written = cost - book if cost > book else Decimal("0.00")
        type_obj = chunk[0].asset_type
        groups.append(
            {
                "name": type_obj.name if type_obj else "Unclassified",
                "assets": chunk,
                "count": len(chunk),
                "cost": cost,
                "book": book,
                "written": written,
            }
        )
    return groups


def classified_register_queryset(qs):
    return qs.select_related(
        "asset_type", "category", "branch", "location", "custodian"
    ).order_by("asset_type__sort_order", "asset_type__name", "asset_tag")


def build_fixed_asset_register_pdf(buffer, *, assets, stats):
    generated_at = timezone.localtime().strftime("%d %b %Y, %H:%M")
    as_at = timezone.localtime().strftime("%d %b %Y")
    groups = classified_asset_groups(assets)
    count = stats.get("total") or 0

    company_style = ParagraphStyle(
        "FarCompany", fontName="Helvetica-Bold", fontSize=11, leading=14, textColor=INK
    )
    line_style = ParagraphStyle(
        "FarLine", fontName="Helvetica", fontSize=8, leading=11, textColor=MUTED
    )
    issued_label = ParagraphStyle(
        "FarIssuedLabel",
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=MUTED,
        alignment=TA_RIGHT,
    )
    issued_value = ParagraphStyle(
        "FarIssuedValue",
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=12,
        textColor=INK,
        alignment=TA_RIGHT,
    )
    title_style = ParagraphStyle(
        "FarTitle",
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        textColor=INK,
        alignment=TA_CENTER,
        spaceBefore=4,
        spaceAfter=1,
    )
    subtitle_style = ParagraphStyle(
        "FarSubtitle",
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=MUTED,
        alignment=TA_CENTER,
        spaceAfter=8,
    )
    empty_style = ParagraphStyle(
        "FarEmpty", fontName="Helvetica-Oblique", fontSize=9, leading=12, textColor=MUTED
    )
    cell_left = ParagraphStyle(
        "FarCellLeft", fontName="Helvetica", fontSize=7.5, leading=9.5, textColor=INK
    )
    cell_right = ParagraphStyle(
        "FarCellRight",
        fontName="Helvetica",
        fontSize=7.5,
        leading=9.5,
        textColor=INK,
        alignment=TA_RIGHT,
    )
    head_left = ParagraphStyle(
        "FarHeadLeft",
        fontName="Helvetica-Bold",
        fontSize=7,
        leading=9,
        textColor=MUTED,
        alignment=TA_LEFT,
    )
    head_right = ParagraphStyle(
        "FarHeadRight",
        fontName="Helvetica-Bold",
        fontSize=7,
        leading=9,
        textColor=MUTED,
        alignment=TA_RIGHT,
    )
    section_style = ParagraphStyle(
        "FarSection",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=INK,
    )
    foot_left = ParagraphStyle(
        "FarFootLeft", fontName="Helvetica-Bold", fontSize=8, leading=10, textColor=INK
    )
    foot_right = ParagraphStyle(
        "FarFootRight",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=INK,
        alignment=TA_RIGHT,
    )
    totals_head = ParagraphStyle(
        "FarTotalsHead",
        fontName="Helvetica-Bold",
        fontSize=7,
        leading=9,
        textColor=MUTED,
        alignment=TA_RIGHT,
    )
    totals_value = ParagraphStyle(
        "FarTotalsValue",
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=INK,
        alignment=TA_RIGHT,
    )
    totals_count = ParagraphStyle(
        "FarTotalsCount",
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=INK,
        alignment=TA_LEFT,
    )

    brand = Table(
        [
            [
                _logo_flowable(),
                [
                    Paragraph("CANMEE DAIRIES (PVT) LTD", company_style),
                    Paragraph("Phone: 071 543 5019", line_style),
                ],
                [
                    Paragraph("Generated", issued_label),
                    Paragraph(generated_at, issued_value),
                ],
            ]
        ],
        colWidths=[16 * mm, 180 * mm, 77 * mm],
    )
    brand.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (0, 0), 3 * mm),
            ]
        )
    )

    totals_table = Table(
        [
            [
                Paragraph("ASSETS", totals_head),
                Paragraph("GROSS COST", totals_head),
                Paragraph("WRITTEN DOWN", totals_head),
                Paragraph("NET BOOK VALUE", totals_head),
            ],
            [
                _p(str(count), totals_count),
                _p(_money(stats.get("total_cost")), totals_value),
                _p(_money(stats.get("written_down")), totals_value),
                _p(_money(stats.get("total_value")), totals_value),
            ],
        ],
        colWidths=[68.25 * mm] * 4,
    )
    totals_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                ("LINEABOVE", (0, 0), (-1, 0), 1.5, INK),
                ("LINEBELOW", (0, -1), (-1, -1), 1.2, INK),
                ("GRID", (0, 0), (-1, -1), 0.6, GRID),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )

    col_widths = [
        10 * mm,
        24 * mm,
        52 * mm,
        38 * mm,
        32 * mm,
        28 * mm,
        20 * mm,
        23 * mm,
        23 * mm,
        23 * mm,
    ]
    header_row = [
        Paragraph("#", head_left),
        Paragraph("TAG", head_left),
        Paragraph("PARTICULARS", head_left),
        Paragraph("CATEGORY", head_left),
        Paragraph("BRANCH", head_left),
        Paragraph("LOCATION", head_left),
        Paragraph("STATUS", head_left),
        Paragraph("COST", head_right),
        Paragraph("WRITTEN DOWN", head_right),
        Paragraph("NBV", head_right),
    ]

    elements = [
        brand,
        Spacer(1, 4 * mm),
        Paragraph("FIXED ASSET REGISTER", title_style),
        _p(f"Classified register as at {as_at}  ·  FAR-{timezone.localtime().year}", subtitle_style),
        totals_table,
        Spacer(1, 3 * mm),
        HRFlowable(width="100%", thickness=2.25, color=INK, spaceBefore=0, spaceAfter=6),
    ]

    if not groups:
        elements.append(Paragraph("No assets on the register.", empty_style))
    else:
        seq = 0
        for group in groups:
            elements.append(
                Paragraph(
                    f"{group['name'].upper()}  ·  {group['count']} asset"
                    + ("" if group["count"] == 1 else "s"),
                    section_style,
                )
            )
            rows = [header_row]
            for asset in group["assets"]:
                seq += 1
                rows.append(
                    [
                        _p(str(seq), cell_left),
                        _p(asset.asset_tag, cell_left),
                        _p(asset.name, cell_left),
                        _p(asset.category.name if asset.category_id else "-", cell_left),
                        _p(asset.branch_label, cell_left),
                        _p(asset.location.name if asset.location_id else "-", cell_left),
                        _p(asset.get_status_display(), cell_left),
                        _p(_money(asset.purchase_cost), cell_right),
                        _p(_money(asset.written_down), cell_right),
                        _p(_money(asset.current_value), cell_right),
                    ]
                )
            rows.append(
                [
                    _p(f"Subtotal — {group['name']} ({group['count']})", foot_left),
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    _p(_money(group["cost"]), foot_right),
                    _p(_money(group["written"]), foot_right),
                    _p(_money(group["book"]), foot_right),
                ]
            )
            table = Table(rows, repeatRows=1, colWidths=col_widths)
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                        ("BACKGROUND", (0, -1), (-1, -1), HEADER_BG),
                        ("SPAN", (0, -1), (6, -1)),
                        ("GRID", (0, 0), (-1, -1), 0.6, GRID),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 3),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                        ("TOPPADDING", (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ]
                )
            )
            elements.append(table)
            elements.append(Spacer(1, 3 * mm))

        grand = Table(
            [
                [
                    _p(f"Grand total ({count})", foot_left),
                    _p(_money(stats.get("total_cost")), foot_right),
                    _p(_money(stats.get("written_down")), foot_right),
                    _p(_money(stats.get("total_value")), foot_right),
                ]
            ],
            colWidths=[181 * mm, 23 * mm, 23 * mm, 23 * mm],
        )
        grand.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, -1), HEADER_BG),
                    ("GRID", (0, 0), (-1, -1), 0.6, GRID),
                    ("LINEABOVE", (0, 0), (-1, 0), 1.2, INK),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
            )
        )
        elements.append(grand)

    doc = SimpleDocTemplate(
        buffer,
        pagesize=PAGE,
        leftMargin=12 * mm,
        rightMargin=12 * mm,
        topMargin=10 * mm,
        bottomMargin=16 * mm,
        title="Fixed asset register",
        author="Canmee Dairies (Pvt) Ltd",
    )
    doc.build(elements, onFirstPage=_draw_register_footer, onLaterPages=_draw_register_footer)
    return buffer
