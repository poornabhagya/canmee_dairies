from datetime import date
from decimal import Decimal
from io import BytesIO

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin, UserPassesTestMixin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Sum
from django.db.models.deletion import ProtectedError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.views import View
from django.views.generic import CreateView, DeleteView, DetailView, FormView, ListView, UpdateView

from branches.utils import get_allowed_branches_qs
from canmee_dairies.formatting import format_money
from canmee_dairies.mixins import ModelFormPageTitleMixin, RedirectGetDeleteMixin

from .forms import (
    AssetCategoryForm,
    AssetForm,
    AssetLocationDeleteForm,
    AssetLocationForm,
    AssetModuleSettingsForm,
    AssetTransferForm,
    AssetTypeDeleteForm,
    AssetTypeForm,
    AssetValuationForm,
    AssetVerificationForm,
    DepreciationPolicyForm,
    DepreciationRunForm,
    LabelBatchForm,
)
from .models import (
    Asset,
    AssetCategory,
    AssetDepreciationEntry,
    AssetLabelBatch,
    AssetLocation,
    AssetModuleSettings,
    AssetType,
    AssetVerification,
    DepreciationPolicy,
    assets_for_user,
)
from .register_pdf import (
    build_fixed_asset_register_pdf,
    classified_asset_groups,
    classified_register_queryset,
)
from .services import (
    create_label_batch,
    depreciation_enabled,
    location_descendant_ids,
    reassign_and_delete_asset_location,
    reassign_and_delete_asset_type,
    next_asset_tag,
    record_valuation,
    record_verification,
    run_depreciation_for_month,
    transfer_asset,
)


def _can_manage_settings(user):
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return user.has_perm("assets.manage_asset_settings")


class AssetListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = Asset
    template_name = "assets/asset_list.html"
    context_object_name = "assets"
    permission_required = "assets.view_asset"

    def get_queryset(self):
        return assets_for_user(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        base = assets_for_user(self.request.user)
        ctx["scope"] = (self.request.GET.get("scope") or "all").strip().lower()
        ctx["branches"] = get_allowed_branches_qs(self.request.user).order_by("name")
        ctx["asset_types"] = AssetType.objects.filter(is_active=True)
        ctx["categories"] = AssetCategory.objects.filter(is_active=True).select_related(
            "asset_type"
        )
        ctx["category_type_rows"] = [
            {"id": c.pk, "name": c.name, "type_id": c.asset_type_id}
            for c in ctx["categories"]
        ]
        totals = base.aggregate(
            total_value=Sum("current_value"),
            total_cost=Sum("purchase_cost"),
        )
        total_cost = totals["total_cost"] or Decimal("0.00")
        total_value = totals["total_value"] or Decimal("0.00")
        written_down = total_cost - total_value if total_cost > total_value else Decimal("0.00")
        ctx["stats"] = {
            "total": base.count(),
            "main": base.filter(branch__isnull=True).count(),
            "branch": base.filter(branch__isnull=False).count(),
            "active": base.filter(status=Asset.Status.ACTIVE).count(),
            "in_storage": base.filter(status=Asset.Status.IN_STORAGE).count(),
            "under_maintenance": base.filter(status=Asset.Status.UNDER_MAINTENANCE).count(),
            "lost": base.filter(status=Asset.Status.LOST).count(),
            "disposed": base.filter(status=Asset.Status.DISPOSED).count(),
            "total_cost": total_cost,
            "total_value": total_value,
            "written_down": written_down,
            "nbv_pct": int((total_value / total_cost * 100)) if total_cost else 0,
        }
        ctx["depreciation_enabled"] = depreciation_enabled()
        ctx["status_choices"] = Asset.Status.choices
        ctx["transfer_form"] = AssetTransferForm(user=self.request.user)
        ctx["location_options"] = [
            {
                "id": loc.pk,
                "name": loc.name,
                "code": loc.code,
                "branch_id": loc.branch_id or "",
            }
            for loc in AssetLocation.objects.filter(is_active=True)
            .select_related("branch")
            .order_by("name")
        ]
        if self.request.user.has_perm("assets.change_asset") or self.request.user.has_perm(
            "assets.add_asset"
        ):
            ctx["asset_modal_form"] = AssetForm(user=self.request.user)
        default_type = AssetType.objects.filter(code="fixed", is_active=True).first()
        ctx["asset_modal_defaults"] = {
            "asset_tag": next_asset_tag(),
            "asset_type_id": default_type.pk if default_type else "",
            "status": Asset.Status.ACTIVE,
            "condition": Asset.Condition.GOOD,
            "quick_add_url": reverse("asset-quick-add"),
        }
        ctx["classified_groups"] = classified_asset_groups(
            classified_register_queryset(base)
        )
        return ctx


class AssetRegisterPDFView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "assets.view_asset"

    def get(self, request):
        qs = classified_register_queryset(assets_for_user(request.user))
        assets = list(qs)
        totals = qs.aggregate(
            total_value=Sum("current_value"),
            total_cost=Sum("purchase_cost"),
        )
        total_cost = totals["total_cost"] or Decimal("0.00")
        total_value = totals["total_value"] or Decimal("0.00")
        written_down = total_cost - total_value if total_cost > total_value else Decimal("0.00")
        buffer = BytesIO()
        build_fixed_asset_register_pdf(
            buffer,
            assets=assets,
            stats={
                "total": len(assets),
                "total_cost": total_cost,
                "total_value": total_value,
                "written_down": written_down,
            },
        )
        buffer.seek(0)
        response = HttpResponse(buffer, content_type="application/pdf")
        response["Content-Disposition"] = 'inline; filename="fixed_asset_register.pdf"'
        return response


def _fmt_date(value):
    if not value:
        return ""
    return f"{value.day} {value.strftime('%b')} {value.year}"


def serialize_asset(asset):
    cost = asset.purchase_cost or Decimal("0.00")
    book = asset.current_value or Decimal("0.00")
    written = cost - book if cost > book else Decimal("0.00")
    return {
        "id": asset.pk,
        "asset_tag": asset.asset_tag,
        "name": asset.name,
        "description": asset.description or "",
        "serial_number": asset.serial_number or "",
        "manufacturer": asset.manufacturer or "",
        "model_number": asset.model_number or "",
        "asset_type_id": asset.asset_type_id or "",
        "asset_type_name": asset.asset_type.name if asset.asset_type_id else "",
        "category_id": asset.category_id or "",
        "category_name": asset.category.name if asset.category_id else "",
        "branch_id": asset.branch_id or "",
        "branch_label": asset.branch_label,
        "location_id": asset.location_id or "",
        "location_name": asset.location.name if asset.location_id else "",
        "status": asset.status,
        "status_label": asset.get_status_display(),
        "condition": asset.condition,
        "condition_label": asset.get_condition_display(),
        "custodian_id": asset.custodian_id or "",
        "custodian_name": str(asset.custodian) if asset.custodian_id else "",
        "purchase_date": asset.purchase_date.isoformat() if asset.purchase_date else "",
        "purchase_date_display": _fmt_date(asset.purchase_date),
        "warranty_expiry": asset.warranty_expiry.isoformat() if asset.warranty_expiry else "",
        "warranty_expiry_display": _fmt_date(asset.warranty_expiry),
        "supplier_name": asset.supplier_name or "",
        "purchase_cost": str(cost),
        "purchase_cost_display": format_money(cost),
        "current_value": str(book),
        "current_value_display": format_money(book),
        "salvage_value": str(asset.salvage_value or Decimal("0.00")),
        "salvage_value_display": format_money(asset.salvage_value),
        "written_down_display": format_money(written),
        "depreciation_policy_id": asset.depreciation_policy_id or "",
        "depreciation_policy_name": str(asset.depreciation_policy) if asset.depreciation_policy_id else "",
        "useful_life_months": asset.useful_life_months or "",
        "notes": asset.notes or "",
        "account_url": reverse("asset-detail", args=[asset.pk]),
        "quick_edit_url": reverse("asset-quick-edit", args=[asset.pk]),
    }


def _asset_json_qs(user):
    return assets_for_user(user).select_related(
        "asset_type",
        "category",
        "branch",
        "location",
        "custodian",
        "depreciation_policy",
    )


def _category_type_rows(exclude_pk=None):
    qs = AssetCategory.objects.filter(is_active=True).only("id", "name", "asset_type_id")
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return [{"id": c.pk, "name": c.name, "type_id": c.asset_type_id} for c in qs]


class AssetCreateView(LoginRequiredMixin, PermissionRequiredMixin, ModelFormPageTitleMixin, CreateView):
    model = Asset
    form_class = AssetForm
    template_name = "assets/asset_form.html"
    permission_required = "assets.add_asset"
    success_url = reverse_lazy("asset-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        messages.success(self.request, "Asset created.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["category_type_rows"] = _category_type_rows()
        return ctx


class AssetUpdateView(LoginRequiredMixin, PermissionRequiredMixin, ModelFormPageTitleMixin, UpdateView):
    model = Asset
    form_class = AssetForm
    template_name = "assets/asset_form.html"
    permission_required = "assets.change_asset"
    success_url = reverse_lazy("asset-list")

    def get_queryset(self):
        return assets_for_user(self.request.user)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, "Asset updated.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["category_type_rows"] = _category_type_rows()
        return ctx


class AssetDetailView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = Asset
    template_name = "assets/asset_detail.html"
    context_object_name = "asset"
    permission_required = "assets.view_asset"

    def get_queryset(self):
        return assets_for_user(self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        asset = self.object
        verifications = asset.verifications.select_related("verified_by", "location")
        valuations = asset.valuations.select_related("valued_by")
        transfers = asset.transfers.select_related(
            "from_branch", "to_branch", "from_location", "to_location", "transferred_by"
        )
        depreciation_entries = asset.depreciation_entries.select_related("policy", "run_by")
        ctx["verifications"] = verifications
        ctx["valuations"] = valuations
        ctx["transfers"] = transfers
        ctx["depreciation_entries"] = depreciation_entries
        ctx["depreciation_enabled"] = depreciation_enabled()
        ctx["verify_form"] = AssetVerificationForm(
            initial={"condition": asset.condition, "location": asset.location}
        )
        ctx["valuation_form"] = AssetValuationForm(
            initial={"new_value": asset.current_value}
        )
        ctx["transfer_form"] = AssetTransferForm(user=self.request.user)
        latest_verify = verifications.first()
        cost = asset.purchase_cost or Decimal("0.00")
        book = asset.current_value or Decimal("0.00")
        written_down = cost - book if cost > book else Decimal("0.00")
        ctx["asset_stats"] = {
            "verifications": verifications.count(),
            "valuations": valuations.count(),
            "transfers": transfers.count(),
            "depreciation_entries": depreciation_entries.count(),
            "cost": cost,
            "book": book,
            "written_down": written_down,
            "nbv_pct": int((book / cost * 100)) if cost else 0,
            "latest_verify": latest_verify.verified_at if latest_verify else None,
        }
        return ctx


class AssetDetailJsonView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "assets.view_asset"

    def get(self, request, pk):
        asset = get_object_or_404(_asset_json_qs(request.user), pk=pk)
        return JsonResponse({"ok": True, "asset": serialize_asset(asset)})


class AssetQuickUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "assets.change_asset"

    def post(self, request, pk):
        asset = get_object_or_404(assets_for_user(request.user), pk=pk)
        form = AssetForm(request.POST, instance=asset, user=request.user)
        if not form.is_valid():
            errors = {name: [str(err) for err in msgs] for name, msgs in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errors}, status=400)
        form.save()
        return JsonResponse({"ok": True})


class AssetQuickStatusView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "assets.change_asset"

    def post(self, request, pk):
        asset = get_object_or_404(assets_for_user(request.user), pk=pk)
        status = (request.POST.get("status") or "").strip()
        valid = {choice[0] for choice in Asset.Status.choices}
        if status not in valid:
            return JsonResponse({"ok": False, "error": "Invalid status."}, status=400)
        asset.status = status
        asset.save(update_fields=["status", "updated_at"])
        return JsonResponse(
            {
                "ok": True,
                "status": asset.status,
                "label": asset.get_status_display(),
            }
        )


class AssetQuickCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "assets.add_asset"

    def post(self, request):
        form = AssetForm(request.POST, user=request.user)
        if not form.is_valid():
            errors = {name: [str(err) for err in msgs] for name, msgs in form.errors.items()}
            return JsonResponse({"ok": False, "errors": errors}, status=400)
        form.instance.created_by = request.user
        form.save()
        return JsonResponse({"ok": True})


class AssetDeleteView(
    LoginRequiredMixin, PermissionRequiredMixin, RedirectGetDeleteMixin, DeleteView
):
    model = Asset
    permission_required = "assets.delete_asset"
    success_url = reverse_lazy("asset-list")

    def get_queryset(self):
        return assets_for_user(self.request.user)

    def form_valid(self, form):
        messages.success(self.request, "Asset deleted.")
        return super().form_valid(form)


class AssetVerifyView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "assets.verify_assets"

    def post(self, request, pk):
        asset = get_object_or_404(assets_for_user(request.user), pk=pk)
        form = AssetVerificationForm(request.POST)
        if form.is_valid():
            record_verification(
                asset=asset,
                user=request.user,
                result=form.cleaned_data["result"],
                condition=form.cleaned_data.get("condition") or "",
                location=form.cleaned_data.get("location"),
                assessed_value=form.cleaned_data.get("assessed_value"),
                note=form.cleaned_data.get("note") or "",
            )
            messages.success(request, "Verification recorded.")
        else:
            messages.error(request, "Could not record verification.")
        return redirect("asset-detail", pk=pk)


class AssetValueView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "assets.verify_assets"

    def post(self, request, pk):
        asset = get_object_or_404(assets_for_user(request.user), pk=pk)
        form = AssetValuationForm(request.POST)
        if form.is_valid():
            record_valuation(
                asset=asset,
                new_value=form.cleaned_data["new_value"],
                user=request.user,
                reason=form.cleaned_data.get("reason") or "",
            )
            messages.success(request, "Valuation updated.")
        else:
            messages.error(request, "Could not update valuation.")
        return redirect("asset-detail", pk=pk)


class AssetTransferView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "assets.change_asset"

    def post(self, request, pk):
        asset = get_object_or_404(assets_for_user(request.user), pk=pk)
        form = AssetTransferForm(request.POST, user=request.user)
        if form.is_valid():
            transfer_asset(
                asset=asset,
                to_branch=form.cleaned_data.get("to_branch"),
                to_location=form.cleaned_data.get("to_location"),
                user=request.user,
                note=form.cleaned_data.get("note") or "",
            )
            messages.success(request, "Asset transferred.")
        else:
            messages.error(request, "Could not transfer asset.")
        return redirect("asset-detail", pk=pk)


class AssetBulkTransferView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "assets.change_asset"
    success_url = reverse_lazy("asset-list")

    def get(self, request, *args, **kwargs):
        return redirect(self.success_url)

    def post(self, request):
        ids = [int(x) for x in request.POST.getlist("asset_ids") if str(x).isdigit()]
        form = AssetTransferForm(request.POST, user=request.user)
        if not ids:
            messages.error(request, "Select at least one asset to transfer.")
            return redirect(self.success_url)
        if not form.is_valid():
            messages.error(request, "Could not transfer the selected assets.")
            return redirect(self.success_url)
        assets = list(assets_for_user(request.user).filter(pk__in=ids))
        if not assets:
            messages.error(request, "No transferable assets were selected.")
            return redirect(self.success_url)
        to_branch = form.cleaned_data.get("to_branch")
        to_location = form.cleaned_data.get("to_location")
        note = form.cleaned_data.get("note") or ""
        moved = 0
        skipped = 0
        with transaction.atomic():
            for asset in assets:
                same_branch = asset.branch_id == (to_branch.pk if to_branch else None)
                same_location = asset.location_id == (
                    to_location.pk if to_location else asset.location_id
                )
                if same_branch and (to_location is None or same_location):
                    skipped += 1
                    continue
                transfer_asset(
                    asset=asset,
                    to_branch=to_branch,
                    to_location=to_location,
                    user=request.user,
                    note=note,
                )
                moved += 1
        if moved:
            extra = f" {skipped} already at the destination." if skipped else ""
            messages.success(request, f"Transferred {moved} asset(s).{extra}")
        else:
            messages.info(request, "Selected assets are already at that destination.")
        return redirect(self.success_url)


class AssetTypeListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = AssetType
    template_name = "assets/type_list.html"
    context_object_name = "types"
    permission_required = "assets.view_assettype"

    def get_queryset(self):
        return AssetType.objects.annotate(
            asset_count=Count("assets", distinct=True),
            category_count=Count("categories", distinct=True),
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = list(ctx["types"])
        ctx["types"] = qs
        ctx["stats"] = {
            "total": len(qs),
            "active": sum(1 for t in qs if t.is_active),
            "inactive": sum(1 for t in qs if not t.is_active),
        }
        ctx["type_options"] = [{"id": t.pk, "name": t.name} for t in qs]
        return ctx


class AssetTypeCreateView(
    LoginRequiredMixin, PermissionRequiredMixin, ModelFormPageTitleMixin, CreateView
):
    model = AssetType
    form_class = AssetTypeForm
    template_name = "assets/type_form.html"
    permission_required = "assets.add_assettype"
    success_url = reverse_lazy("asset-type-list")

    def form_valid(self, form):
        messages.success(self.request, "Asset type created.")
        return super().form_valid(form)


class AssetTypeUpdateView(
    LoginRequiredMixin, PermissionRequiredMixin, ModelFormPageTitleMixin, UpdateView
):
    model = AssetType
    form_class = AssetTypeForm
    template_name = "assets/type_form.html"
    permission_required = "assets.change_assettype"
    success_url = reverse_lazy("asset-type-list")

    def form_valid(self, form):
        messages.success(self.request, "Asset type updated.")
        return super().form_valid(form)


class AssetTypeDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "assets.delete_assettype"
    success_url = reverse_lazy("asset-type-list")

    def get(self, request, *args, **kwargs):
        return redirect(self.success_url)

    def post(self, request, pk):
        obj = get_object_or_404(AssetType, pk=pk)
        asset_count = Asset.objects.filter(asset_type=obj).count()
        category_count = AssetCategory.objects.filter(asset_type=obj).count()
        form = AssetTypeDeleteForm(
            request.POST,
            old_type=obj,
            require_replacement=bool(asset_count or category_count),
        )
        ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        def fail(message):
            if ajax:
                return JsonResponse({"error": message}, status=400)
            messages.error(request, message)
            return redirect(self.success_url)

        if AssetType.objects.exclude(pk=obj.pk).count() == 0:
            return fail("Cannot delete the last asset type.")
        if not form.is_valid():
            err = form.errors.get("replacement")
            return fail(err[0] if err else "Could not delete this type.")
        try:
            result = reassign_and_delete_asset_type(
                old_type=obj,
                new_type=form.cleaned_data.get("replacement"),
            )
        except ValidationError as exc:
            return fail("; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
        moved = result["assets"] + result["categories"]
        if moved:
            messages.success(
                request,
                f"Type deleted. Moved {result['assets']} asset(s) and {result['categories']} categor"
                f"{'y' if result['categories'] == 1 else 'ies'}.",
            )
        else:
            messages.success(request, "Asset type deleted.")
        if ajax:
            return JsonResponse({"ok": True, "redirect": str(self.success_url)})
        return redirect(self.success_url)


class AssetCategoryListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = AssetCategory
    template_name = "assets/category_list.html"
    context_object_name = "categories"
    permission_required = "assets.view_assetcategory"

    def get_queryset(self):
        return AssetCategory.objects.select_related("parent", "asset_type").annotate(
            asset_count=Count("assets", distinct=True),
            child_count=Count("children", distinct=True),
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = ctx["categories"]
        ctx["asset_types"] = AssetType.objects.filter(is_active=True)
        ctx["stats"] = {
            "total": qs.count() if hasattr(qs, "count") else len(qs),
            "active": sum(1 for c in qs if c.is_active),
            "inactive": sum(1 for c in qs if not c.is_active),
        }
        return ctx


class AssetCategoryCreateView(
    LoginRequiredMixin, PermissionRequiredMixin, ModelFormPageTitleMixin, CreateView
):
    model = AssetCategory
    form_class = AssetCategoryForm
    template_name = "assets/category_form.html"
    permission_required = "assets.add_assetcategory"
    success_url = reverse_lazy("asset-category-list")

    def form_valid(self, form):
        messages.success(self.request, "Category created.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["category_parent_rows"] = _category_type_rows()
        return ctx

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["category_parent_rows"] = _category_type_rows()
        return ctx


class AssetCategoryUpdateView(
    LoginRequiredMixin, PermissionRequiredMixin, ModelFormPageTitleMixin, UpdateView
):
    model = AssetCategory
    form_class = AssetCategoryForm
    template_name = "assets/category_form.html"
    permission_required = "assets.change_assetcategory"
    success_url = reverse_lazy("asset-category-list")

    def form_valid(self, form):
        messages.success(self.request, "Category updated.")
        response = super().form_valid(form)
        Asset.objects.filter(category=self.object).exclude(
            asset_type=self.object.asset_type
        ).update(asset_type=self.object.asset_type)
        return response

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["category_parent_rows"] = _category_type_rows(exclude_pk=self.object.pk)
        return ctx


class AssetCategoryDeleteView(
    LoginRequiredMixin, PermissionRequiredMixin, RedirectGetDeleteMixin, DeleteView
):
    model = AssetCategory
    permission_required = "assets.delete_assetcategory"
    success_url = reverse_lazy("asset-category-list")

    def _ajax(self):
        return self.request.headers.get("X-Requested-With") == "XMLHttpRequest"

    def _fail(self, message):
        if self._ajax():
            return JsonResponse({"error": message}, status=400)
        messages.error(self.request, message)
        return redirect(self.success_url)

    def form_valid(self, form):
        obj = self.object
        asset_count = obj.assets.count()
        child_count = obj.children.count()
        if asset_count or child_count:
            return self._fail(
                f"Cannot delete this category. It is used by {asset_count} asset(s) "
                f"and {child_count} sub-categor{'y' if child_count == 1 else 'ies'}."
            )
        try:
            response = super().form_valid(form)
        except ProtectedError:
            return self._fail("Cannot delete this category while related records still exist.")
        messages.success(self.request, "Category deleted.")
        return response


class AssetLocationListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = AssetLocation
    template_name = "assets/location_list.html"
    context_object_name = "locations"
    permission_required = "assets.view_assetlocation"

    def get_queryset(self):
        return AssetLocation.objects.select_related("branch", "parent").annotate(
            asset_count=Count("assets", distinct=True),
            child_count=Count("sub_locations", distinct=True),
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = list(ctx["locations"])
        ctx["locations"] = qs
        ctx["stats"] = {
            "total": len(qs),
            "main": sum(1 for loc in qs if not loc.branch_id),
            "branch": sum(1 for loc in qs if loc.branch_id),
            "sub": sum(1 for loc in qs if loc.parent_id),
            "active": sum(1 for loc in qs if loc.is_active),
            "inactive": sum(1 for loc in qs if not loc.is_active),
        }
        ctx["branches"] = get_allowed_branches_qs(self.request.user).order_by("name")
        ctx["location_options"] = [
            {
                "id": loc.pk,
                "name": loc.name,
                "code": loc.code,
                "branch": loc.branch.name if loc.branch_id else "Head Office",
                "parent_id": loc.parent_id,
                "is_active": loc.is_active,
            }
            for loc in qs
        ]
        return ctx


class AssetLocationCreateView(
    LoginRequiredMixin, PermissionRequiredMixin, ModelFormPageTitleMixin, CreateView
):
    model = AssetLocation
    form_class = AssetLocationForm
    template_name = "assets/location_form.html"
    permission_required = "assets.add_assetlocation"
    success_url = reverse_lazy("asset-location-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, "Location created.")
        return super().form_valid(form)


class AssetLocationUpdateView(
    LoginRequiredMixin, PermissionRequiredMixin, ModelFormPageTitleMixin, UpdateView
):
    model = AssetLocation
    form_class = AssetLocationForm
    template_name = "assets/location_form.html"
    permission_required = "assets.change_assetlocation"
    success_url = reverse_lazy("asset-location-list")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, "Location updated.")
        return super().form_valid(form)


class AssetLocationDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "assets.delete_assetlocation"
    success_url = reverse_lazy("asset-location-list")

    def get(self, request, *args, **kwargs):
        return redirect(self.success_url)

    def post(self, request, pk):
        obj = get_object_or_404(AssetLocation, pk=pk)
        asset_count = Asset.objects.filter(location=obj).count()
        child_count = AssetLocation.objects.filter(parent=obj).count()
        exclude_ids = location_descendant_ids(obj) if child_count else set()
        form = AssetLocationDeleteForm(
            request.POST,
            old_location=obj,
            require_replacement=bool(asset_count or child_count),
            exclude_ids=exclude_ids,
        )
        ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

        def fail(message):
            if ajax:
                return JsonResponse({"error": message}, status=400)
            messages.error(request, message)
            return redirect(self.success_url)

        if not form.is_valid():
            err = form.errors.get("replacement")
            return fail(err[0] if err else "Could not delete this location.")
        try:
            result = reassign_and_delete_asset_location(
                old_location=obj,
                new_location=form.cleaned_data.get("replacement"),
            )
        except ValidationError as exc:
            return fail("; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
        except ProtectedError:
            return fail("Cannot delete this location while related records still exist.")
        moved = result["assets"] + result["children"]
        if moved:
            messages.success(
                request,
                f"Location deleted. Moved {result['assets']} asset(s) and {result['children']} "
                f"sub-location{'s' if result['children'] != 1 else ''}.",
            )
        else:
            messages.success(request, "Location deleted.")
        if ajax:
            return JsonResponse({"ok": True, "redirect": str(self.success_url)})
        return redirect(self.success_url)


class AssetLabelGenerateView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    template_name = "assets/label_generate.html"
    form_class = LabelBatchForm
    permission_required = "assets.print_asset_labels"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = assets_for_user(self.request.user).filter(
            status__in=[Asset.Status.ACTIVE, Asset.Status.IN_STORAGE]
        )
        ids = self.request.GET.getlist("ids")
        if ids:
            qs = qs.filter(pk__in=[i for i in ids if str(i).isdigit()])
        ctx["assets"] = qs[:200]
        ctx["recent_batches"] = AssetLabelBatch.objects.select_related("created_by")[:10]
        return ctx

    def form_valid(self, form):
        ids = form.cleaned_data["asset_ids"]
        if not ids:
            # fall back to posted checkboxes
            ids = [
                int(x)
                for x in self.request.POST.getlist("selected_assets")
                if str(x).isdigit()
            ]
        assets = list(assets_for_user(self.request.user).filter(pk__in=ids))
        if not assets:
            messages.error(self.request, "Select at least one asset.")
            return redirect("asset-label-generate")
        batch = create_label_batch(
            assets=assets, user=self.request.user, note=form.cleaned_data.get("note") or ""
        )
        messages.success(self.request, f"Label batch #{batch.pk} created ({batch.label_count} labels).")
        return redirect("asset-label-print", pk=batch.pk)


class AssetLabelPrintView(LoginRequiredMixin, PermissionRequiredMixin, DetailView):
    model = AssetLabelBatch
    template_name = "assets/label_print.html"
    context_object_name = "batch"
    permission_required = "assets.print_asset_labels"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["assets"] = self.object.assets.select_related("branch", "category", "location")
        return ctx


class DepreciationPolicyListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    model = DepreciationPolicy
    template_name = "assets/policy_list.html"
    context_object_name = "policies"

    def test_func(self):
        return depreciation_enabled() and (
            self.request.user.is_superuser
            or self.request.user.has_perm("assets.view_depreciationpolicy")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = ctx["policies"]
        ctx["stats"] = {
            "total": qs.count(),
            "active": qs.filter(is_active=True).count(),
            "straight": qs.filter(method=DepreciationPolicy.Method.STRAIGHT_LINE).count(),
            "reducing": qs.filter(method=DepreciationPolicy.Method.REDUCING_BALANCE).count(),
        }
        return ctx


class DepreciationPolicyCreateView(
    LoginRequiredMixin, UserPassesTestMixin, ModelFormPageTitleMixin, CreateView
):
    model = DepreciationPolicy
    form_class = DepreciationPolicyForm
    template_name = "assets/policy_form.html"
    success_url = reverse_lazy("asset-policy-list")

    def test_func(self):
        return depreciation_enabled() and (
            self.request.user.is_superuser
            or self.request.user.has_perm("assets.add_depreciationpolicy")
        )

    def form_valid(self, form):
        messages.success(self.request, "Depreciation policy created.")
        return super().form_valid(form)


class DepreciationPolicyUpdateView(
    LoginRequiredMixin, UserPassesTestMixin, ModelFormPageTitleMixin, UpdateView
):
    model = DepreciationPolicy
    form_class = DepreciationPolicyForm
    template_name = "assets/policy_form.html"
    success_url = reverse_lazy("asset-policy-list")

    def test_func(self):
        return depreciation_enabled() and (
            self.request.user.is_superuser
            or self.request.user.has_perm("assets.change_depreciationpolicy")
        )

    def form_valid(self, form):
        messages.success(self.request, "Depreciation policy updated.")
        return super().form_valid(form)


class DepreciationRunView(LoginRequiredMixin, UserPassesTestMixin, FormView):
    template_name = "assets/depreciation_run.html"
    form_class = DepreciationRunForm
    success_url = reverse_lazy("asset-depreciation-run")

    def test_func(self):
        return depreciation_enabled() and (
            self.request.user.is_superuser
            or self.request.user.has_perm("assets.run_asset_depreciation")
        )

    def get_initial(self):
        today = date.today()
        return {"period_month": f"{today.year:04d}-{today.month:02d}"}

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        recent_list = list(
            AssetDepreciationEntry.objects.select_related("asset", "policy", "run_by")[:50]
        )
        ctx["recent_entries"] = recent_list
        ctx["run_stats"] = {
            "entries": len(recent_list),
            "amount": sum((e.amount or Decimal("0.00")) for e in recent_list),
        }
        return ctx

    def form_valid(self, form):
        try:
            result = run_depreciation_for_month(
                period_start=form.cleaned_data["period_month"],
                user=self.request.user,
                asset_qs=assets_for_user(self.request.user),
            )
            messages.success(
                self.request,
                f"Depreciation run complete: {result['created']} entries, {result['skipped']} skipped.",
            )
        except ValidationError as exc:
            messages.error(self.request, "; ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
        return super().form_valid(form)


class AssetSettingsView(LoginRequiredMixin, UserPassesTestMixin, UpdateView):
    model = AssetModuleSettings
    form_class = AssetModuleSettingsForm
    template_name = "assets/settings.html"
    success_url = reverse_lazy("asset-settings")

    def test_func(self):
        return _can_manage_settings(self.request.user)

    def get_object(self, queryset=None):
        return AssetModuleSettings.get_solo()

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        messages.success(self.request, "Asset settings saved.")
        return super().form_valid(form)


class AssetVerificationListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = AssetVerification
    template_name = "assets/verification_list.html"
    context_object_name = "verifications"
    permission_required = "assets.verify_assets"

    def get_queryset(self):
        asset_ids = assets_for_user(self.request.user).values_list("pk", flat=True)
        return AssetVerification.objects.filter(asset_id__in=asset_ids).select_related(
            "asset", "verified_by", "location", "asset__branch", "asset__category"
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = ctx["verifications"]
        ctx["stats"] = {
            "total": qs.count(),
            "found": qs.filter(result=AssetVerification.Result.FOUND).count(),
            "missing": qs.filter(result=AssetVerification.Result.MISSING).count(),
            "damaged": qs.filter(result=AssetVerification.Result.DAMAGED).count(),
        }
        return ctx
