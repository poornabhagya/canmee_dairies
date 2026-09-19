import re
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

# F + branch initial + route initial + 5 digits (e.g. FAW00006)
_FARMER_REG_NUM_RE = re.compile(r"^F[A-Za-z]{2}(\d{5})$")


def _title_case_words(value):
    text = " ".join(str(value or "").split())
    return text.title() if text else ""


def _first_alpha(value, fallback):
    for ch in str(value or "").strip():
        if ch.isalpha():
            return ch.upper()
    return fallback


class AuditModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        abstract = True


class Route(AuditModel):
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=120)
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="routes",
        null=True,
        blank=True,
    )
    vehicle_name = models.CharField(max_length=120, blank=True)
    driver_name = models.CharField(max_length=120, blank=True)
    driver_access_code = models.CharField(
        max_length=32,
        unique=True,
        blank=True,
        null=True,
        help_text="Alphanumeric code for driver portal login.",
    )
    driver_can_view_route_summary = models.BooleanField(
        default=True,
        help_text="Driver may view route milk collection summary.",
    )
    driver_can_view_point_summary = models.BooleanField(
        default=True,
        help_text="Driver may view point milk collection summary.",
    )
    driver_can_view_farmer_goods = models.BooleanField(
        default=True,
        help_text="Driver may view issued farmer goods for this route.",
    )

    class Meta:
        ordering = ['code']

    def _generate_code(self):
        prefix = (self.branch.code if self.branch_id and self.branch else "ROUTE").upper()
        existing_codes = Route.objects.filter(code__startswith=f"{prefix}-").values_list("code", flat=True)
        max_num = 0
        for code in existing_codes:
            if not code.startswith(f"{prefix}-"):
                continue
            tail = code.split("-", 1)[1]
            if tail.isdigit():
                max_num = max(max_num, int(tail))
        return f"{prefix}-{max_num + 1:03d}"

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self._generate_code()
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.code} - {self.name}'

class CollectionPoint(AuditModel):
    number = models.CharField(max_length=20)
    name = models.CharField(max_length=120)
    location = models.CharField(max_length=255, blank=True)
    collector_fee = models.DecimalField(
        "Collector fee",
        max_digits=10,
        decimal_places=2,
        default=5,
    )
    additional = models.DecimalField(
        "Additional",
        max_digits=10,
        decimal_places=2,
        default=0,
    )
    additional_farmers = models.ManyToManyField(
        "Farmer",
        blank=True,
        related_name="additional_basis_collection_points",
        help_text="Farmers whose milk totals are used for Additional. Leave empty to use all farmers at this point.",
    )
    route = models.ForeignKey(Route, on_delete=models.PROTECT, related_name='collection_points')

    class Meta:
        unique_together = ('number', 'route')
        ordering = ['route__code', 'number']

    def save(self, *args, **kwargs):
        is_adding = self._state.adding
        prior_collector_fee = None
        prior_additional = None
        if not is_adding and self.pk:
            prior = (
                CollectionPoint.objects.filter(pk=self.pk)
                .only("collector_fee", "additional")
                .first()
            )
            if prior:
                prior_collector_fee = prior.collector_fee
                prior_additional = prior.additional
        super().save(*args, **kwargs)
        fee_changed = (
            is_adding
            or prior_collector_fee is None
            or prior_collector_fee != self.collector_fee
            or prior_additional != self.additional
        )
        if fee_changed:
            from datetime import datetime, time

            effective_date = getattr(self, "_fee_effective_from", None) or timezone.localdate()
            if effective_date == timezone.localdate():
                effective_at = timezone.now()
            else:
                effective_at = timezone.make_aware(
                    datetime.combine(effective_date, time.min),
                    timezone.get_current_timezone(),
                )
            changed_by = getattr(self, "_fee_changed_by", None)
            existing = (
                CollectionPointFeeHistory.objects.filter(
                    collection_point=self,
                    effective_at__date=effective_date,
                )
                .order_by("-effective_at", "-id")
                .first()
            )
            if existing:
                existing.collector_fee = self.collector_fee
                existing.additional = self.additional
                existing.effective_at = effective_at
                update_fields = ["collector_fee", "additional", "effective_at", "updated_at"]
                if changed_by is not None:
                    existing.changed_by = changed_by
                    update_fields.append("changed_by")
                existing.save(update_fields=update_fields)
            else:
                CollectionPointFeeHistory.objects.create(
                    collection_point=self,
                    collector_fee=self.collector_fee,
                    additional=self.additional,
                    effective_at=effective_at,
                    changed_by=changed_by,
                )
            if hasattr(self, "_fee_effective_from"):
                delattr(self, "_fee_effective_from")
            if hasattr(self, "_fee_changed_by"):
                delattr(self, "_fee_changed_by")
            try:
                from user_management.audit import log_audit_event

                log_audit_event(
                    user=changed_by,
                    action="updated",
                    module="Collection points",
                    summary=(
                        f"{self} fees · collector {self.collector_fee} / "
                        f"additional {self.additional}"
                    ),
                    object_type="collection_point",
                    object_id=str(self.pk),
                    source="fee_history",
                    details={
                        "collector_fee": str(self.collector_fee),
                        "additional": str(self.additional),
                    },
                )
            except Exception:
                pass

    def __str__(self):
        return f'{self.route.code}-{self.number} {self.name}'

    @property
    def primary_bank_account(self):
        accounts = list(self.bank_accounts.all())
        for account in accounts:
            if account.is_primary:
                return account
        return accounts[0] if accounts else None


