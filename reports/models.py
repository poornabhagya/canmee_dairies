from django.conf import settings
from django.db import models
from django.utils import timezone


class FarmerPaymentLoanDeductionSetting(models.Model):
    """Per farmer + payment period: whether loan instalments are deducted from net pay."""

    farmer = models.ForeignKey(
        "masters.Farmer",
        on_delete=models.CASCADE,
        related_name="payment_loan_deduction_settings",
    )
    period_start = models.DateField()
    period_end = models.DateField()
    deduct_loan = models.BooleanField(
        default=True,
        help_text="When unchecked, loan instalments are calculated but not deducted from this payment.",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="farmer_payment_loan_deduction_settings",
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_end", "-period_start", "farmer_id"]
        permissions = [
            ("view_farmerpaymentsheet", "Can view farmer payment sheet"),
            ("view_collectionpointpaymentsheet", "Can view collection point payment sheet"),
            ("view_pointgoodssummary", "Can view point goods summary"),
            ("view_paymentsettlement", "Can view payment settlement details"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["farmer", "period_start", "period_end"],
                name="uniq_farmer_payment_loan_deduction_period",
            ),
        ]

    def __str__(self):
        state = "deduct" if self.deduct_loan else "skip"
        return f"{self.farmer_id} {self.period_start}–{self.period_end} ({state})"


class FarmerPaymentPeriodDeductionSkip(models.Model):
    """Skip a specific advance, goods receipt/line, or loan instalment for one payment period."""

    class Kind(models.TextChoices):
        ADVANCE = "advance", "Advance"
        GOODS_LINE = "goods_line", "Goods line"
        GOODS_RECEIPT = "goods_receipt", "Goods receipt"
        LOAN_INSTALMENT = "loan_instalment", "Loan instalment"

    farmer = models.ForeignKey(
        "masters.Farmer",
        on_delete=models.CASCADE,
        related_name="payment_deduction_skips",
    )
    period_start = models.DateField()
    period_end = models.DateField()
    kind = models.CharField(max_length=20, choices=Kind.choices)
    reference_id = models.PositiveBigIntegerField(default=0)
    reference_key = models.CharField(max_length=64, blank=True, default="")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="farmer_payment_deduction_skips",
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "farmer",
                    "period_start",
                    "period_end",
                    "kind",
                    "reference_id",
                    "reference_key",
                ],
                name="uniq_farmer_payment_period_deduction_skip",
            ),
        ]

    def __str__(self):
        return f"{self.farmer_id} {self.period_start}–{self.period_end} skip {self.kind}"


class CollectionPointPaymentLoanDeductionSetting(models.Model):
    """Per collection point + payment period: whether point loan instalments are deducted."""

    collection_point = models.ForeignKey(
        "masters.CollectionPoint",
        on_delete=models.CASCADE,
        related_name="payment_loan_deduction_settings",
    )
    period_start = models.DateField()
    period_end = models.DateField()
    deduct_loan = models.BooleanField(
        default=True,
        help_text="When unchecked, collection point loan instalments are calculated but not deducted.",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="collection_point_payment_loan_deduction_settings",
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_end", "-period_start", "collection_point_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["collection_point", "period_start", "period_end"],
                name="uniq_cp_payment_loan_deduction_period",
            ),
        ]

    def __str__(self):
        state = "deduct" if self.deduct_loan else "skip"
        return f"{self.collection_point_id} {self.period_start}–{self.period_end} ({state})"


class CollectionPointPaymentPeriodDeductionSkip(models.Model):
    """Skip a direct-to-point goods receipt/line or loan instalment for one payment period."""

    class Kind(models.TextChoices):
        ADVANCE = "advance", "Advance"
        GOODS_LINE = "goods_line", "Goods line"
        GOODS_RECEIPT = "goods_receipt", "Goods receipt"
        LOAN_INSTALMENT = "loan_instalment", "Loan instalment"

    collection_point = models.ForeignKey(
        "masters.CollectionPoint",
        on_delete=models.CASCADE,
        related_name="payment_deduction_skips",
    )
    period_start = models.DateField()
    period_end = models.DateField()
    kind = models.CharField(max_length=20, choices=Kind.choices)
    reference_id = models.PositiveBigIntegerField(default=0)
    reference_key = models.CharField(max_length=64, blank=True, default="")
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="collection_point_payment_deduction_skips",
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "collection_point",
                    "period_start",
                    "period_end",
                    "kind",
                    "reference_id",
                    "reference_key",
                ],
                name="uniq_collection_point_payment_period_deduction_skip",
            ),
        ]

    def __str__(self):
        return (
            f"{self.collection_point_id} {self.period_start}–{self.period_end} skip {self.kind}"
        )


