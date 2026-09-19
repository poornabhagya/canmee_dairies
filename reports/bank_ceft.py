"""CEFT-style bank payment file helpers for collection point payments."""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from django.conf import settings

CEFT_HEADERS = [
    "Record Identifier ",
    "Debit Account No ",
    "Beneficiary Name ",
    "Beneficiary Bank Code ",
    "Beneficiary Branch Code ",
    "Beneficiary Account No ",
    "Payment Amount ",
    "Payment Value Date ",
    "Customer Reference No ",
    "Payment Product Code ",
    "credit Narration",
    "Reason For Payment ",
]

CEFT_SHEET_NAME = "PAYMENT_COMMON_UPLOAD"
CEFT_PRODUCT_CODE = "CEFT "


def default_debit_account_no():
    """Prefer primary company bank account; fall back to settings / sample default."""
    try:
        from masters.models import CompanyProfile

        account = CompanyProfile.get_solo().primary_bank_account
        if account and account.account_number:
            return str(account.account_number).strip()
    except Exception:
        pass
    return str(getattr(settings, "CEFT_DEBIT_ACCOUNT_NO", "") or "230020113992").strip()


def resolve_debit_account_no(*, account_id=None, account_no=None):
    """Resolve Debit Account No from company account id, explicit number, or default."""
    raw_no = str(account_no or "").strip()
    if raw_no:
        return "".join(ch for ch in raw_no if ch.isalnum()) or raw_no
    try:
        account_pk = int(str(account_id or "").strip())
    except (TypeError, ValueError):
        account_pk = None
    if account_pk:
        try:
            from masters.models import CompanyBankAccount

            account = CompanyBankAccount.objects.filter(
                pk=account_pk,
                is_active=True,
                is_deleted=False,
            ).first()
            if account and account.account_number:
                return str(account.account_number).strip()
        except Exception:
            pass
    return default_debit_account_no()


def company_debit_accounts():
    """Active company debit accounts for the CEFT picker (primary first)."""
    try:
        from masters.models import CompanyBankAccount, CompanyProfile

        company = CompanyProfile.get_solo()
        return list(
            CompanyBankAccount.objects.filter(
                company_id=company.pk,
                is_active=True,
                is_deleted=False,
            ).order_by("-is_primary", "id")
        )
    except Exception:
        return []


def format_ceft_value_date(value=None):
    """CEFT Payment Value Date as DDMMYY. Accepts a date or YYYY-MM-DD / DDMMYY text."""
    if value is None:
        day = date.today()
    elif isinstance(value, datetime):
        day = value.date()
    elif isinstance(value, date):
        day = value
    else:
        text = str(value).strip()
        if re.fullmatch(r"\d{6}", text):
            return text
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text[:10]):
            day = datetime.strptime(text[:10], "%Y-%m-%d").date()
        else:
            day = date.today()
    return day.strftime("%d%m%y")


def format_ceft_amount(amount):
    value = Decimal(amount or 0).quantize(Decimal("0.01"))
    return f"{value:.2f}"


def sanitize_ceft_token(value, fallback="PAY"):
    text = re.sub(r"[^A-Za-z0-9]+", "_", str(value or "").strip().upper()).strip("_")
    return text[:40] or fallback


def build_customer_reference(branch_name, start, end):
    branch = sanitize_ceft_token(branch_name, "BRANCH")[:12]
    month = (end or start or date.today()).strftime("%b").upper()
    return f"CANMEE_MILK_{branch}_{month}"[:35]


def build_credit_narration(point_number, point_name=None):
    number = sanitize_ceft_token(point_number, "POINT")
    return f"MILK_{number}"[:35]


def build_reason_for_payment(branch_name=None):
    branch = sanitize_ceft_token(branch_name or "MILK", "MILK")[:12]
    return f"{branch}_MILK_PAY"[:35]


def primary_or_first_bank_account(point):
    try:
        accounts = list(point.bank_accounts.all())
    except Exception:
        accounts = []
    if not accounts:
        return None
    for account in accounts:
        if account.is_primary:
            return account
    return accounts[0]


def build_ceft_row(
    *,
    debit_account_no,
    account,
    amount,
    value_date,
    customer_reference,
    credit_narration,
    reason_for_payment,
):
    return [
        "P",
        str(debit_account_no or "").strip(),
        str(account.account_name or "").strip().upper(),
        str(account.bank_code or "").strip(),
        str(account.branch_code or "").strip(),
        str(account.account_number or "").strip(),
        format_ceft_amount(amount),
        value_date,
        customer_reference,
        CEFT_PRODUCT_CODE,
        credit_narration,
        reason_for_payment,
    ]