SRI_LANKA_BANKS = (
    "Amana Bank",
    "Bank of Ceylon",
    "Cargills Bank",
    "Citibank",
    "Commercial Bank of Ceylon",
    "DFCC Bank",
    "Habib Bank",
    "Hatton National Bank",
    "HDFC Bank",
    "HSBC",
    "Indian Bank",
    "Indian Overseas Bank",
    "MCB Bank",
    "National Development Bank",
    "National Savings Bank",
    "Nations Trust Bank",
    "Pan Asia Bank",
    "People's Bank",
    "Public Bank",
    "Regional Development Bank",
    "Sampath Bank",
    "Sanasa Development Bank",
    "Seylan Bank",
    "Standard Chartered Bank",
    "State Bank of India",
    "Union Bank of Colombo",
)


def bank_short_code(bank_name):
    """Short label for display, e.g. Hatton National Bank -> HNB."""
    known = {
        "hatton national bank": "HNB",
        "bank of ceylon": "BOC",
        "commercial bank of ceylon": "COMB",
        "people's bank": "PB",
        "peoples bank": "PB",
        "sampath bank": "SAMP",
        "nations trust bank": "NTB",
        "national savings bank": "NSB",
        "national development bank": "NDB",
        "dfcc bank": "DFCC",
        "seylan bank": "SEYLAN",
        "hsbc": "HSBC",
        "pan asia bank": "PABC",
        "union bank of colombo": "UBC",
        "standard chartered bank": "SCB",
        "amana bank": "AMANA",
        "cargills bank": "CARGILLS",
    }
    text = " ".join(str(bank_name or "").split())
    if not text:
        return "BANK"
    mapped = known.get(text.lower())
    if mapped:
        return mapped
    words = [w for w in re.findall(r"[A-Za-z]+", text) if w.lower() not in {"of", "the", "and", "plc", "pvt", "ltd"}]
    if words:
        return "".join(w[0].upper() for w in words)
    return text[:4].upper()