class CollectionPointPaymentAdvanceDeduction(models.Model):
    """How much of a collection-point advance to deduct in one payment period."""

    collection_point = models.ForeignKey(
        "masters.CollectionPoint",
        on_delete=models.CASCADE,
        related_name="payment_advance_deductions",
    )
    period_start = models.DateField()
    period_end = models.DateField()
    advance = models.ForeignKey(
        "masters.CollectionPointAdvancePayment",
        on_delete=models.CASCADE,
        related_name="period_deductions",
    )
    deduct_amount = models.DecimalField(max_digits=14, decimal_places=2)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="collection_point_payment_advance_deductions",
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_end", "-period_start", "collection_point_id", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["collection_point", "period_start", "period_end", "advance"],
                name="uniq_cp_payment_advance_deduction",
            ),
        ]

    def __str__(self):
        return (
            f"{self.collection_point_id} {self.period_start}–{self.period_end} "
            f"advance {self.advance_id}: {self.deduct_amount}"
        )


class CollectionPointPaymentLoanDeduction(models.Model):
    """How much of a collection-point loan instalment to deduct in one payment period."""

    collection_point = models.ForeignKey(
        "masters.CollectionPoint",
        on_delete=models.CASCADE,
        related_name="payment_loan_deductions",
    )
    period_start = models.DateField()
    period_end = models.DateField()
    schedule = models.ForeignKey(
        "collection_point_loans.CollectionPointLoanRepaymentSchedule",
        on_delete=models.CASCADE,
        related_name="period_deductions",
    )
    deduct_amount = models.DecimalField(max_digits=14, decimal_places=2)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="collection_point_payment_loan_deductions",
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_end", "-period_start", "collection_point_id", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["collection_point", "period_start", "period_end", "schedule"],
                name="uniq_cp_payment_loan_deduction",
            ),
        ]

    def __str__(self):
        return (
            f"{self.collection_point_id} {self.period_start}–{self.period_end} "
            f"loan schedule {self.schedule_id}: {self.deduct_amount}"
        )


class FarmerPeriodPayment(models.Model):
    """Recorded milk payment for a farmer for a payment period."""

    farmer = models.ForeignKey(
        "masters.Farmer",
        on_delete=models.PROTECT,
        related_name="period_payments",
    )
    period_start = models.DateField()
    period_end = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    expected_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text="Net payable at the time of recording.",
    )
    rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Farmer rate used when this payment was recorded.",
    )
    apply_rate_paid = models.CharField(max_length=3, blank=True, default="")
    total_liters = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    gross_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    advance_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    goods_deduction = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    loan_deduction = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    net_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    branch_ids = models.JSONField(default=list, blank=True)
    paid_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="farmer_period_payments_recorded",
    )

    class PaymentMethod(models.TextChoices):
        CASH = "cash", "Cash"
        BANK_TRANSFER = "bank_transfer", "Bank transfer"

    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH,
    )
    bank_account = models.ForeignKey(
        "masters.FarmerBankAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="period_payments",
    )
    bank_account_name = models.CharField(max_length=120, blank=True)
    bank_account_number = models.CharField(max_length=40, blank=True)
    bank_name = models.CharField(max_length=120, blank=True)
    bank_branch = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ["-paid_at", "-id"]
        permissions = [
            (
                "record_manual_settlement",
                "Can record manual advance/goods/loan settlement",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["farmer", "period_start", "period_end"],
                name="uniq_farmer_period_payment",
            ),
        ]

    def __str__(self):
        return f"Farmer {self.farmer_id} {self.period_start}–{self.period_end}: {self.amount}"

    @property
    def has_payment_snapshot(self):
        return self.rate is not None

    @property
    def bank_label(self):
        if self.payment_method != self.PaymentMethod.BANK_TRANSFER:
            return ""
        bits = [self.bank_name, self.bank_account_number, self.bank_account_name]
        return " · ".join(bit for bit in bits if bit)


class FarmerPaymentSheetRecord(models.Model):
    """Aggregated paid farmer payment sheet for a period and branch selection."""

    period_start = models.DateField()
    period_end = models.DateField()
    branch_ids = models.JSONField(default=list, blank=True)
    branch_key = models.CharField(max_length=128, db_index=True)
    farmer_count = models.PositiveIntegerField(default=0)
    total_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_gross = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    last_paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_paid_at", "-period_end", "-period_start"]
        constraints = [
            models.UniqueConstraint(
                fields=["period_start", "period_end", "branch_key"],
                name="uniq_farmer_payment_sheet_record",
            ),
        ]

    def __str__(self):
        return f"Farmer sheet {self.period_start}–{self.period_end} ({self.branch_key})"


