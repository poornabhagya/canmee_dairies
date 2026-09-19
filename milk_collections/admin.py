from django.contrib import admin
from .models import MilkCollection, MilkCollectionAdjustment, MilkFactor


class MilkCollectionAdjustmentInline(admin.TabularInline):
    model = MilkCollectionAdjustment
    extra = 0
    readonly_fields = ("created_at", "updated_at")
    raw_id_fields = ("created_by",)


@admin.register(MilkCollection)
class MilkCollectionAdmin(admin.ModelAdmin):
    list_display = ("date", "route", "source", "collection_point", "farmer", "kg", "liters", "created_by")
    list_filter = ("source", "date")
    inlines = (MilkCollectionAdjustmentInline,)


@admin.register(MilkFactor)
class MilkFactorAdmin(admin.ModelAdmin):
    list_display = ("date", "route", "source", "collection_point", "farmer", "fat", "snf", "lr")
    list_filter = ("source", "date")