def bank_name_choices(current=None):
    try:
        names = list(
            Bank.objects.filter(is_active=True, is_deleted=False)
            .order_by("sort_order", "name")
            .values_list("name", flat=True)
        )
    except Exception:
        names = []
    if not names:
        names = list(SRI_LANKA_BANKS)
    value = " ".join(str(current or "").split())
    if value and not any(name.lower() == value.lower() for name in names):
        names = [value] + names
    return [("", "Select bank")] + [(name, name) for name in names]


class Bank(AuditModel):
    name = models.CharField("Bank", max_length=120, unique=True)
    code = models.CharField(
        "Bank code",
        max_length=10,
        blank=True,
        help_text="CEFT / SLIPS bank code (e.g. 7010).",
    )
    is_active = models.BooleanField("Active", default=True)
    sort_order = models.PositiveSmallIntegerField("Order", default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name = "Bank"
        verbose_name_plural = "Banks"

    def __str__(self):
        return self.name


class CompanyProfile(AuditModel):
    """Singleton company / dairy profile used on letters and CEFT debit accounts."""

    name = models.CharField("Company name", max_length=160, default="CANMEE DAIRIES (PVT) LTD")
    legal_name = models.CharField("Legal name", max_length=160, blank=True)
    address = models.TextField("Address", blank=True)
    phone = models.CharField("Phone", max_length=40, blank=True)
    email = models.EmailField("Email", blank=True)
    tax_id = models.CharField("Tax / VAT ID", max_length=60, blank=True)
    website = models.CharField("Website", max_length=120, blank=True)

    class Meta:
        verbose_name = "Company profile"
        verbose_name_plural = "Company profile"

    def __str__(self):
        return self.name or "Company profile"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(
            pk=1,
            defaults={"name": "CANMEE DAIRIES (PVT) LTD"},
        )
        return obj

    @property
    def display_name(self):
        return (self.legal_name or self.name or "CANMEE DAIRIES (PVT) LTD").strip()

    @property
    def primary_bank_account(self):
        accounts = list(self.bank_accounts.filter(is_active=True))
        for account in accounts:
            if account.is_primary:
                return account
        return accounts[0] if accounts else None


class CompanyBankAccount(AuditModel):
    """Company debit / settlement accounts for CEFT bank payment uploads."""

    company = models.ForeignKey(
        CompanyProfile,
        on_delete=models.CASCADE,
        related_name="bank_accounts",
    )
    account_name = models.CharField("Account name", max_length=120)
    account_number = models.CharField("Account number", max_length=40)
    bank_name = models.CharField("Bank", max_length=120)
    bank_code = models.CharField("Bank code", max_length=10, blank=True)
    bank_branch = models.CharField("Branch", max_length=120, blank=True)
    branch_code = models.CharField("Branch code", max_length=10, blank=True)
    is_primary = models.BooleanField("Primary", default=False)
    is_active = models.BooleanField("Active", default=True)

    class Meta:
        ordering = ["-is_primary", "id"]
        verbose_name = "Company bank account"
        verbose_name_plural = "Company bank accounts"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        siblings = CompanyBankAccount.objects.filter(company_id=self.company_id)
        if self.is_primary:
            siblings.exclude(pk=self.pk).filter(is_primary=True).update(is_primary=False)
        elif not siblings.filter(is_primary=True, is_active=True).exists():
            type(self).objects.filter(pk=self.pk).update(is_primary=True)

    def __str__(self):
        return f"{self.account_name} · {self.account_number}"

    @property
    def label(self):
        bits = [self.bank_name, self.account_number, self.account_name]
        return " · ".join(bit for bit in bits if bit)

    @property
    def debit_option_label(self):
        number = (self.account_number or "").strip()
        try:
            short = bank_short_code(self.bank_name)
        except Exception:
            short = (self.bank_name or "BANK").strip() or "BANK"
        if number and short:
            return f"{number} ({short})"
        return number or short or (self.account_name or "").strip() or "Account"


class CollectionPointBankAccount(AuditModel):
    collection_point = models.ForeignKey(
        CollectionPoint,
        on_delete=models.CASCADE,
        related_name="bank_accounts",
    )
    account_name = models.CharField("Account name", max_length=120)
    account_number = models.CharField("Account number", max_length=40)
    bank_name = models.CharField("Bank", max_length=120)
    bank_code = models.CharField(
        "Bank code",
        max_length=10,
        blank=True,
        help_text="Beneficiary bank code for CEFT upload (e.g. 7010).",
    )
    bank_branch = models.CharField("Branch", max_length=120)
    branch_code = models.CharField(
        "Branch code",
        max_length=10,
        blank=True,
        help_text="Beneficiary branch code for CEFT upload (e.g. 048).",
    )
    is_primary = models.BooleanField("Primary", default=False)

    class Meta:
        ordering = ["-is_primary", "id"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        siblings = CollectionPointBankAccount.objects.filter(collection_point_id=self.collection_point_id)
        if self.is_primary:
            siblings.exclude(pk=self.pk).filter(is_primary=True).update(is_primary=False)
        elif not siblings.filter(is_primary=True).exists():
            type(self).objects.filter(pk=self.pk).update(is_primary=True)

    def __str__(self):
        return f"{self.account_name} · {self.account_number}"

    @property
    def label(self):
        bits = [self.bank_name, self.account_number, self.account_name]
        return " · ".join(bit for bit in bits if bit)

    @property
    def is_ceft_ready(self):
        return bool(
            (self.account_name or "").strip()
            and (self.account_number or "").strip()
            and (self.bank_code or "").strip()
            and (self.branch_code or "").strip()
        )


class FarmerBankAccount(AuditModel):
    farmer = models.ForeignKey(
        "Farmer",
        on_delete=models.CASCADE,
        related_name="bank_accounts",
    )
    account_name = models.CharField("Account name", max_length=120)
    account_number = models.CharField("Account number", max_length=40)
    bank_name = models.CharField("Bank", max_length=120)
    bank_code = models.CharField(
        "Bank code",
        max_length=10,
        blank=True,
        help_text="Beneficiary bank code for CEFT upload (e.g. 7010).",
    )
    bank_branch = models.CharField("Branch", max_length=120)
    branch_code = models.CharField(
        "Branch code",
        max_length=10,
        blank=True,
        help_text="Beneficiary branch code for CEFT upload (e.g. 048).",
    )
    is_primary = models.BooleanField("Primary", default=False)

    class Meta:
        ordering = ["-is_primary", "id"]

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        siblings = FarmerBankAccount.objects.filter(farmer_id=self.farmer_id)
        if self.is_primary:
            siblings.exclude(pk=self.pk).filter(is_primary=True).update(is_primary=False)
        elif not siblings.filter(is_primary=True).exists():
            type(self).objects.filter(pk=self.pk).update(is_primary=True)

    def __str__(self):
        return f"{self.account_name} · {self.account_number}"

    @property
    def label(self):
        bits = [self.bank_name, self.account_number, self.account_name]
        return " · ".join(bit for bit in bits if bit)

    @property
    def is_ceft_ready(self):
        return bool(
            (self.account_name or "").strip()
            and (self.account_number or "").strip()
            and (self.bank_code or "").strip()
            and (self.branch_code or "").strip()
        )


class CollectionPointFeeHistory(AuditModel):
    collection_point = models.ForeignKey(
        CollectionPoint,
        on_delete=models.CASCADE,
        related_name="fee_history",
    )
    collector_fee = models.DecimalField(max_digits=10, decimal_places=2)
    additional = models.DecimalField(max_digits=10, decimal_places=2)
    effective_at = models.DateTimeField(default=timezone.now, db_index=True)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="collection_point_fee_changes",
    )

    class Meta:
        ordering = ["-effective_at", "-id"]

    def __str__(self):
        return (
            f"{self.collection_point_id} fee {self.collector_fee} / "
            f"additional {self.additional}"
        )

class Buyer(AuditModel):
    class RateUnit(models.TextChoices):
        LITER = "liter", "Per liter"
        KG = "kg", "Per kg"

    name = models.CharField(max_length=120, unique=True)
    contact_person = models.CharField(max_length=120, blank=True)
    phone = models.CharField(max_length=25, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=0,
        help_text="Current buying rate paid to us.",
    )
    rate_unit = models.CharField(
        max_length=10,
        choices=RateUnit.choices,
        default=RateUnit.LITER,
        help_text="Whether the rate applies per liter or per kg.",
    )
    branches = models.ManyToManyField(
        "branches.Branch",
        blank=True,
        related_name="buyers",
    )

    def save(self, *args, **kwargs):
        is_adding = self._state.adding
        prior_rate = None
        prior_unit = None
        if not is_adding and self.pk:
            prior = (
                Buyer.objects.filter(pk=self.pk)
                .values("rate", "rate_unit")
                .first()
            )
            if prior:
                prior_rate = prior["rate"]
                prior_unit = prior["rate_unit"]
        super().save(*args, **kwargs)
        rate_changed = (
            is_adding
            or prior_rate is None
            or prior_rate != self.rate
            or (prior_unit or "") != (self.rate_unit or "")
        )
        if rate_changed:
            effective_from = getattr(self, "_rate_effective_from", None) or timezone.localdate()
            # Keep one open row per effective date; later same-day changes replace it.
            existing = (
                BuyerRateHistory.objects.filter(buyer=self, effective_from=effective_from)
                .order_by("-id")
                .first()
            )
            if existing:
                existing.rate = self.rate
                existing.rate_unit = self.rate_unit
                existing.save(update_fields=["rate", "rate_unit", "updated_at"])
            else:
                BuyerRateHistory.objects.create(
                    buyer=self,
                    rate=self.rate,
                    rate_unit=self.rate_unit,
                    effective_from=effective_from,
                )
            if hasattr(self, "_rate_effective_from"):
                delattr(self, "_rate_effective_from")

    def __str__(self):
        return self.name

    @property
    def rate_unit_label(self):
        return self.get_rate_unit_display()


class BuyerRateHistory(AuditModel):
    buyer = models.ForeignKey(Buyer, on_delete=models.CASCADE, related_name="rate_history")
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    rate_unit = models.CharField(
        max_length=10,
        choices=Buyer.RateUnit.choices,
        default=Buyer.RateUnit.LITER,
    )
    effective_from = models.DateField(default=timezone.localdate, db_index=True)

    class Meta:
        ordering = ["-effective_from", "-id"]
        permissions = [
            ("change_buyerrate", "Can change buyer rate"),
        ]

    def __str__(self):
        return f"{self.buyer_id} {self.rate}/{self.rate_unit} from {self.effective_from}"

    @property
    def effective_to(self):
        """Day before the next rate change; None if this is the current open rate."""
        nxt = (
            BuyerRateHistory.objects.filter(
                buyer_id=self.buyer_id,
                effective_from__gt=self.effective_from,
            )
            .order_by("effective_from", "id")
            .values_list("effective_from", flat=True)
            .first()
        )
        if not nxt:
            return None
        from datetime import timedelta

        return nxt - timedelta(days=1)


class Farmer(AuditModel):
    class ApplyRatePaid(models.TextChoices):
        YES = "yes", "Yes"
        NO = "no", "No"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        RESIGNED = "resigned", "Resigned"

    registration_number = models.CharField(
        max_length=50,
        unique=True,
        null=True,
        blank=True,
        help_text="Leave blank to assign automatically (F + Branch initial + Route initial + sequence).",
    )
    full_name = models.CharField(max_length=160)
    initial = models.CharField(max_length=160, blank=True)
    common_name = models.CharField(max_length=100)
    rate = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    apply_rate_paid = models.CharField(
        max_length=3,
        choices=ApplyRatePaid.choices,
        default=ApplyRatePaid.YES,
    )
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="farmers",
        null=True,
        blank=False,
    )
    route = models.ForeignKey(
        Route,
        on_delete=models.PROTECT,
        related_name="farmers",
        null=True,
        blank=True,
    )
    collection_point = models.ForeignKey(
        CollectionPoint,
        on_delete=models.SET_NULL,
        related_name="assigned_farmers",
        null=True,
        blank=False,
        verbose_name="collection point",
    )
    nic = models.CharField(
        max_length=20,
        blank=True,
        verbose_name="NIC",
        help_text="National Identity Card number",
    )
    date_of_birth = models.DateField(null=True, blank=True)
    mobile = models.CharField(max_length=25, blank=True)
    whatsapp = models.CharField(max_length=25, blank=True)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    resigned_at = models.DateField(null=True, blank=True)
    resignation_note = models.CharField(max_length=255, blank=True)
    resigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="farmer_resignations_recorded",
    )

    class Meta:
        ordering = ["common_name", "full_name"]

    @property
    def primary_bank_account(self):
        accounts = list(self.bank_accounts.all())
        for account in accounts:
            if account.is_primary:
                return account
        return accounts[0] if accounts else None

    def _build_initial(self):
        value = (self.full_name or "").strip()
        if not value:
            return ""
        parts = [p for p in value.replace(".", " ").split() if p]
        if len(parts) <= 1:
            return value
        initials = [f"{p[0].upper()}." for p in parts[:-1]]
        return f"{' '.join(initials)} {parts[-1]}"

    def _registration_prefix(self):
        branch_initial = _first_alpha(self.branch.name if self.branch_id and self.branch else "", "B")
        route_initial = _first_alpha(self.route.name if self.route_id and self.route else "", "R")
        return f"F{branch_initial}{route_initial}"

    def _generate_registration_number(self):
        """
        Global numeric suffix: next free 5-digit tail not used in any full registration string.
        Avoids IntegrityError from count()+1 colliding with existing rows.
        """
        prefix = self._registration_prefix()
        qs = Farmer.objects.all()
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        used = {
            str(r).strip()
            for r in qs.exclude(registration_number__isnull=True)
            .exclude(registration_number="")
            .values_list("registration_number", flat=True)
        }
        max_seq = 0
        for r in used:
            m = _FARMER_REG_NUM_RE.match(r)
            if m:
                max_seq = max(max_seq, int(m.group(1)))
        seq = max_seq + 1
        for _ in range(1000000):
            candidate = f"{prefix}{seq:05d}"
            if candidate not in used:
                return candidate
            seq += 1
        raise RuntimeError("Could not allocate a unique registration number.")

    def _registration_number_preserving_suffix(self, new_prefix, prior_reg):
        """
        On update, keep the 5-digit tail from the previous registration when the prefix
        (branch/route) changes. If the combined value is already taken, increment the
        numeric part until free.
        """
        text = str(prior_reg or "").strip()
        m = _FARMER_REG_NUM_RE.match(text)
        if not m:
            return self._generate_registration_number()
        seq = int(m.group(1))
        qs = Farmer.objects.all()
        if self.pk:
            qs = qs.exclude(pk=self.pk)
        used = {
            str(r).strip()
            for r in qs.exclude(registration_number__isnull=True)
            .exclude(registration_number="")
            .values_list("registration_number", flat=True)
        }
        for _ in range(1000000):
            candidate = f"{new_prefix}{seq:05d}"
            if candidate not in used:
                return candidate
            seq += 1
        raise RuntimeError("Could not allocate a unique registration number.")

    def clean(self):
        super().clean()
        self.full_name = _title_case_words(self.full_name)
        self.common_name = _title_case_words(self.common_name)
        if not self.full_name:
            raise ValidationError({"full_name": "Full name is required."})
        if not self.common_name:
            raise ValidationError({"common_name": "Common name is required."})
        if self.rate is None or self.rate <= 0:
            raise ValidationError({"rate": "Rate must be greater than zero."})
        if not (self.apply_rate_paid or "").strip():
            raise ValidationError({"apply_rate_paid": "Select Apply Rate Paid option."})
        if not self.initial:
            self.initial = self._build_initial()
        if self.collection_point_id:
            self.route = self.collection_point.route
        if self.route_id:
            self.branch = self.route.branch
        if not self.collection_point_id:
            raise ValidationError({"collection_point": "Collection point is required."})
        if not self.branch_id:
            raise ValidationError({"branch": "Branch is required."})

    def _registration_prefix_for_branch_route(self, branch, route):
        """Same rule as _registration_prefix but from related objects (e.g. DB snapshot)."""
        bi = _first_alpha(branch.name if branch is not None else "", "B")
        ri = _first_alpha(route.name if route is not None else "", "R")
        return f"F{bi}{ri}"

    def save(self, *args, **kwargs):
        is_adding = self._state.adding
        # Snapshot prefix and previous registration before full_clean (branch/route may change).
        old_prefix = None
        prior_reg = None
        prior_rate = None
        prior_apply_rate_paid = None
        if self.pk:
            try:
                prev = Farmer.objects.select_related("branch", "route").get(pk=self.pk)
                old_prefix = self._registration_prefix_for_branch_route(prev.branch, prev.route)
                prior_reg = prev.registration_number
                prior_rate = prev.rate
                prior_apply_rate_paid = prev.apply_rate_paid
            except Farmer.DoesNotExist:
                pass

        if not self.initial:
            self.initial = self._build_initial()
        self.full_clean()
        new_prefix = self._registration_prefix()

        needs_auto_number = False
        if self._state.adding:
            needs_auto_number = not (self.registration_number and str(self.registration_number).strip())
        else:
            # Update: regenerate when branch/route (or their names) change the code prefix, or reg was empty.
            if old_prefix is not None and new_prefix != old_prefix:
                needs_auto_number = True
            elif not (self.registration_number and str(self.registration_number).strip()):
                needs_auto_number = True

        if needs_auto_number:
            if self._state.adding:
                self.registration_number = self._generate_registration_number()
            elif prior_reg and str(prior_reg).strip():
                # Update: keep the same 5-digit sequence; only the F+branch+route prefix may change.
                self.registration_number = self._registration_number_preserving_suffix(
                    new_prefix, prior_reg
                )
            else:
                self.registration_number = self._generate_registration_number()
        super().save(*args, **kwargs)
        rate_changed = (
            is_adding
            or prior_rate is None
            or prior_rate != self.rate
            or (prior_apply_rate_paid or "") != (self.apply_rate_paid or "")
        )
        if rate_changed:
            from datetime import datetime, time

            effective_date = getattr(self, "_rate_effective_from", None) or timezone.localdate()
            if effective_date == timezone.localdate():
                effective_at = timezone.now()
            else:
                effective_at = timezone.make_aware(
                    datetime.combine(effective_date, time.min),
                    timezone.get_current_timezone(),
                )
            # Keep one history row per calendar day; later same-day changes replace it
            # (matches Buyer rate history and prevents Enter/focusout double-save duplicates).
            existing = (
                FarmerRateHistory.objects.filter(
                    farmer=self,
                    effective_at__date=effective_date,
                )
                .order_by("-effective_at", "-id")
                .first()
            )
            if existing:
                existing.rate = self.rate
                existing.apply_rate_paid = self.apply_rate_paid
                existing.effective_at = effective_at
                existing.save(
                    update_fields=["rate", "apply_rate_paid", "effective_at", "updated_at"]
                )
            else:
                FarmerRateHistory.objects.create(
                    farmer=self,
                    rate=self.rate,
                    apply_rate_paid=self.apply_rate_paid,
                    effective_at=effective_at,
                )
            if hasattr(self, "_rate_effective_from"):
                delattr(self, "_rate_effective_from")

    def __str__(self):
        primary_name = (self.common_name or self.full_name or "").strip() or "-"
        reg = (self.registration_number or "").strip()
        return f"{reg} - {primary_name}" if reg else primary_name

    @property
    def name(self):
        return self.full_name


