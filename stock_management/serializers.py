from rest_framework import serializers

from branches.utils import get_user_branch_ids

from .models import BranchStock, MainStock, Product, StockIssue, StockTransfer
from .services import issue_from_branch_stock, transfer_from_main_to_branch


class ProductSerializer(serializers.ModelSerializer):
    class Meta:
        model = Product
        fields = ["id", "name", "category", "unit", "description", "status", "created_at", "updated_at"]
        read_only_fields = ["id", "created_at", "updated_at"]


class StockTransferSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockTransfer
        fields = ["id", "product", "quantity", "from_location", "to_branch", "date", "created_by"]
        read_only_fields = ["id", "from_location", "date", "created_by"]

    def validate_to_branch(self, value):
        user = self.context["request"].user if self.context.get("request") else None
        if user and user.is_authenticated and not user.is_superuser:
            allowed_ids = get_user_branch_ids(user) or []
            if value.id not in allowed_ids:
                raise serializers.ValidationError("You do not have access to this branch.")
        return value

    def create(self, validated_data):
        user = self.context["request"].user if self.context.get("request") else None
        return transfer_from_main_to_branch(created_by=user, actor=user, **validated_data)


class StockIssueSerializer(serializers.ModelSerializer):
    class Meta:
        model = StockIssue
        fields = ["id", "product", "quantity", "branch", "issued_to_type", "issued_to_id", "date"]
        read_only_fields = ["id", "date"]

    def validate_branch(self, value):
        user = self.context["request"].user if self.context.get("request") else None
        if user and user.is_authenticated and not user.is_superuser:
            allowed_ids = get_user_branch_ids(user) or []
            if value.id not in allowed_ids:
                raise serializers.ValidationError("You do not have access to this branch.")
        return value

    def create(self, validated_data):
        user = self.context["request"].user if self.context.get("request") else None
        return issue_from_branch_stock(actor=user, **validated_data)


class MainStockSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    unit = serializers.CharField(source="product.unit", read_only=True)

    class Meta:
        model = MainStock
        fields = ["product", "product_name", "unit", "quantity", "updated_at"]


class BranchStockSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", read_only=True)
    unit = serializers.CharField(source="product.unit", read_only=True)
    branch_name = serializers.CharField(source="branch.name", read_only=True)

    class Meta:
        model = BranchStock
        fields = ["branch", "branch_name", "product", "product_name", "unit", "quantity", "updated_at"]
