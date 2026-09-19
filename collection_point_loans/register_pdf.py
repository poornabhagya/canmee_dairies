from decimal import Decimal
from xml.sax.saxutils import escape

from django.contrib.staticfiles import finders
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from canmee_dairies.formatting import format_money
from .models import CollectionPointLoan

INK = colors.HexColor("#111111")
MUTED = colors.HexColor("#333333")
GRID = colors.HexColor("#222222")
HEADER_BG = colors.HexColor("#f3f3f3")
FOOTER = colors.HexColor("#444444")
RULE = colors.HexColor("#888888")


def _logo_flowable():
    found = finders.find("img/logo.png") or finders.find("img/logo-128.png")
    if found:
        image = Image(str(found), width=12 * mm, height=12 * mm)
        image.hAlign = "LEFT"
        return image
    return Spacer(12 * mm, 12 * mm)


def _loan_status_label(loan):
    balance = getattr(loan, "balance_total", None)
    if balance is None:
        paid = getattr(loan, "paid_total", None) or Decimal("0.00")
        balance = (loan.loan_amount or Decimal("0.00")) - paid
    if loan.status == CollectionPointLoan.Status.APPROVED and balance <= 0:
        return "Settled"
    return loan.get_status_display()


def _p(text, style):
    cleaned = ("" if text is None else str(text)).replace("—", "-").replace("–", "-")
    return Paragraph(escape(cleaned), style)


def _point_label(loan):
    point = loan.collection_point
    if not point:
        return "-"
    number = (point.number or "").strip()
    name = (point.name or "").strip()
    if number and name:
        return f"{number} - {name}"
    return number or name or "-"


def _draw_register_footer(canvas, _doc):
    canvas.saveState()
    canvas.setStrokeColor(RULE)
    canvas.setLineWidth(0.4)
    y = 14 * mm
    canvas.line(14 * mm, y, A4[0] - 14 * mm, y)
    canvas.setFillColor(FOOTER)
    canvas.setFont("Helvetica-Oblique", 7)
    canvas.drawString(
        14 * mm,
        10 * mm,
        "This is a computer-generated register and does not require a signature.",
    )
    canvas.setFont("Helvetica", 7)
    canvas.drawRightString(A4[0] - 14 * mm, 10 * mm, "Canmee Dairies (Pvt) Ltd · Confidential")
    canvas.restoreState()