class FarmerRateHistory(AuditModel):
    farmer = models.ForeignKey(Farmer, on_delete=models.CASCADE, related_name="rate_history")
    rate = models.DecimalField(max_digits=10, decimal_places=2)
    apply_rate_paid = models.CharField(
        max_length=3,
        choices=Farmer.ApplyRatePaid.choices,
        default=Farmer.ApplyRatePaid.YES,
    )
    effective_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-effective_at", "-id"]
        permissions = [
            ("change_farmerrate", "Can change farmer rate"),
            ("bulk_update_farmerrate", "Can bulk update farmer rates"),
        ]

    def __str__(self):
        return f"{self.farmer_id} {self.rate} ({self.apply_rate_paid})"


class AdvancePaymentType(models.TextChoices):
    ADVANCE = "advance", "Advance Payment"
    BANK_LOAN = "bank_loan", "Bank Loan Deduction"


class FarmerAdvancePayment(AuditModel):
    farmer = models.ForeignKey(Farmer, on_delete=models.CASCADE, related_name="advance_payments")
    date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    advance_type = models.CharField(
        max_length=20,
        choices=AdvancePaymentType.choices,
        default=AdvancePaymentType.ADVANCE,
    )
    note = models.CharField(max_length=255, blank=True)
    is_recovered = models.BooleanField(default=False)
    recovered_at = models.DateTimeField(null=True, blank=True)
    recovery_note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="farmer_advance_payments",
    )

    class Meta:
        ordering = ["-date", "-id"]

    def clean(self):
        if self.amount is None or self.amount <= 0:
            raise ValidationError({"amount": "Advance amount must be greater than zero."})

    def __str__(self):
        return f"{self.farmer_id} advance {self.amount}"