class CollectionPointPeriodPayment(models.Model):
    """Recorded payment for a collection point for a payment period."""

    collection_point = models.ForeignKey(
        "masters.CollectionPoint",
        on_delete=models.PROTECT,
        related_name="period_payments",
    )
    period_start = models.DateField()
    period_end = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    expected_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        help_text="Payable amount at the time of recording.",
    )
    rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Average linked farmer rate when this payment was recorded.",
    )
    collector_fee_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Collector fee per unit when this payment was recorded.",
    )
    total_quantity = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    gross_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    advance_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    goods_deduction = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    loan_deduction = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    collector_fee_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    additional_rate = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Additional per unit when this payment was recorded.",
    )
    additional_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    previous_outstanding_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Previous outstanding deducted when this payment was recorded.",
    )
    payment_correction_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Manual payment correction applied when this payment was recorded.",
    )
    net_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)

    class PaymentMethod(models.TextChoices):
        CASH = "cash", "Cash"
        BANK_TRANSFER = "bank_transfer", "Bank transfer"

    payment_method = models.CharField(
        max_length=20,
        choices=PaymentMethod.choices,
        default=PaymentMethod.CASH,
    )
    bank_account = models.ForeignKey(
        "masters.CollectionPointBankAccount",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="period_payments",
    )
    bank_account_name = models.CharField(max_length=120, blank=True)
    bank_account_number = models.CharField(max_length=40, blank=True)
    bank_name = models.CharField(max_length=120, blank=True)
    bank_branch = models.CharField(max_length=120, blank=True)
    branch_ids = models.JSONField(default=list, blank=True)
    paid_at = models.DateTimeField(default=timezone.now)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="collection_point_period_payments_recorded",
    )

    class Meta:
        ordering = ["-paid_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["collection_point", "period_start", "period_end"],
                name="uniq_collection_point_period_payment",
            ),
        ]

    def __str__(self):
        return (
            f"Point {self.collection_point_id} {self.period_start}–{self.period_end}: {self.amount}"
        )

    @property
    def has_payment_snapshot(self):
        return self.rate is not None

    @property
    def bank_label(self):
        if self.payment_method != self.PaymentMethod.BANK_TRANSFER:
            return ""
        bits = [self.bank_name, self.bank_account_number, self.bank_account_name]
        return " · ".join(bit for bit in bits if bit)


class CollectionPointPaymentSheetRecord(models.Model):
    """Aggregated paid collection point payment sheet for a period and branch selection."""

    period_start = models.DateField()
    period_end = models.DateField()
    branch_ids = models.JSONField(default=list, blank=True)
    branch_key = models.CharField(max_length=128, db_index=True)
    point_count = models.PositiveIntegerField(default=0)
    total_paid = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total_gross = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    last_paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_paid_at", "-period_end", "-period_start"]
        constraints = [
            models.UniqueConstraint(
                fields=["period_start", "period_end", "branch_key"],
                name="uniq_collection_point_payment_sheet_record",
            ),
        ]

    def __str__(self):
        return f"Point sheet {self.period_start}–{self.period_end} ({self.branch_key})"


class CollectionPointPreviousOutstanding(models.Model):
    """Deficit carried from a settled collection-point period to later sheets."""

    collection_point = models.ForeignKey(
        "masters.CollectionPoint",
        on_delete=models.CASCADE,
        related_name="previous_outstanding_records",
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    originated_period_start = models.DateField()
    originated_period_end = models.DateField()
    available_on = models.DateField(
        help_text="First date this outstanding can be deducted on a payment sheet.",
    )
    originated_payment = models.ForeignKey(
        CollectionPointPeriodPayment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="originated_previous_outstanding",
    )
    is_recovered = models.BooleanField(default=False)
    recovered_at = models.DateTimeField(null=True, blank=True)
    recovered_period_start = models.DateField(null=True, blank=True)
    recovered_period_end = models.DateField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="collection_point_previous_outstanding_created",
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-available_on", "-id"]
        verbose_name = "collection point previous outstanding"
        verbose_name_plural = "collection point previous outstanding"
        constraints = [
            models.UniqueConstraint(
                fields=[
                    "collection_point",
                    "originated_period_start",
                    "originated_period_end",
                ],
                name="uniq_cp_previous_outstanding_period",
            ),
        ]
        indexes = [
            models.Index(
                fields=["collection_point", "is_recovered", "available_on"],
                name="cp_prev_os_open_idx",
            ),
        ]

    def __str__(self):
        return (
            f"Point {self.collection_point_id} outstanding "
            f"{self.originated_period_start}–{self.originated_period_end}: {self.amount}"
        )


class CollectionPointPaymentCorrection(models.Model):
    """Manual payment adjustment for one collection point and payment period."""

    collection_point = models.ForeignKey(
        "masters.CollectionPoint",
        on_delete=models.CASCADE,
        related_name="payment_corrections",
    )
    period_start = models.DateField()
    period_end = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    note = models.CharField(max_length=255, blank=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="collection_point_payment_corrections_updated",
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-period_end", "-period_start", "collection_point_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["collection_point", "period_start", "period_end"],
                name="uniq_cp_payment_correction_period",
            ),
        ]

    def __str__(self):
        return (
            f"{self.collection_point_id} {self.period_start}–{self.period_end}: "
            f"{self.amount}"
        )