def build_collection_point_loan_register_pdf(buffer, *, loans, totals, status_label, branch_names):
    generated_at = timezone.localtime().strftime("%d %b %Y, %H:%M")
    count = totals["count"]
    noun = "loan" if count == 1 else "loans"
    subtitle_parts = [status_label or "All", f"{count} {noun}"]
    if branch_names:
        subtitle_parts.extend(branch_names)
    subtitle = " · ".join(subtitle_parts)

    company_style = ParagraphStyle(
        "RegisterCompany",
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=INK,
        alignment=TA_LEFT,
    )
    line_style = ParagraphStyle(
        "RegisterLine",
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=MUTED,
        alignment=TA_LEFT,
    )
    issued_label = ParagraphStyle(
        "RegisterIssuedLabel",
        fontName="Helvetica",
        fontSize=8,
        leading=11,
        textColor=MUTED,
        alignment=TA_RIGHT,
    )
    issued_value = ParagraphStyle(
        "RegisterIssuedValue",
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=12,
        textColor=INK,
        alignment=TA_RIGHT,
    )
    title_style = ParagraphStyle(
        "RegisterTitle",
        fontName="Helvetica-Bold",
        fontSize=14,
        leading=18,
        textColor=INK,
        alignment=TA_CENTER,
        spaceBefore=4,
        spaceAfter=1,
    )
    subtitle_style = ParagraphStyle(
        "RegisterSubtitle",
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=MUTED,
        alignment=TA_CENTER,
        spaceAfter=8,
    )
    section_style = ParagraphStyle(
        "RegisterSection",
        fontName="Helvetica-Bold",
        fontSize=10,
        leading=13,
        textColor=INK,
        spaceBefore=8,
        spaceAfter=4,
    )
    empty_style = ParagraphStyle(
        "RegisterEmpty",
        fontName="Helvetica-Oblique",
        fontSize=9,
        leading=12,
        textColor=MUTED,
    )
    cell_left = ParagraphStyle(
        "RegisterCellLeft",
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=INK,
        alignment=TA_LEFT,
    )
    cell_right = ParagraphStyle(
        "RegisterCellRight",
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=INK,
        alignment=TA_RIGHT,
    )
    head_left = ParagraphStyle(
        "RegisterHeadLeft",
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9,
        textColor=MUTED,
        alignment=TA_LEFT,
    )
    head_right = ParagraphStyle(
        "RegisterHeadRight",
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9,
        textColor=MUTED,
        alignment=TA_RIGHT,
    )
    foot_left = ParagraphStyle(
        "RegisterFootLeft",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=INK,
        alignment=TA_LEFT,
    )
    foot_right = ParagraphStyle(
        "RegisterFootRight",
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=INK,
        alignment=TA_RIGHT,
    )
    totals_head = ParagraphStyle(
        "RegisterTotalsHead",
        fontName="Helvetica-Bold",
        fontSize=7.5,
        leading=9,
        textColor=MUTED,
        alignment=TA_RIGHT,
    )
    totals_value = ParagraphStyle(
        "RegisterTotalsValue",
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=INK,
        alignment=TA_RIGHT,
    )
    totals_count = ParagraphStyle(
        "RegisterTotalsCount",
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=14,
        textColor=INK,
        alignment=TA_LEFT,
    )

    company_cell = [
        Paragraph("CANMEE DAIRIES (PVT) LTD", company_style),
        Paragraph("Phone: 071 543 5019", line_style),
    ]
    issued_cell = [
        Paragraph("Generated", issued_label),
        Paragraph(generated_at, issued_value),
    ]
    brand = Table(
        [[_logo_flowable(), company_cell, issued_cell]],
        colWidths=[16 * mm, 110 * mm, 56 * mm],
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
                Paragraph("LOANS", totals_head),
                Paragraph("LOAN AMOUNT", totals_head),
                Paragraph("PAID TOTAL", totals_head),
                Paragraph("OUTSTANDING BALANCE", totals_head),
            ],
            [
                _p(str(count), totals_count),
                _p(format_money(totals["loan_amount"], empty="-"), totals_value),
                _p(format_money(totals["paid"], empty="-"), totals_value),
                _p(format_money(totals["balance"], empty="-"), totals_value),
            ],
        ],
        colWidths=[45.5 * mm] * 4,
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

    elements = [
        brand,
        Spacer(1, 4 * mm),
        Paragraph("COLLECTION POINT LOAN REGISTER", title_style),
        _p(subtitle, subtitle_style),
        totals_table,
        Spacer(1, 3 * mm),
        HRFlowable(width="100%", thickness=2.25, color=INK, spaceBefore=0, spaceAfter=6),
        Paragraph("LOANS", section_style),
    ]

    if loans:
        rows = [
            [
                Paragraph("LOAN", head_left),
                Paragraph("BRANCH", head_left),
                Paragraph("COLLECTION POINT", head_left),
                Paragraph("AMOUNT", head_right),
                Paragraph("PAID", head_right),
                Paragraph("BALANCE", head_right),
                Paragraph("STATUS", head_left),
                Paragraph("INST.", head_right),
            ]
        ]
        for loan in loans:
            rows.append(
                [
                    _p(f"#{loan.pk}", cell_left),
                    _p(loan.branch.name if loan.branch_id else "-", cell_left),
                    _p(_point_label(loan), cell_left),
                    _p(format_money(loan.loan_amount, empty="-"), cell_right),
                    _p(format_money(getattr(loan, "paid_total", None), empty="-"), cell_right),
                    _p(format_money(getattr(loan, "balance_total", None), empty="-"), cell_right),
                    _p(_loan_status_label(loan), cell_left),
                    _p(loan.installment_count or "-", cell_right),
                ]
            )
        rows.append(
            [
                _p(f"Totals ({count})", foot_left),
                "",
                "",
                _p(format_money(totals["loan_amount"], empty="-"), foot_right),
                _p(format_money(totals["paid"], empty="-"), foot_right),
                _p(format_money(totals["balance"], empty="-"), foot_right),
                "",
                "",
            ]
        )
        loans_table = Table(
            rows,
            repeatRows=1,
            colWidths=[16 * mm, 28 * mm, 40 * mm, 24 * mm, 24 * mm, 24 * mm, 16 * mm, 10 * mm],
        )
        loans_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), HEADER_BG),
                    ("BACKGROUND", (0, -1), (-1, -1), HEADER_BG),
                    ("SPAN", (0, -1), (2, -1)),
                    ("SPAN", (6, -1), (7, -1)),
                    ("GRID", (0, 0), (-1, -1), 0.6, GRID),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        elements.append(loans_table)
    else:
        elements.append(Paragraph("No loans in this filter.", empty_style))

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=12 * mm,
        bottomMargin=18 * mm,
        title="Collection point loan register",
        author="Canmee Dairies (Pvt) Ltd",
    )
    doc.build(elements, onFirstPage=_draw_register_footer, onLaterPages=_draw_register_footer)
    return buffer