class CollectionPointAdvancePayment(AuditModel):
    collection_point = models.ForeignKey(
        CollectionPoint,
        on_delete=models.CASCADE,
        related_name="advance_payments",
    )
    date = models.DateField(default=timezone.localdate)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    advance_type = models.CharField(
        max_length=20,
        choices=AdvancePaymentType.choices,
        default=AdvancePaymentType.ADVANCE,
    )
    note = models.CharField(max_length=255, blank=True)
    recovered_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Amount already deducted from collection point payments.",
    )
    available_on = models.DateField(
        help_text="First date the remaining balance can be deducted on a payment sheet.",
    )
    is_recovered = models.BooleanField(default=False)
    recovered_at = models.DateTimeField(null=True, blank=True)
    recovery_note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="collection_point_advance_payments",
    )

    class Meta:
        ordering = ["-date", "-id"]
        indexes = [
            models.Index(
                fields=["collection_point", "is_recovered", "available_on"],
                name="cp_adv_open_avail_idx",
            ),
        ]

    def clean(self):
        if self.amount is None or self.amount <= 0:
            raise ValidationError({"amount": "Advance amount must be greater than zero."})

    def save(self, *args, **kwargs):
        if self.available_on is None:
            self.available_on = self.date or timezone.localdate()
        if self.recovered_amount is None:
            self.recovered_amount = Decimal("0.00")
        super().save(*args, **kwargs)

    @property
    def remaining_amount(self):
        remaining = (self.amount or Decimal("0")) - (self.recovered_amount or Decimal("0"))
        if remaining < 0:
            remaining = Decimal("0")
        return remaining.quantize(Decimal("0.01"))

    def __str__(self):
        return f"{self.collection_point_id} advance {self.amount}"
