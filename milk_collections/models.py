from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from canmee_dairies.constants import MILK_LITER_FACTOR

from masters.models import Route, CollectionPoint, Farmer, AuditModel


class CollectionSource(models.TextChoices):
    POINT = "point", "Collection point"
    FARMER = "farmer", "Farmer"


class MilkCollectionQuerySet(models.QuerySet):
    def route_collections(self):
        """Collection point rows only — branch/route totals must not double-count farmer lines."""
        return self.filter(source=CollectionSource.POINT)


class MilkCollection(AuditModel):
    date = models.DateField()
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="milk_collections",
        null=True,
        blank=True,
        editable=False,
    )
    route = models.ForeignKey(Route, on_delete=models.PROTECT)
    source = models.CharField(
        max_length=10,
        choices=CollectionSource.choices,
        default=CollectionSource.POINT,
    )
    collection_point = models.ForeignKey(
        CollectionPoint,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    farmer = models.ForeignKey(
        Farmer,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    kg = models.DecimalField(max_digits=10, decimal_places=2)
    liters = models.DecimalField(max_digits=10, decimal_places=2, editable=False)
    is_paid = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    objects = MilkCollectionQuerySet.as_manager()

    class Meta:
        indexes = [
            models.Index(fields=["collection_point", "source", "date"], name="milkcoll_point_src_date"),
            models.Index(fields=["farmer", "source", "date"], name="milkcoll_farmer_src_date"),
            models.Index(fields=["source", "is_deleted", "date"], name="milkcoll_src_del_date"),
        ]
        permissions = [
            ("view_milkcollectionpaymentstatus", "Can view milk collection payment status"),
            ("view_milkcollectionreconcile", "Can view milk collection point reconcile"),
            (
                "change_milkcollectionreconcilechoice",
                "Can set milk collection point reconcile choice",
            ),
            (
                "sync_milkcollectionreconcile",
                "Can sync milk collection point reconcile totals",
            ),
            (
                "view_milkcollectiontallysheet",
                "Can view collection point tally sheet",
            ),
            (
                "change_milkcollectiontallysheet",
                "Can save milk quantities on collection point tally sheet",
            ),
            (
                "change_milkcollectiontallyfactor",
                "Can save fat/LR on collection point tally sheet",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["date", "route", "collection_point"],
                name="uniq_daily_route_point_collection",
                condition=models.Q(source=CollectionSource.POINT),
            ),
            models.UniqueConstraint(
                fields=["date", "route", "farmer"],
                name="uniq_daily_route_farmer_collection",
                condition=models.Q(source=CollectionSource.FARMER),
            ),
        ]
        ordering = ["-date", "route__code", "collection_point__number", "farmer__full_name"]

    def clean(self):
        if self.route_id:
            self.branch = self.route.branch
        if self.source == CollectionSource.POINT:
            if not self.collection_point_id:
                raise ValidationError({"collection_point": "Select a collection point."})
            if self.farmer_id:
                raise ValidationError({"farmer": "Clear farmer when collecting from a collection point."})
            if self.route_id and self.collection_point.route_id != self.route_id:
                raise ValidationError("Collection point must belong to the selected route.")
        elif self.source == CollectionSource.FARMER:
            if not self.farmer_id:
                raise ValidationError({"farmer": "Select a farmer."})
            if self.collection_point_id:
                raise ValidationError({"collection_point": "Clear collection point when collecting from a farmer."})

    def save(self, *args, **kwargs):
        self.liters = (self.kg * MILK_LITER_FACTOR).quantize(Decimal("0.01"))
        self.full_clean()
        super().save(*args, **kwargs)

    def source_label(self):
        if self.source == CollectionSource.FARMER and self.farmer_id:
            return str(self.farmer)
        if self.collection_point_id:
            return str(self.collection_point)
        return "—"


class CollectionPointMilkReconcileChoice(models.Model):
    """Which milk total (point row vs farmer rollup) to use for payment on a given day."""

    class Source(models.TextChoices):
        POINT = "point", "Point"
        FARMER = "farmer", "Farmer"

    collection_point = models.ForeignKey(
        CollectionPoint,
        on_delete=models.CASCADE,
        related_name="milk_reconcile_choices",
    )
    route = models.ForeignKey(Route, on_delete=models.CASCADE, related_name="milk_reconcile_choices")
    date = models.DateField()
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.POINT)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="collection_point_milk_reconcile_choices",
        null=True,
        blank=True,
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "collection_point_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["collection_point", "route", "date"],
                name="uniq_collection_point_milk_reconcile_day",
            ),
        ]

    def __str__(self):
        return f"{self.collection_point_id} {self.date} → {self.source}"