def build_ceft_matrix(rows, *, debit_account_no=None, value_date=None, start=None, end=None):
    """
    rows: iterable of dicts with keys:
      - point or farmer (entity with bank_accounts)
      - amount (Decimal/str/float)
      - account (optional bank account)
    """
    debit = debit_account_no or default_debit_account_no()
    value = format_ceft_value_date(value_date or end or date.today())
    matrix = [list(CEFT_HEADERS)]
    skipped = []
    for item in rows:
        entity = item.get("point") or item.get("farmer") or item.get("entity")
        amount = item.get("amount")
        account = item.get("account") or primary_or_first_bank_account(entity)
        if not entity:
            skipped.append("Missing payee.")
            continue
        try:
            amount_dec = Decimal(str(amount).replace(",", "")).quantize(Decimal("0.01"))
        except (InvalidOperation, TypeError, ValueError, AttributeError):
            skipped.append(f"{entity}: invalid amount.")
            continue
        if amount_dec <= 0:
            skipped.append(f"{entity}: amount must be greater than zero.")
            continue
        if not account:
            skipped.append(f"{entity}: no bank account.")
            continue
        if not account.is_ceft_ready:
            skipped.append(
                f"{entity}: bank account needs name, account number, bank code, and branch code."
            )
            continue
        branch_name = ""
        if getattr(entity, "route", None) and getattr(entity.route, "branch", None):
            branch_name = entity.route.branch.name
        elif getattr(entity, "branch", None):
            branch_name = entity.branch.name
        number = (
            getattr(entity, "number", None)
            or getattr(entity, "registration_number", None)
            or str(getattr(entity, "pk", "") or "")
        )
        name = (
            getattr(entity, "name", None)
            or getattr(entity, "common_name", None)
            or getattr(entity, "full_name", None)
        )
        matrix.append(
            build_ceft_row(
                debit_account_no=debit,
                account=account,
                amount=amount_dec,
                value_date=value,
                customer_reference=build_customer_reference(branch_name, start, end),
                credit_narration=build_credit_narration(number, name),
                reason_for_payment=build_reason_for_payment(branch_name),
            )
        )
    return matrix, skipped


def normalize_header(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())


def detect_ceft_columns(headers):
    normalized = {normalize_header(h): idx for idx, h in enumerate(headers)}
    mapping = {}
    aliases = {
        "account_name": ("beneficiaryname", "accountname", "account_name", "name"),
        "bank_code": ("beneficiarybankcode", "bankcode", "bank_code"),
        "branch_code": ("beneficiarybranchcode", "branchcode", "branch_code"),
        "account_number": ("beneficiaryaccountno", "accountnumber", "account_number", "accountno"),
        "point_number": ("pointnumber", "collectionpointnumber", "point_number", "point"),
        "point_label": ("collectionpoint", "pointlabel", "creditnarration", "reasonforpayment"),
        # Dairy / company branch (Setup → Branches). Prefer exact "branch" over bank branch.
        "company_branch": (
            "branch",
            "companybranch",
            "dairybranch",
            "officebranch",
            "canmeebranch",
            "branchname",
        ),
        "bank_name": ("bankname", "bank", "bank_name"),
        "bank_branch": ("bankbranch", "bank_branch", "bankbranchname"),
        "is_primary": ("isprimary", "primary", "is_primary"),
    }
    for key, keys in aliases.items():
        for alias in keys:
            if alias in normalized:
                mapping[key] = normalized[alias]
                break
    return mapping


def extract_point_number_from_text(value):
    text = str(value or "").strip()
    if not text:
        return ""
    # Prefer trailing digits after underscore/space, e.g. NWG_MILK_401 or MILK_PAY_520
    match = re.search(r"(?:^|[_\s-])([A-Za-z0-9]*\d+[A-Za-z0-9]*)$", text)
    if match:
        return match.group(1).strip()
    # Or leading number before dash: 401 — Name
    if "—" in text or "-" in text:
        left = re.split(r"[—-]", text, maxsplit=1)[0].strip()
        if left:
            return left
    return text


def cell_text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    text = str(value).strip()
    if text.endswith(".0") and text.replace(".", "", 1).isdigit():
        return text[:-2]
    return text
