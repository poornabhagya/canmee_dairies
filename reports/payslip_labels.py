"""Payment sheet print labels (English / Sinhala)."""

import re

_SI_MONTHS = (
    "",
    "ජනවාරි",
    "පෙබරවාරි",
    "මාර්තු",
    "අප්‍රේල්",
    "මැයි",
    "ජූනි",
    "ජූලි",
    "අගෝස්තු",
    "සැප්තැම්බර්",
    "ඔක්තෝබර්",
    "නොවැම්බර්",
    "දෙසැම්බර්",
)


def payment_sheet_period_english(start, end):
    start_month = start.strftime("%B").upper()
    if start.year == end.year and start.month == end.month:
        return f"{start.year} {start_month} {start.day}-{end.day}"
    end_month = end.strftime("%B").upper()
    if start.year == end.year:
        return f"{start.year} {start_month} {start.day} - {end_month} {end.day}"
    return f"{start.day} {start_month} {start.year} - {end.day} {end_month} {end.year}"


def payment_sheet_period_sinhala(start, end):
    start_month = _SI_MONTHS[start.month]
    if start.year == end.year and start.month == end.month:
        return f"{start.year} {start_month} {start.day}-{end.day}"
    end_month = _SI_MONTHS[end.month]
    if start.year == end.year:
        return f"{start.year} {start_month} {start.day} - {end_month} {end.day}"
    return f"{start.day} {start_month} {start.year} - {end.day} {end_month} {end.year}"


def payment_sheet_period_title_sinhala(start, end):
    return payment_sheet_period_sinhala(start, end)


def collection_point_payslip_display_name(point):
    return f"{point.number} — {point.name}"


_BRANCH_NAME_SI = {
    "nawagaththegama": "නවගත්තේගම",
    "nawagattegama": "නවගත්තේගම",
    "anamaduwa": "ආණමඩුව",
    "gampaha": "ගම්පහ",
}


def _branch_name_lookup_key(name) -> str:
    return re.sub(r"[\s\-_]+", "", str(name or "").strip().lower())


def branch_name_sinhala(name) -> str:
    """English branch name → Sinhala for payslip / print."""
    text = str(name or "").strip()
    if not text:
        return ""
    mapped = _BRANCH_NAME_SI.get(_branch_name_lookup_key(text))
    return mapped if mapped else text


def branch_payslip_name(branch, *, lang="en") -> str:
    if branch is None:
        return ""
    name = branch.name if hasattr(branch, "name") else str(branch)
    name = str(name or "").strip()
    if lang == "si":
        return branch_name_sinhala(name)
    return name


def farmer_payslip_display_name(farmer):
    """Format as INITIAL (Common name), e.g. M. K. C. L. DAYARATHNE (Chathura)."""
    initial = (farmer.initial or "").strip().upper()
    common = (farmer.common_name or "").strip()
    if initial and common:
        return f"{initial} ({common})"
    if common:
        return common
    return (farmer.full_name or "").strip()


PAYSLIP_LABELS_EN = {
    "lang": "en",
    "company": "CANMEE DAIRIES (PVT) LTD",
    "phone": "Phone : 0715435019",
    "farmer": "Farmer",
    "date": "Date",
    "ltr": "Ltr",
    "rate": "Rate",
    "amount": "Amount",
    "total_ltrs": "Total ltrs",
    "total_payment": "Total payment",
    "feed_deduction": "Cattle feed deduction",
    "vitamin": "Vitamin",
    "omi": "Omi",
    "minerals": "Minerals",
    "other_goods": "Other Farmer Goods",
    "advance": "Advance",
    "loan": "Bank Loan",
    "balance_payment": "Balance payment",
    "loan_amount": "Loan amount",
    "paid": "Paid",
    "loan_balance": "Loan balance",
    "footer": "Higher price for fresh milk",
    "authorized_signature": "Authorized signature",
}

POINT_PAYSLIP_LABELS_EN = {
    **PAYSLIP_LABELS_EN,
    "address": "MILK CHILLING CENTER - ",
    "collecting_point_name": "Collecting point name",
    "no": "No",
    "name": "Name",
    "kg": "Kg",
    "ltr": "Ltr",
    "fat": "Fat",
    "snf": "Snf",
    "total": "Total",
    "deduction": "Deduction",
    "goods_deduction": "Goods deduction",
    "goods_deduction_attachment": "Goods deduction summary",
    "attachment": "Attachment",
    "product": "Product",
    "collector_fee": "Collector fee",
    "additional": "Additional",
    "previous_outstanding": "Previous outstanding",
    "payment_correction": "Payment corrections",
    "point": "Collection point",
    "qty": "Qty",
    "collector_fee_rate": "Fee / unit",
    "farmer_gross": "Farmer gross (linked)",
    "total_collector_fee": "Total collector fee",
    "authorized_by": "Authorized by",
    "signature": "Signature",
}

PAYSLIP_LABELS_SI = {
    "lang": "si",
    "company": "කැන්මී ඩේරීස් පුද්ගලික සමාගම",
    "phone": "දු. අංකය : 0715435019",
    "farmer": "ගොවි මහතාගේ නම",
    "date": "දිනය",
    "ltr": "ලීටර්",
    "rate": "මිල",
    "amount": "මුදල",
    "total_ltrs": "මුළු ලීටර්",
    "total_payment": "මුළු ගෙවීම",
    "feed_deduction": "ගව ආහාර අඩු කිරීම",
    "vitamin": "විටමින්",
    "omi": "ඕමි",
    "minerals": "ඛනිජ",
    "other_goods": "වෙනත් ගොවි වස්තු",
    "advance": "අත්තිකාරම්",
    "loan": "බැංකු ණය",
    "balance_payment": "ශේෂ ගෙවීම",
    "loan_amount": "ණය මුදල",
    "paid": "ගෙවූ මුදල",
    "loan_balance": "ණය ශේෂය",
    "footer": "නැවුම් කිරි සඳහා ඉහළ මිලක්",
    "authorized_signature": "අනුමත අත්සන",
}

POINT_PAYSLIP_LABELS_SI = {
    **PAYSLIP_LABELS_SI,
    "address": "කිරි ශීත කිරීමේ මධ්‍යස්ථානය - ",
    "collecting_point_name": "එකතු කිරීමේ මධ්‍යස්ථානයේ නම",
    "no": "අංකය",
    "name": "නම",
    "kg": "කි.ග්‍රෑ.",
    "ltr": "ලීටර්",
    "fat": "මේදය",
    "snf": "SNF",
    "total": "මුළු",
    "deduction": "අඩු කිරීම",
    "goods_deduction": "භාණ්ඩ සඳහා අඩු කිරීම",
    "goods_deduction_attachment": "භාණ්ඩ අඩු කිරීමේ සාරාංශය",
    "attachment": "ඇමුණුම",
    "product": "නිෂ්පාදනය",
    "collector_fee": "එකතු කිරීමේ ගාස්තු",
    "additional": "අමතර",
    "previous_outstanding": "පෙර හිඟය",
    "payment_correction": "ගෙවීම් නිවැරදි කිරීම්",
    "point": "එකතු කිරීමේ ස්ථානය",
    "qty": "ප්‍රමාණය",
    "collector_fee_rate": "ඒකකයකට ගාස්තුව",
    "farmer_gross": "සම්බන්ධ ගොවි මුළු ගෙවීම",
    "total_collector_fee": "මුළු එකතුකරු ගාස්තුව",
    "authorized_by": "අනුමත කළේ",
    "signature": "අත්සන",
}