class CollectionPointMilkReconcileSyncSnapshot(models.Model):
    """Previous collection-point kg stored before an explicit reconcile Sync (for reverse)."""

    collection_point = models.ForeignKey(
        CollectionPoint,
        on_delete=models.CASCADE,
        related_name="milk_reconcile_sync_snapshots",
    )
    route = models.ForeignKey(
        Route,
        on_delete=models.CASCADE,
        related_name="milk_reconcile_sync_snapshots",
    )
    date = models.DateField()
    previous_kg = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    had_point_row = models.BooleanField(default=False)
    synced_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="milk_reconcile_sync_snapshots",
        null=True,
        blank=True,
    )
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "collection_point_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["collection_point", "route", "date"],
                name="uniq_collection_point_milk_reconcile_sync_day",
            ),
        ]

    def __str__(self):
        return f"{self.collection_point_id} {self.date} prev={self.previous_kg}"


class MilkCollectionAdjustment(AuditModel):
    """Audit trail for kg add/subtract on a milk collection row (collection point or farmer)."""

    milk_collection = models.ForeignKey(
        MilkCollection,
        on_delete=models.CASCADE,
        related_name="kg_adjustments",
    )
    delta_kg = models.DecimalField(max_digits=10, decimal_places=2)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.milk_collection_id} {self.delta_kg} kg"


class MilkFactor(AuditModel):
    date = models.DateField()
    branch = models.ForeignKey(
        "branches.Branch",
        on_delete=models.PROTECT,
        related_name="milk_factors",
        null=True,
        blank=True,
        editable=False,
    )
    route = models.ForeignKey(Route, on_delete=models.PROTECT)
    source = models.CharField(
        max_length=10,
        choices=CollectionSource.choices,
        default=CollectionSource.POINT,
    )
    collection_point = models.ForeignKey(
        CollectionPoint,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    farmer = models.ForeignKey(
        Farmer,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    fat = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    snf = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    lr = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    alcohol = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    acidity = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    kq = models.DecimalField(max_digits=6, decimal_places=2, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["date", "route", "collection_point"],
                name="uniq_daily_route_point_factor",
                condition=models.Q(source=CollectionSource.POINT),
            ),
            models.UniqueConstraint(
                fields=["date", "route", "farmer"],
                name="uniq_daily_route_farmer_factor",
                condition=models.Q(source=CollectionSource.FARMER),
            ),
        ]
        ordering = ["-date", "route__code", "collection_point__number", "farmer__full_name"]

    def clean(self):
        if self.route_id:
            self.branch = self.route.branch
        if self.source == CollectionSource.POINT:
            if not self.collection_point_id:
                raise ValidationError({"collection_point": "Select a collection point."})
            if self.farmer_id:
                raise ValidationError({"farmer": "Clear farmer when recording factors for a collection point."})
            if self.route_id and self.collection_point.route_id != self.route_id:
                raise ValidationError("Collection point must belong to the selected route.")
        elif self.source == CollectionSource.FARMER:
            if not self.farmer_id:
                raise ValidationError({"farmer": "Select a farmer."})
            if self.collection_point_id:
                raise ValidationError({"collection_point": "Clear collection point when recording factors for a farmer."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def factor_target_label(self):
        if self.source == CollectionSource.FARMER and self.farmer_id:
            return str(self.farmer)
        if self.collection_point_id:
            return str(self.collection_point)
        return "—"
