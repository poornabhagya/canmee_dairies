from django.db import transaction
from rest_framework import serializers

from branches.models import Branch

from .models import ConsumptionSettlement, FarmerGoodsIssue, GRN, GRNItem, Supplier, SupplierTransaction
from .services import consume_goods, finalize_grn_totals, issue_goods, record_supplier_payment


class SupplierSerializer(serializers.ModelSerializer):
    branches = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Branch.objects.all(),
        required=False,
    )

    class Meta:
        model = Supplier
        fields = [
            "id",
            "name",
            "category",
            "contact_number",
            "address",
            "email",
            "status",
            "branches",
            "created_at",
        ]
        read_only_fields = ["created_at"]

    def create(self, validated_data):
        from branches.utils import get_allowed_branches_qs

        branches = validated_data.pop("branches", None)
        supplier = Supplier.objects.create(**validated_data)
        request = self.context.get("request")
        if branches is not None:
            if request and not request.user.is_superuser:
                allowed_ids = set(get_allowed_branches_qs(request.user).values_list("id", flat=True))
                branches = [b for b in branches if b.id in allowed_ids]
            supplier.branches.set(branches)
        elif request:
            branch_ids = []
            raw_branch = request.data.get("branch") or request.data.get("branch_id")
            if raw_branch and str(raw_branch).isdigit():
                branch_ids = [int(raw_branch)]
            elif not request.user.is_superuser:
                branch_ids = list(get_allowed_branches_qs(request.user).values_list("id", flat=True))
            if branch_ids:
                supplier.branches.set(branch_ids)
        return supplier


class GRNItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = GRNItem
        fields = [
            "product",
            "quantity",
            "free_quantity",
            "unit_price",
            "issuing_price",
            "line_discount_percent",
            "line_discount_amount",
        ]


class GRNSerializer(serializers.ModelSerializer):
    items = GRNItemSerializer(many=True, write_only=True)
    branch = serializers.PrimaryKeyRelatedField(queryset=Branch.objects.all(), required=True)
    supplier = serializers.PrimaryKeyRelatedField(
        queryset=Supplier.objects.exclude(category=Supplier.Category.RAW_MILK_SUPPLIER)
    )

    class Meta:
        model = GRN
        fields = [
            "id",
            "grn_number",
            "supplier",
            "branch",
            "grn_type",
            "date",
            "total_amount",
            "status",
            "discount_scope",
            "document_discount_percent",
            "document_discount_amount",
            "items",
        ]
        read_only_fields = ["id", "grn_number", "total_amount", "status"]

    @transaction.atomic
    def create(self, validated_data):
        items = validated_data.pop("items", [])
        grn = GRN.objects.create(**validated_data)
        for item in items:
            GRNItem.objects.create(grn=grn, **item)
        finalize_grn_totals(grn)
        grn.refresh_from_db()
        return grn


class ConfirmGRNSerializer(serializers.Serializer):
    grn_id = serializers.IntegerField()


class FarmerGoodsIssueSerializer(serializers.ModelSerializer):
    class Meta:
        model = FarmerGoodsIssue
        fields = ["product", "quantity", "issue_to_type", "issue_to_id", "from_branch", "date"]
        read_only_fields = ["date"]

    def validate_quantity(self, value):
        if value is None or value <= 0:
            raise serializers.ValidationError(
                "Quantity must be greater than zero. Negative stock is not allowed."
            )
        return value

    def create(self, validated_data):
        try:
            return issue_goods(**validated_data)
        except Exception as exc:
            raise serializers.ValidationError(str(exc))


class SupplierPaymentSerializer(serializers.Serializer):
    supplier = serializers.PrimaryKeyRelatedField(queryset=Supplier.objects.all())
    amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    description = serializers.CharField(required=False, allow_blank=True)

    def create(self, validated_data):
        try:
            return record_supplier_payment(
                supplier=validated_data["supplier"],
                amount=validated_data["amount"],
                description=validated_data.get("description", ""),
                reference="Payment API",
            )
        except Exception as exc:
            raise serializers.ValidationError(str(exc))


class SupplierTransactionSerializer(serializers.ModelSerializer):
    supplier_name = serializers.CharField(source="supplier.name", read_only=True)

    class Meta:
        model = SupplierTransaction
        fields = ["id", "supplier", "supplier_name", "date", "transaction_type", "amount", "reference", "description"]


class ConsumptionSettlementSerializer(serializers.ModelSerializer):
    class Meta:
        model = ConsumptionSettlement
        fields = ["product", "quantity", "source_type", "source_id", "date"]
        read_only_fields = ["date"]

    def create(self, validated_data):
        try:
            return consume_goods(**validated_data)
        except Exception as exc:
            raise serializers.ValidationError(str(exc))
