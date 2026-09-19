import csv
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin, UserPassesTestMixin
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.db.models import Count, Q, Sum
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, FormView, ListView, TemplateView, UpdateView
from rest_framework import generics
from rest_framework.permissions import DjangoModelPermissions, IsAuthenticated

from branches.utils import get_allowed_branches_qs, get_user_branch_ids
from branches.models import Branch
from canmee_dairies.formatting import format_money
from suppliers.models import FarmerGoodsIssue, FarmerGoodsStock, GRN, GRNItem
from .forms import (
    ProductCategoryForm,
    ProductForm,
    StockCountCreateForm,
    StockIssueForm,
    StockTransferForm,
    StockTransferNoteForm,
    get_stock_transfer_line_formset,
)
from .models import (
    BranchStock,
    MainStock,
    Product,
    ProductCategory,
    StockCount,
    StockCountLine,
    StockIssue,
    StockLog,
    StockTransfer,
    StockTransferLine,
    StockTransferNote,
)
from .serializers import (
    BranchStockSerializer,
    MainStockSerializer,
    ProductSerializer,
    StockIssueSerializer,
    StockTransferSerializer,
)
from .services import (
    create_stock_count,
    create_stock_transfer_note,
    delete_stock_transfer_note,
    issue_from_branch_stock,
    post_stock_count,
    refresh_stock_count_book_quantities,
    save_stock_count_physical_quantities,
    transfer_from_main_to_branch,
    transfer_note_priced_lines,
    update_stock_transfer_note,
)


class StockOverviewView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "stock_management/stock_overview.html"
    permission_required = "stock_management.view_mainstock"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["main_stocks"] = MainStock.objects.select_related("product").order_by("product__name")
        branch_ids = get_user_branch_ids(self.request.user)
        all_branches = Branch.objects.order_by("name")
        branch_stocks = BranchStock.objects.select_related("branch", "product")
        if branch_ids is not None:
            branch_stocks = branch_stocks.filter(branch_id__in=branch_ids)
        ctx["branch_stocks"] = branch_stocks.order_by(
            "branch__name", "product__name"
        )
        # Branch-wise stock report tabs.
        branch_tab_qs = get_allowed_branches_qs(self.request.user).order_by("name")
        bs_product = (self.request.GET.get("bs_product") or "").strip()
        bs_product_id = int(bs_product) if bs_product.isdigit() else None
        bs_branch = (self.request.GET.get("bs_branch") or "").strip()
        # Received = confirmed GRN + incoming transfer note (and legacy transfer) into branch.
        grn_rows = (
            GRNItem.objects.filter(
                grn__status=GRN.Status.CONFIRMED,
                grn__branch__in=branch_tab_qs,
            )
            .values("grn__branch_id", "product_id")
            .annotate(total=Sum("quantity"))
        )
        incoming_transfer_rows = (
            StockTransferLine.objects.filter(note__to_branch__in=branch_tab_qs)
            .values("note__to_branch_id", "product_id")
            .annotate(total=Sum("quantity"))
        )
        incoming_legacy_transfer_rows = (
            StockTransfer.objects.filter(to_branch__in=branch_tab_qs)
            .values("to_branch_id", "product_id")
            .annotate(total=Sum("quantity"))
        )
        # Transfer = total moved OUT from this branch through transfer notes.
        outgoing_transfer_rows = (
            StockTransferLine.objects.filter(note__from_branch__in=branch_tab_qs)
            .values("note__from_branch_id", "product_id")
            .annotate(total=Sum("quantity"))
        )
        # Point/Farmer = stock issue + farmer goods issue (to point/farmer) from this branch.
        stock_issue_rows = (
            StockIssue.objects.filter(branch__in=branch_tab_qs)
            .values("branch_id", "product_id")
            .annotate(total=Sum("quantity"))
        )
        fg_issue_rows = (
            FarmerGoodsIssue.objects.filter(
                from_branch__in=branch_tab_qs,
                issue_to_type__in=[
                    FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
                    FarmerGoodsIssue.IssueToType.FARMER,
                ],
            )
            .exclude(driver_status=FarmerGoodsIssue.DriverStatus.REJECTED)
            .values("from_branch_id", "product_id")
            .annotate(total=Sum("quantity"))
        )
        received_map = {}
        for r in grn_rows:
            key = (r["grn__branch_id"], r["product_id"])
            received_map[key] = received_map.get(key, Decimal("0")) + (r["total"] or Decimal("0"))
        for r in incoming_transfer_rows:
            key = (r["note__to_branch_id"], r["product_id"])
            received_map[key] = received_map.get(key, Decimal("0")) + (r["total"] or Decimal("0"))
        for r in incoming_legacy_transfer_rows:
            key = (r["to_branch_id"], r["product_id"])
            received_map[key] = received_map.get(key, Decimal("0")) + (r["total"] or Decimal("0"))

        transfer_map = {
            (r["note__from_branch_id"], r["product_id"]): (r["total"] or Decimal("0"))
            for r in outgoing_transfer_rows
        }
        issue_map = {
            (r["branch_id"], r["product_id"]): (r["total"] or Decimal("0"))
            for r in stock_issue_rows
        }
        for r in fg_issue_rows:
            key = (r["from_branch_id"], r["product_id"])
            issue_map[key] = issue_map.get(key, Decimal("0")) + (r["total"] or Decimal("0"))

        product_ids = set(branch_stocks.values_list("product_id", flat=True))
        product_ids |= {r["product_id"] for r in grn_rows}
        product_ids |= {r["product_id"] for r in incoming_transfer_rows}
        product_ids |= {r["product_id"] for r in incoming_legacy_transfer_rows}
        product_ids |= {r["product_id"] for r in outgoing_transfer_rows}
        product_ids |= {r["product_id"] for r in stock_issue_rows}
        product_ids |= {r["product_id"] for r in fg_issue_rows}
        product_meta = {
            p["id"]: {"name": p["name"], "unit": p["unit"]}
            for p in Product.objects.filter(id__in=product_ids).values("id", "name", "unit")
        }
        branch_tabs = []
        active_branch_id = None
        if bs_branch.isdigit():
            candidate = int(bs_branch)
            if branch_tab_qs.filter(pk=candidate).exists():
                active_branch_id = candidate
        for b in branch_tab_qs:
            pids = sorted(
                {
                    pid
                    for (bid, pid) in received_map.keys()
                    if bid == b.id
                }
                | {
                    pid
                    for (bid, pid) in issue_map.keys()
                    if bid == b.id
                }
                | {
                    pid
                    for (bid, pid) in transfer_map.keys()
                    if bid == b.id
                },
                key=lambda x: product_meta.get(x, {}).get("name", ""),
            )
            if bs_product_id is not None:
                pids = [pid for pid in pids if pid == bs_product_id]
            rows = []
            for idx, pid in enumerate(pids, start=1):
                meta = product_meta.get(pid, {"name": f"Product #{pid}", "unit": ""})
                received = received_map.get((b.id, pid), Decimal("0"))
                issued_pf = issue_map.get((b.id, pid), Decimal("0"))
                transfer_qty = transfer_map.get((b.id, pid), Decimal("0"))
                available = received - (transfer_qty + issued_pf)
                rows.append(
                    {
                        "index": idx,
                        "product_id": pid,
                        "product_name": meta["name"],
                        "unit": meta["unit"] or "",
                        "received": received,
                        "transfer_qty": transfer_qty,
                        "point_farmer": issued_pf,
                        "available": available,
                    }
                )
            is_active = (
                (active_branch_id is not None and b.id == active_branch_id)
                or (active_branch_id is None and not branch_tabs)
            )
            branch_tabs.append(
                {"id": b.id, "name": b.name, "rows": rows, "is_active": is_active}
            )
        ctx["branch_tabs"] = branch_tabs
        ctx["branch_products"] = Product.objects.filter(id__in=product_ids).order_by("name")
        ctx["branch_filters"] = {
            "product": bs_product if bs_product_id is not None else "",
            "branch": str(active_branch_id or ""),
        }
        # FARMER_GOODS GRNs post here (not MainStock). CORPORATE GRNs post to MainStock.
        ctx["farmer_goods_stocks"] = FarmerGoodsStock.objects.select_related("product").order_by(
            "product__name"
        )
        # Farmer goods periodic stock-movement report (received vs issued to branches).
        from_date = (self.request.GET.get("fg_from_date") or "").strip()
        to_date = (self.request.GET.get("fg_to_date") or "").strip()
        fg_product = (self.request.GET.get("fg_product") or "").strip()
        from_day = None
        to_day = None
        try:
            if from_date:
                from_day = datetime.strptime(from_date, "%Y-%m-%d").date()
        except ValueError:
            from_date = ""
        try:
            if to_date:
                to_day = datetime.strptime(to_date, "%Y-%m-%d").date()
        except ValueError:
            to_date = ""

        received_qs = (
            GRNItem.objects.filter(
                grn__status=GRN.Status.CONFIRMED,
                grn__grn_type__in=[
                    GRN.GRNType.FARMER_GOODS,
                    GRN.GRNType.STOCK_CORRECTION,
                ],
            )
            .values("product_id", "product__name", "product__unit")
            .annotate(total=Sum("quantity"))
        )
        if fg_product.isdigit():
            received_qs = received_qs.filter(product_id=int(fg_product))
        if from_day:
            received_qs = received_qs.filter(grn__date__gte=from_day)
        if to_day:
            received_qs = received_qs.filter(grn__date__lte=to_day)
        received_rows = list(received_qs)

        issued_branch_qs = FarmerGoodsIssue.objects.filter(
            issue_to_type=FarmerGoodsIssue.IssueToType.BRANCH
        )
        if fg_product.isdigit():
            issued_branch_qs = issued_branch_qs.filter(product_id=int(fg_product))
        if from_day:
            issued_branch_qs = issued_branch_qs.filter(
                date__gte=timezone.make_aware(datetime.combine(from_day, time.min))
            )
        if to_day:
            issued_branch_qs = issued_branch_qs.filter(
                date__lt=timezone.make_aware(
                    datetime.combine(to_day + timedelta(days=1), time.min)
                )
            )
        issued_rows = list(
            issued_branch_qs.values("product_id", "issue_to_id").annotate(total=Sum("quantity"))
        )
        # Branch availability aligned with Branch Stock tab formula:
        # available = received (GRN + incoming transfer) - (transfer out + point/farmer issues)
        fg_product_id = int(fg_product) if fg_product.isdigit() else None
        fg_grn_branch_qs = GRNItem.objects.filter(
            grn__status=GRN.Status.CONFIRMED,
            grn__branch__in=all_branches,
        )
        fg_incoming_transfer_qs = StockTransferLine.objects.filter(note__to_branch__in=all_branches)
        fg_incoming_legacy_qs = StockTransfer.objects.filter(to_branch__in=all_branches)
        fg_outgoing_transfer_qs = StockTransferLine.objects.filter(note__from_branch__in=all_branches)
        fg_stock_issue_qs = StockIssue.objects.filter(branch__in=all_branches)
        fg_pf_issue_branch_qs = FarmerGoodsIssue.objects.filter(
            from_branch__in=all_branches,
            issue_to_type__in=[
                FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
                FarmerGoodsIssue.IssueToType.FARMER,
            ],
        ).exclude(driver_status=FarmerGoodsIssue.DriverStatus.REJECTED)
        if fg_product_id:
            fg_grn_branch_qs = fg_grn_branch_qs.filter(product_id=fg_product_id)
            fg_incoming_transfer_qs = fg_incoming_transfer_qs.filter(product_id=fg_product_id)
            fg_incoming_legacy_qs = fg_incoming_legacy_qs.filter(product_id=fg_product_id)
            fg_outgoing_transfer_qs = fg_outgoing_transfer_qs.filter(product_id=fg_product_id)
            fg_stock_issue_qs = fg_stock_issue_qs.filter(product_id=fg_product_id)
            fg_pf_issue_branch_qs = fg_pf_issue_branch_qs.filter(product_id=fg_product_id)
        fg_grn_branch_rows = list(
            fg_grn_branch_qs.values("grn__branch_id", "product_id").annotate(total=Sum("quantity"))
        )
        fg_incoming_transfer_rows = list(
            fg_incoming_transfer_qs.values("note__to_branch_id", "product_id").annotate(
                total=Sum("quantity")
            )
        )
        fg_incoming_legacy_transfer_rows = list(
            fg_incoming_legacy_qs.values("to_branch_id", "product_id").annotate(total=Sum("quantity"))
        )
        fg_outgoing_transfer_rows = list(
            fg_outgoing_transfer_qs.values("note__from_branch_id", "product_id").annotate(
                total=Sum("quantity")
            )
        )
        fg_stock_issue_rows = list(
            fg_stock_issue_qs.values("branch_id", "product_id").annotate(total=Sum("quantity"))
        )
        fg_pf_issue_branch_rows = list(
            fg_pf_issue_branch_qs.values("from_branch_id", "product_id").annotate(total=Sum("quantity"))
        )
        issued_pf_qs = FarmerGoodsIssue.objects.filter(
            issue_to_type__in=[
                FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
                FarmerGoodsIssue.IssueToType.FARMER,
            ]
        ).exclude(driver_status=FarmerGoodsIssue.DriverStatus.REJECTED)
        if fg_product_id:
            issued_pf_qs = issued_pf_qs.filter(product_id=fg_product_id)
        if from_day:
            issued_pf_qs = issued_pf_qs.filter(
                date__gte=timezone.make_aware(datetime.combine(from_day, time.min))
            )
        if to_day:
            issued_pf_qs = issued_pf_qs.filter(
                date__lt=timezone.make_aware(
                    datetime.combine(to_day + timedelta(days=1), time.min)
                )
            )
        issued_pf_rows = list(
            issued_pf_qs.values("product_id").annotate(total=Sum("quantity"))
        )

        branch_cols = list(all_branches.values("id", "name"))
        received_map = {}
        product_meta = {}
        for r in received_rows:
            pid = r["product_id"]
            qty = r["total"] or Decimal("0")
            received_map[pid] = qty
            product_meta[pid] = {"name": r["product__name"], "unit": r["product__unit"]}

        issue_map = {}
        for r in issued_rows:
            pid = r["product_id"]
            bid = r["issue_to_id"]
            qty = r["total"] or Decimal("0")
            issue_map[(pid, bid)] = qty
        fg_received_branch_map = {}
        for r in fg_grn_branch_rows:
            key = (r["product_id"], r["grn__branch_id"])
            fg_received_branch_map[key] = fg_received_branch_map.get(key, Decimal("0")) + (
                r["total"] or Decimal("0")
            )
        for r in fg_incoming_transfer_rows:
            key = (r["product_id"], r["note__to_branch_id"])
            fg_received_branch_map[key] = fg_received_branch_map.get(key, Decimal("0")) + (
                r["total"] or Decimal("0")
            )
        for r in fg_incoming_legacy_transfer_rows:
            key = (r["product_id"], r["to_branch_id"])
            fg_received_branch_map[key] = fg_received_branch_map.get(key, Decimal("0")) + (
                r["total"] or Decimal("0")
            )
        fg_transfer_out_map = {}
        for r in fg_outgoing_transfer_rows:
            fg_transfer_out_map[(r["product_id"], r["note__from_branch_id"])] = r["total"] or Decimal("0")
        fg_issue_out_map = {}
        for r in fg_stock_issue_rows:
            fg_issue_out_map[(r["product_id"], r["branch_id"])] = r["total"] or Decimal("0")
        for r in fg_pf_issue_branch_rows:
            key = (r["product_id"], r["from_branch_id"])
            fg_issue_out_map[key] = fg_issue_out_map.get(key, Decimal("0")) + (r["total"] or Decimal("0"))
        branch_avail_map = {}
        branch_keys = set(fg_received_branch_map.keys()) | set(fg_transfer_out_map.keys()) | set(
            fg_issue_out_map.keys()
        )
        for key in branch_keys:
            branch_avail_map[key] = fg_received_branch_map.get(key, Decimal("0")) - (
                fg_transfer_out_map.get(key, Decimal("0")) + fg_issue_out_map.get(key, Decimal("0"))
            )
        point_farmer_map = {}
        for r in issued_pf_rows:
            pid = r["product_id"]
            point_farmer_map[pid] = r["total"] or Decimal("0")

        row_product_ids = (
            set(received_map.keys())
            | {pid for pid, _ in issue_map.keys()}
            | {pid for pid, _ in branch_avail_map.keys()}
            | set(point_farmer_map.keys())
        )
        missing_meta = row_product_ids - set(product_meta.keys())
        if missing_meta:
            for p in Product.objects.filter(id__in=missing_meta).values("id", "name", "unit"):
                product_meta[p["id"]] = {"name": p["name"], "unit": p["unit"]}
        report_rows = []
        for idx, pid in enumerate(sorted(row_product_ids, key=lambda x: product_meta.get(x, {}).get("name", "")), start=1):
            meta = product_meta.get(pid, {"name": f"Product #{pid}", "unit": ""})
            branch_values = []
            total_in_branches = Decimal("0")
            for b in branch_cols:
                q = branch_avail_map.get((pid, b["id"]), Decimal("0"))
                branch_values.append({"branch_id": b["id"], "qty": q})
                total_in_branches += q
            received_total = received_map.get(pid, Decimal("0"))
            point_farmer_issued = point_farmer_map.get(pid, Decimal("0"))
            report_rows.append(
                {
                    "index": idx,
                    "product_id": pid,
                    "product_name": meta["name"],
                    "unit": meta["unit"] or "",
                    "received_total": received_total,
                    "total_in_branches": total_in_branches,
                    "point_farmer_issued": point_farmer_issued,
                    "balance": received_total - point_farmer_issued,
                    "branch_values": branch_values,
                }
            )

        ctx["fg_report_branches"] = branch_cols
        ctx["fg_report_rows"] = report_rows
        ctx["fg_products"] = Product.objects.order_by("name")
        ctx["fg_filters"] = {
            "from_date": from_date,
            "to_date": to_date,
            "product": fg_product,
        }
        if self.request.user.is_superuser:
            from suppliers.farmer_goods_pool_sync import find_farmer_goods_pool_sync

            ctx["fg_pool_sync_count"] = len(find_farmer_goods_pool_sync())
        else:
            ctx["fg_pool_sync_count"] = 0
        return ctx


class ProductCreateView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    template_name = "stock_management/product_form.html"
    form_class = ProductForm
    success_url = reverse_lazy("stock-overview")
    permission_required = "stock_management.add_product"

    def form_valid(self, form):
        product = form.save(commit=False)
        product.status = Product.Status.ACTIVE
        product.save()
        messages.success(self.request, "Product created successfully.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["products"] = Product.objects.order_by("name")
        ctx["categories"] = ProductCategory.objects.order_by("name")
        ctx["category_form"] = ProductCategoryForm()
        ctx["product_status_choices"] = Product.Status.choices
        ctx["is_edit"] = False
        return ctx


class ProductUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    template_name = "stock_management/product_form.html"
    form_class = ProductForm
    queryset = Product.objects.order_by("name")
    permission_required = "stock_management.change_product"

    def get_success_url(self):
        return reverse("stock-product-add")

    def form_valid(self, form):
        messages.success(self.request, "Product updated successfully.")
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["products"] = Product.objects.order_by("name")
        ctx["categories"] = ProductCategory.objects.order_by("name")
        ctx["category_form"] = ProductCategoryForm()
        ctx["product_status_choices"] = Product.Status.choices
        ctx["is_edit"] = True
        return ctx


class ProductCategoryCreateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "stock_management.add_productcategory"

    def post(self, request, *args, **kwargs):
        form = ProductCategoryForm(request.POST)
        if form.is_valid():
            form.save()
            messages.success(request, "Category added successfully.")
        else:
            for error in form.errors.get("name", []):
                messages.error(request, error)
        return redirect(reverse_lazy("stock-product-add"))


class ProductCategoryUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "stock_management.change_productcategory"

    def post(self, request, *args, **kwargs):
        category = get_object_or_404(ProductCategory, pk=kwargs["pk"])
        form = ProductCategoryForm(request.POST, instance=category)
        if form.is_valid():
            old_code = category.code
            updated = form.save()
            Product.objects.filter(category=old_code).update(category=updated.code)
            messages.success(request, "Category updated successfully.")
        else:
            for error in form.errors.get("name", []):
                messages.error(request, error)
        return redirect(reverse_lazy("stock-product-add"))


class ProductCategoryDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "stock_management.delete_productcategory"

    def post(self, request, *args, **kwargs):
        category = get_object_or_404(ProductCategory, pk=kwargs["pk"])
        if Product.objects.filter(category=category.code).exists():
            messages.error(request, "Category is used by products and cannot be deleted.")
        else:
            category.delete()
            messages.success(request, "Category deleted successfully.")
        return redirect(reverse_lazy("stock-product-add"))


class ProductStatusUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "stock_management.change_product"

    def post(self, request, *args, **kwargs):
        product = get_object_or_404(Product, pk=kwargs["pk"])
        status = request.POST.get("status")
        valid_statuses = {choice[0] for choice in Product.Status.choices}

        if status not in valid_statuses:
            messages.error(request, "Invalid status selected.")
            return redirect(request.META.get("HTTP_REFERER", reverse_lazy("stock-product-add")))

        if product.status != status:
            product.status = status
            product.save(update_fields=["status", "updated_at"])
            messages.success(request, f"Status updated for {product.name}.")

        return redirect(request.META.get("HTTP_REFERER", reverse_lazy("stock-product-add")))


class ProductDeleteView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "stock_management.delete_product"

    def post(self, request, *args, **kwargs):
        product = get_object_or_404(Product, pk=kwargs["pk"])
        try:
            product_name = product.name
            product.delete()
            messages.success(request, f"{product_name} deleted successfully.")
        except Exception:
            messages.error(request, "Product cannot be deleted because it is linked to records.")
        return redirect(request.META.get("HTTP_REFERER", reverse_lazy("stock-product-add")))


class StockTransferNoteCreateView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Multi-product transfer from one branch to another."""

    template_name = "stock_management/transfer_form.html"

    def test_func(self):
        u = self.request.user
        return u.is_superuser or u.has_perm("stock_management.add_stocktransfernote") or u.has_perm(
            "stock_management.add_stocktransfer"
        )

    def get(self, request):
        form = StockTransferNoteForm(user=request.user)
        FormSet = get_stock_transfer_line_formset()
        formset = FormSet()
        return render(request, self.template_name, {"form": form, "formset": formset, "is_edit": False})

    def post(self, request):
        form = StockTransferNoteForm(request.POST, user=request.user)
        FormSet = get_stock_transfer_line_formset()
        formset = FormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            line_items = _extract_transfer_line_items(formset)
            if not line_items:
                form.add_error(None, "Add at least one product line with a quantity.")
                return render(request, self.template_name, {"form": form, "formset": formset, "is_edit": False})
            try:
                note = create_stock_transfer_note(
                    from_branch=form.cleaned_data["from_branch"],
                    to_branch=form.cleaned_data["to_branch"],
                    line_items=line_items,
                    date=form.cleaned_data["date"],
                    remarks=form.cleaned_data.get("remarks") or "",
                    created_by=request.user,
                    actor=request.user,
                )
            except ValidationError as exc:
                form.add_error(None, str(exc))
                return render(request, self.template_name, {"form": form, "formset": formset, "is_edit": False})
            messages.success(request, f"Transfer {note.transfer_number} saved.")
            return redirect(f"{reverse('stock-transfer-note-print', kwargs={'pk': note.pk})}?standalone=1")

        return render(request, self.template_name, {"form": form, "formset": formset, "is_edit": False})


def _extract_transfer_line_items(formset):
    line_items = []
    for f in formset.forms:
        cd = getattr(f, "cleaned_data", None) or {}
        if cd.get("product") and cd.get("quantity"):
            line_items.append((cd["product"], cd["quantity"]))
    return line_items


class StockTransferNoteListView(LoginRequiredMixin, UserPassesTestMixin, ListView):
    template_name = "stock_management/transfer_note_list.html"
    model = StockTransferNote
    context_object_name = "notes"

    def test_func(self):
        u = self.request.user
        if u.is_superuser:
            return True
        return u.has_perm("stock_management.view_stocktransfernote") or u.has_perm(
            "stock_management.add_stocktransfernote"
        ) or u.has_perm("stock_management.add_stocktransfer")

    def get_queryset(self):
        qs = (
            StockTransferNote.objects.select_related("from_branch", "to_branch", "created_by")
            .prefetch_related("lines__product")
            .order_by("-date", "-id")
        )
        branch_ids = get_user_branch_ids(self.request.user)
        if branch_ids is not None:
            qs = qs.filter(Q(from_branch_id__in=branch_ids) | Q(to_branch_id__in=branch_ids))
        return qs


class StockTransferNoteUpdateView(LoginRequiredMixin, UserPassesTestMixin, View):
    template_name = "stock_management/transfer_form.html"

    def test_func(self):
        u = self.request.user
        if u.is_superuser:
            return True
        return u.has_perm("stock_management.change_stocktransfernote") or u.has_perm(
            "stock_management.add_stocktransfernote"
        ) or u.has_perm("stock_management.add_stocktransfer")

    def _get_note(self, request, pk):
        qs = StockTransferNote.objects.select_related("from_branch", "to_branch").prefetch_related("lines__product")
        branch_ids = get_user_branch_ids(request.user)
        if branch_ids is not None:
            qs = qs.filter(Q(from_branch_id__in=branch_ids) | Q(to_branch_id__in=branch_ids))
        return get_object_or_404(qs, pk=pk)

    def get(self, request, pk):
        note = self._get_note(request, pk)
        form = StockTransferNoteForm(instance=note, user=request.user)
        FormSet = get_stock_transfer_line_formset()
        initial = [{"product": l.product, "quantity": l.quantity} for l in note.lines.all()]
        formset = FormSet(initial=initial if initial else None)
        return render(
            request,
            self.template_name,
            {"form": form, "formset": formset, "is_edit": True, "note": note},
        )

    def post(self, request, pk):
        note = self._get_note(request, pk)
        form = StockTransferNoteForm(request.POST, instance=note, user=request.user)
        FormSet = get_stock_transfer_line_formset()
        formset = FormSet(request.POST)
        if form.is_valid() and formset.is_valid():
            line_items = _extract_transfer_line_items(formset)
            if not line_items:
                form.add_error(None, "Add at least one product line with a quantity.")
                return render(
                    request,
                    self.template_name,
                    {"form": form, "formset": formset, "is_edit": True, "note": note},
                )
            try:
                updated = update_stock_transfer_note(
                    note=note,
                    from_branch=form.cleaned_data["from_branch"],
                    to_branch=form.cleaned_data["to_branch"],
                    line_items=line_items,
                    date=form.cleaned_data["date"],
                    remarks=form.cleaned_data.get("remarks") or "",
                    actor=request.user,
                )
            except ValidationError as exc:
                form.add_error(None, str(exc))
                return render(
                    request,
                    self.template_name,
                    {"form": form, "formset": formset, "is_edit": True, "note": note},
                )
            messages.success(request, f"Transfer {updated.transfer_number} updated.")
            return redirect("stock-transfer-note-list")

        return render(
            request,
            self.template_name,
            {"form": form, "formset": formset, "is_edit": True, "note": note},
        )


class StockTransferNotePrintView(LoginRequiredMixin, UserPassesTestMixin, DetailView):
    model = StockTransferNote
    template_name = "stock_management/transfer_note_print.html"
    context_object_name = "note"

    def test_func(self):
        u = self.request.user
        if u.is_superuser:
            return True
        return u.has_perm("stock_management.view_stocktransfernote") or u.has_perm(
            "stock_management.add_stocktransfernote"
        ) or u.has_perm("stock_management.add_stocktransfer")

    def get_queryset(self):
        qs = (
            StockTransferNote.objects.all()
            .select_related("from_branch", "to_branch", "created_by")
            .prefetch_related("lines__product")
        )
        branch_ids = get_user_branch_ids(self.request.user)
        if branch_ids is not None:
            qs = qs.filter(Q(from_branch_id__in=branch_ids) | Q(to_branch_id__in=branch_ids))
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        lines = transfer_note_priced_lines(self.object)
        ctx["priced_lines"] = lines
        ctx["lines_total_amount"] = sum(
            (row["amount"] for row in lines), Decimal("0")
        ).quantize(Decimal("0.01"))
        return ctx


class StockTransferNoteDeleteView(LoginRequiredMixin, View):
    success_url = reverse_lazy("stock-transfer-note-list")

    def post(self, request, pk):
        if not request.user.is_superuser:
            messages.error(request, "Only superusers can delete transfer notes.")
            return redirect(self.success_url)
        note = get_object_or_404(StockTransferNote.objects.prefetch_related("lines__product"), pk=pk)
        try:
            ref = delete_stock_transfer_note(note=note, actor=request.user)
        except ValidationError as exc:
            messages.error(request, str(exc))
            return redirect(self.success_url)
        messages.success(request, f"Transfer {ref} deleted.")
        return redirect(self.success_url)


class StockIssueView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    template_name = "stock_management/issue_form.html"
    form_class = StockIssueForm
    success_url = reverse_lazy("stock-overview")
    permission_required = "stock_management.add_stockissue"

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["branch"].queryset = get_allowed_branches_qs(self.request.user).order_by("name")
        return form

    def form_valid(self, form):
        data = form.cleaned_data
        try:
            issue_from_branch_stock(
                product=data["product"],
                quantity=data["quantity"],
                branch=data["branch"],
                issued_to_type=data["issued_to_type"],
                issued_to_id=data["issued_to_id"],
                actor=self.request.user,
            )
        except ValidationError as exc:
            form.add_error(None, str(exc))
            return self.form_invalid(form)
        messages.success(self.request, "Stock issued successfully.")
        return redirect(self.success_url)


class ProductCreateAPI(generics.CreateAPIView):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [IsAuthenticated, DjangoModelPermissions]


class StockTransferAPI(generics.CreateAPIView):
    queryset = StockTransfer.objects.all()
    serializer_class = StockTransferSerializer
    permission_classes = [IsAuthenticated, DjangoModelPermissions]


class StockIssueAPI(generics.CreateAPIView):
    queryset = StockIssue.objects.all()
    serializer_class = StockIssueSerializer
    permission_classes = [IsAuthenticated, DjangoModelPermissions]


class MainStockListAPI(generics.ListAPIView):
    queryset = MainStock.objects.select_related("product").all()
    serializer_class = MainStockSerializer
    permission_classes = [IsAuthenticated, DjangoModelPermissions]


class BranchStockListAPI(generics.ListAPIView):
    serializer_class = BranchStockSerializer
    permission_classes = [IsAuthenticated, DjangoModelPermissions]

    def get_queryset(self):
        qs = BranchStock.objects.select_related("branch", "product")
        branch_ids = get_user_branch_ids(self.request.user)
        if branch_ids is not None:
            qs = qs.filter(branch_id__in=branch_ids)
        return qs


class StockHistoryView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "stock_management/stock_history.html"
    permission_required = "stock_management.view_stocklog"

    def _base_logs_queryset(self):
        qs = StockLog.objects.select_related("product")
        branch_ids = get_user_branch_ids(self.request.user)
        if branch_ids is None:
            return qs
        branch_names = set(Branch.objects.filter(id__in=branch_ids).values_list("name", flat=True))
        markers = [f"Branch: {name}" for name in branch_names]
        return qs.filter(Q(source__in=markers) | Q(destination__in=markers))

    def _branch_marker(self):
        branch_id = (self.request.GET.get("branch") or "").strip()
        if not branch_id:
            return None
        branch = Branch.objects.filter(id=branch_id).first()
        return f"Branch: {branch.name}" if branch else None

    def _signed_delta(self, log, branch_marker=None):
        """
        Net stock effect for running balance under current filters.
        - No branch filter: GRN +, ISSUE -, TRANSFER 0 (internal move).
        - Branch filter: + when stock enters that branch, - when it leaves.
        """
        qty = Decimal(str(log.quantity or 0))
        if branch_marker:
            if log.destination == branch_marker and log.source != branch_marker:
                return qty
            if log.source == branch_marker and log.destination != branch_marker:
                return -qty
            return Decimal("0.00")
        if log.action_type == StockLog.ActionType.GRN:
            return qty
        if log.action_type == StockLog.ActionType.ISSUE:
            return -qty
        if log.action_type == StockLog.ActionType.TRANSFER:
            return Decimal("0.00")
        return Decimal("0.00")

    def _parse_filter_date(self, raw):
        value = (raw or "").strip()
        if not value:
            return None
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return None

    def _day_start(self, day):
        return timezone.make_aware(datetime.combine(day, time.min))

    def _day_end_exclusive(self, day):
        return timezone.make_aware(datetime.combine(day + timedelta(days=1), time.min))

    def _apply_filters(self, qs, *, include_action=True, include_dates=True):
        product_id = self.request.GET.get("product")
        action_type = self.request.GET.get("action_type")
        branch_id = self.request.GET.get("branch")
        from_day = self._parse_filter_date(self.request.GET.get("from_date"))
        to_day = self._parse_filter_date(self.request.GET.get("to_date"))

        if product_id:
            qs = qs.filter(product_id=product_id)
        if include_action and action_type:
            qs = qs.filter(action_type=action_type)
        if branch_id:
            branch = Branch.objects.filter(id=branch_id).first()
            if branch:
                marker = f"Branch: {branch.name}"
                qs = qs.filter(Q(source=marker) | Q(destination=marker))
        if include_dates:
            # Avoid date__date (broken with MySQL + USE_TZ); use aware day bounds.
            if from_day:
                qs = qs.filter(date__gte=self._day_start(from_day))
            if to_day:
                qs = qs.filter(date__lt=self._day_end_exclusive(to_day))
        return qs.order_by("date", "pk")

    def _opening_balances(self, base_qs):
        """Per-product balance from movements before from_date (same product/branch scope)."""
        from_day = self._parse_filter_date(self.request.GET.get("from_date"))
        if not from_day:
            return {}
        prior = self._apply_filters(base_qs, include_action=False, include_dates=False).filter(
            date__lt=self._day_start(from_day)
        )
        branch_marker = self._branch_marker()
        opening = {}
        from stock_management.history_rows import (
            applied_fg_issue_missing_log_rows,
            live_fg_issue_ids_for_logs,
            stocklog_is_live,
        )

        prior_logs = list(prior.order_by("date", "pk"))
        live_ids = live_fg_issue_ids_for_logs(prior_logs)
        for log in prior_logs:
            if not stocklog_is_live(log, live_ids):
                continue
            pid = log.product_id
            opening[pid] = opening.get(pid, Decimal("0.00")) + self._signed_delta(
                log, branch_marker
            )
        product_id, branch_id, from_day, _to_day = self._history_filter_ids()
        if from_day:
            branch_ids = get_user_branch_ids(self.request.user)
            for row in applied_fg_issue_missing_log_rows(
                product_id=product_id,
                branch_id=branch_id,
                before_day=from_day,
                branch_ids=branch_ids,
            ):
                pid = row.product.id if hasattr(row.product, "id") else row.product
                opening[pid] = opening.get(pid, Decimal("0.00")) + self._signed_delta(
                    row, branch_marker
                )
        return opening

    def _with_running_balances(self, logs, opening=None):
        """
        Attach balance_qty on each log: per-product running total after that movement.
        Starts from opening (for date-from filter). Chronology oldest→newest.
        """
        rows = list(logs)
        branch_marker = self._branch_marker()
        running = {pid: Decimal(str(bal)) for pid, bal in (opening or {}).items()}
        balances = {}
        for log in sorted(rows, key=lambda r: (r.date, r.pk)):
            pid = log.product_id
            running[pid] = running.get(pid, Decimal("0.00")) + self._signed_delta(
                log, branch_marker
            )
            balances[log.pk] = running[pid]
        for log in rows:
            log.balance_qty = balances.get(log.pk, Decimal("0.00"))
        return rows

    def _history_filter_ids(self):
        product_raw = (self.request.GET.get("product") or "").strip()
        branch_raw = (self.request.GET.get("branch") or "").strip()
        product_id = int(product_raw) if product_raw.isdigit() else None
        branch_id = int(branch_raw) if branch_raw.isdigit() else None
        from_day = self._parse_filter_date(self.request.GET.get("from_date"))
        to_day = self._parse_filter_date(self.request.GET.get("to_date"))
        return product_id, branch_id, from_day, to_day

    def _history_row_day(self, row):
        dt = getattr(row, "date", None)
        if dt is None:
            return None
        if timezone.is_aware(dt):
            dt = timezone.localtime(dt)
        return dt.date()

    def _history_row_in_period(self, row, from_day, to_day):
        day = self._history_row_day(row)
        if day is None:
            return True
        if from_day and day < from_day:
            return False
        if to_day and day > to_day:
            return False
        return True

    def _extra_status_rows(self, *, include_dates=True):
        from stock_management.history_rows import draft_grn_history_rows, extra_fg_issue_history_rows

        product_id, branch_id, from_day, to_day = self._history_filter_ids()
        if not include_dates:
            from_day = None
            to_day = None
        branch_ids = get_user_branch_ids(self.request.user)
        draft_rows = draft_grn_history_rows(
            product_id=product_id,
            branch_id=branch_id,
            from_day=from_day,
            to_day=to_day,
            branch_ids=branch_ids,
        )
        fg_rows = extra_fg_issue_history_rows(
            product_id=product_id,
            branch_id=branch_id,
            from_day=from_day,
            to_day=to_day,
            branch_ids=branch_ids,
        )
        return draft_rows + fg_rows

    def _filtered_logs_with_balances(self):
        """
        Balance uses posted movements (StockLog + accepted receipts missing a log).
        Period filter uses the issue receipt date, not the accept/log write time.
        Draft GRNs and rejected/pending FG issues are listed with Status but do not move balance.
        """
        from stock_management.history_rows import (
            attach_running_balances,
            stocklogs_to_history_rows,
        )

        base = self._base_logs_queryset()
        scoped_qs = self._apply_filters(base, include_action=False, include_dates=False)
        branch_marker = self._branch_marker()
        _product_id, _branch_id, from_day, to_day = self._history_filter_ids()

        posted_rows = stocklogs_to_history_rows(
            scoped_qs.select_related("product").order_by("date", "pk")
        )
        extra_rows = self._extra_status_rows(include_dates=False)
        all_rows = attach_running_balances(
            posted_rows + extra_rows,
            signed_delta_fn=lambda fake: self._signed_delta(fake, branch_marker),
            opening={},
        )
        if from_day or to_day:
            all_rows = [
                row for row in all_rows if self._history_row_in_period(row, from_day, to_day)
            ]
            extra_rows = [
                row for row in extra_rows if self._history_row_in_period(row, from_day, to_day)
            ]

        action_type = (self.request.GET.get("action_type") or "").strip()
        if action_type:
            all_rows = [row for row in all_rows if row.action_type == action_type]
        return scoped_qs, all_rows, extra_rows

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        scoped_qs, all_rows, extra_rows = self._filtered_logs_with_balances()
        status_filter = (self.request.GET.get("status") or "").strip().lower()
        rows = (
            [row for row in all_rows if row.status_key == status_filter]
            if status_filter
            else all_rows
        )

        ctx["history_summary"] = {
            "count": len(all_rows),
            "grn_count": sum(1 for r in all_rows if r.action_type == StockLog.ActionType.GRN),
            "transfer_count": sum(
                1 for r in all_rows if r.action_type == StockLog.ActionType.TRANSFER
            ),
            "issue_count": sum(1 for r in all_rows if r.action_type == StockLog.ActionType.ISSUE),
            "posted_count": sum(
                1 for r in all_rows if r.status_key in ("posted", "accepted", "not_required")
            ),
            "draft_count": sum(1 for r in all_rows if r.status_key == "draft"),
            "rejected_count": sum(1 for r in all_rows if r.status_key == "rejected"),
            "pending_count": sum(1 for r in all_rows if r.status_key == "pending"),
        }
        ctx["logs"] = rows
        ctx["products"] = Product.objects.order_by("name")
        ctx["branches"] = get_allowed_branches_qs(self.request.user).order_by("name")
        ctx["action_types"] = StockLog.ActionType.choices
        ctx["filters"] = {
            "product": self.request.GET.get("product", ""),
            "action_type": self.request.GET.get("action_type", ""),
            "status": self.request.GET.get("status", ""),
            "branch": self.request.GET.get("branch", ""),
            "from_date": self.request.GET.get("from_date", ""),
            "to_date": self.request.GET.get("to_date", ""),
        }
        # Preserve non-action/status filters when building chip links.
        q = []
        for key in ("product", "branch", "from_date", "to_date"):
            val = ctx["filters"].get(key) or ""
            if val:
                q.append(f"{key}={val}")
        ctx["history_filter_query"] = "&".join(q)
        status_q = list(q)
        if ctx["filters"].get("action_type"):
            status_q.append(f"action_type={ctx['filters']['action_type']}")
        ctx["history_status_filter_query"] = "&".join(status_q)
        return ctx


class StockHistoryExportView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    permission_required = "stock_management.view_stocklog"

    def get(self, request, *args, **kwargs):
        history_view = StockHistoryView()
        history_view.request = request
        _, rows, _ = history_view._filtered_logs_with_balances()
        status_filter = (request.GET.get("status") or "").strip().lower()
        if status_filter:
            rows = [row for row in rows if row.status_key == status_filter]

        response = HttpResponse(content_type="text/csv")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        response["Content-Disposition"] = f'attachment; filename="stock_history_{stamp}.csv"'
        writer = csv.writer(response)
        writer.writerow(
            [
                "Date",
                "Action",
                "Status",
                "Product",
                "Quantity",
                "Balance",
                "Source",
                "Destination",
            ]
        )
        for row in rows:
            writer.writerow(
                [
                    row.date.strftime("%Y-%m-%d %H:%M:%S"),
                    row.get_action_type_display(),
                    getattr(row, "status_label", "Posted"),
                    row.product.name,
                    row.quantity,
                    row.balance_qty if getattr(row, "affects_balance", True) else "",
                    row.source,
                    row.destination,
                ]
            )
        return response


def _resolve_modal_branch(request):
    """Optional branch from ?branch= for stock overview modals."""
    raw = (request.GET.get("branch") or "").strip()
    if not raw:
        return None
    try:
        branch_id = int(raw)
    except (TypeError, ValueError):
        return None
    allowed = get_allowed_branches_qs(request.user).filter(pk=branch_id).first()
    return allowed


def _branch_product_movement_summary(branch, product):
    """Received / transfer out / point-farmer / available for one product at one branch."""
    grn_total = (
        GRNItem.objects.filter(
            product=product,
            grn__status=GRN.Status.CONFIRMED,
            grn__branch=branch,
        ).aggregate(total=Sum("quantity"))["total"]
        or Decimal("0")
    )
    in_transfer = (
        StockTransferLine.objects.filter(product=product, note__to_branch=branch).aggregate(
            total=Sum("quantity")
        )["total"]
        or Decimal("0")
    )
    in_legacy = (
        StockTransfer.objects.filter(product=product, to_branch=branch).aggregate(
            total=Sum("quantity")
        )["total"]
        or Decimal("0")
    )
    out_transfer = (
        StockTransferLine.objects.filter(product=product, note__from_branch=branch).aggregate(
            total=Sum("quantity")
        )["total"]
        or Decimal("0")
    )
    stock_issue = (
        StockIssue.objects.filter(product=product, branch=branch).aggregate(total=Sum("quantity"))[
            "total"
        ]
        or Decimal("0")
    )
    fg_issue = (
        FarmerGoodsIssue.objects.filter(
            product=product,
            from_branch=branch,
            issue_to_type__in=[
                FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
                FarmerGoodsIssue.IssueToType.FARMER,
            ],
        )
        .exclude(driver_status=FarmerGoodsIssue.DriverStatus.REJECTED)
        .aggregate(total=Sum("quantity"))["total"]
        or Decimal("0")
    )
    received = grn_total + in_transfer + in_legacy
    point_farmer = stock_issue + fg_issue
    available = received - (out_transfer + point_farmer)
    return {
        "received": received,
        "transfer_qty": out_transfer,
        "point_farmer": point_farmer,
        "available": available,
    }


def _farmer_goods_product_movement_summary(product, branches):
    """
    Farmer-goods pool movement for one product (matches Farmer goods stock report).
    Received = farmer-goods GRNs + stock corrections; In branches = sum of branch Available;
    Point/Farmer = issues to CP/farmer; Balance = received − point/farmer (in-branch stock
    is still on hand, so it is not subtracted again).
    """
    from suppliers.farmer_goods_pool_sync import POOL_RECEIPT_GRN_TYPES, farmer_goods_pool_target

    received = (
        GRNItem.objects.filter(
            product=product,
            grn__status=GRN.Status.CONFIRMED,
            grn__grn_type__in=POOL_RECEIPT_GRN_TYPES,
        ).aggregate(total=Sum("quantity"))["total"]
        or Decimal("0")
    )
    draft_received = (
        GRNItem.objects.filter(
            product=product,
            grn__status=GRN.Status.DRAFT,
            grn__grn_type=GRN.GRNType.FARMER_GOODS,
        ).aggregate(total=Sum("quantity"))["total"]
        or Decimal("0")
    )
    issued_to_branches = (
        FarmerGoodsIssue.objects.filter(
            product=product,
            issue_to_type=FarmerGoodsIssue.IssueToType.BRANCH,
        ).aggregate(total=Sum("quantity"))["total"]
        or Decimal("0")
    )
    pf_base = FarmerGoodsIssue.objects.filter(
        product=product,
        issue_to_type__in=[
            FarmerGoodsIssue.IssueToType.COLLECTION_POINT,
            FarmerGoodsIssue.IssueToType.FARMER,
        ],
    )
    # Point/Farmer movement includes pending (soft-reserved) but not rejected.
    point_farmer = (
        pf_base.exclude(driver_status=FarmerGoodsIssue.DriverStatus.REJECTED).aggregate(
            total=Sum("quantity")
        )["total"]
        or Decimal("0")
    )
    pending_point_farmer = (
        pf_base.filter(driver_status=FarmerGoodsIssue.DriverStatus.PENDING).aggregate(
            total=Sum("quantity")
        )["total"]
        or Decimal("0")
    )
    applied_point_farmer = (
        pf_base.exclude(
            driver_status__in=[
                FarmerGoodsIssue.DriverStatus.PENDING,
                FarmerGoodsIssue.DriverStatus.REJECTED,
            ]
        ).aggregate(total=Sum("quantity"))["total"]
        or Decimal("0")
    )
    # Pool ledger = confirmed FG GRNs − all applied issues (pending/rejected excluded).
    applied_all_issues = (
        FarmerGoodsIssue.objects.filter(product=product)
        .exclude(
            driver_status__in=[
                FarmerGoodsIssue.DriverStatus.PENDING,
                FarmerGoodsIssue.DriverStatus.REJECTED,
            ]
        )
        .aggregate(total=Sum("quantity"))["total"]
        or Decimal("0")
    )
    pending_all_issues = (
        FarmerGoodsIssue.objects.filter(
            product=product,
            driver_status=FarmerGoodsIssue.DriverStatus.PENDING,
        ).aggregate(total=Sum("quantity"))["total"]
        or Decimal("0")
    )
    in_branches = Decimal("0")
    for b in branches:
        in_branches += _branch_product_movement_summary(b, product)["available"]
    balance = received - point_farmer
    pool = FarmerGoodsStock.objects.filter(product=product).first()
    pool_qty = pool.quantity if pool else Decimal("0")
    pool_target, _received, _issued = farmer_goods_pool_target(product.id)
    return {
        "received": received,
        "draft_received": draft_received,
        "issued_to_branches": issued_to_branches,
        "in_branches": in_branches,
        "point_farmer": point_farmer,
        "pending_point_farmer": pending_point_farmer,
        "applied_point_farmer": applied_point_farmer,
        "applied_all_issues": applied_all_issues,
        "pending_all_issues": pending_all_issues,
        "balance": balance,
        "pool_qty": pool_qty,
        "pool_target": pool_target,
    }


class ProductSummaryModalAPI(LoginRequiredMixin, PermissionRequiredMixin, View):
    """JSON product stock summary for Stock Overview modal."""

    permission_required = "stock_management.view_mainstock"

    def get(self, request, product_id):
        product = get_object_or_404(Product, pk=product_id)
        branch = _resolve_modal_branch(request)
        main = MainStock.objects.filter(product=product).first()
        fg = FarmerGoodsStock.objects.filter(product=product).first()
        allowed_branches = list(get_allowed_branches_qs(request.user).order_by("name"))
        branch_rows = []
        for b in allowed_branches:
            # Same Available formula as Branch Stock tab (not raw BranchStock ledger alone).
            move = _branch_product_movement_summary(b, product)
            branch_rows.append(
                {
                    "id": b.id,
                    "name": b.name,
                    "quantity": f"{move['available']:.2f}",
                    "is_focus": bool(branch and branch.id == b.id),
                }
            )
        focus = None
        if branch:
            move = _branch_product_movement_summary(branch, product)
            focus = {
                "branch_id": branch.id,
                "branch_name": branch.name,
                "received": f"{move['received']:.2f}",
                "transfer_qty": f"{move['transfer_qty']:.2f}",
                "point_farmer": f"{move['point_farmer']:.2f}",
                "available": f"{move['available']:.2f}",
            }
        fg_move = _farmer_goods_product_movement_summary(product, allowed_branches)
        return JsonResponse(
            {
                "product": {
                    "id": product.id,
                    "name": product.name,
                    "unit": product.unit or "",
                    "category": product.get_category_display(),
                },
                "main_qty": f"{(main.quantity if main else Decimal('0')):.2f}",
                "farmer_goods_qty": f"{(fg.quantity if fg else Decimal('0')):.2f}",
                "fg_movement": {
                    "received": f"{fg_move['received']:.2f}",
                    "draft_received": f"{fg_move['draft_received']:.2f}",
                    "issued_to_branches": f"{fg_move['issued_to_branches']:.2f}",
                    "in_branches": f"{fg_move['in_branches']:.2f}",
                    "point_farmer": f"{fg_move['point_farmer']:.2f}",
                    "pending_point_farmer": f"{fg_move['pending_point_farmer']:.2f}",
                    "applied_point_farmer": f"{fg_move['applied_point_farmer']:.2f}",
                    "applied_all_issues": f"{fg_move['applied_all_issues']:.2f}",
                    "pending_all_issues": f"{fg_move['pending_all_issues']:.2f}",
                    "pool_target": f"{fg_move['pool_target']:.2f}",
                    "balance": f"{fg_move['balance']:.2f}",
                },
                "branches": branch_rows,
                "focus": focus,
                "history_url": (
                    f"{reverse('stock-history')}?product={product.id}"
                    + (f"&branch={branch.id}" if branch else "")
                ),
                "grn_url": (
                    f"{reverse('grn-list')}?product={product.id}"
                    + (f"&branch={branch.id}" if branch else "")
                ),
            }
        )


class ProductStockHistoryModalAPI(LoginRequiredMixin, PermissionRequiredMixin, View):
    """JSON history rows for Stock Overview product modal."""

    permission_required = "stock_management.view_stocklog"

    def get(self, request, product_id):
        from stock_management.history_rows import (
            attach_running_balances,
            extra_fg_issue_history_rows,
            stocklogs_to_history_rows,
        )

        product = get_object_or_404(Product, pk=product_id)
        history_view = StockHistoryView()
        history_view.request = request
        branch = _resolve_modal_branch(request)
        qs = history_view._base_logs_queryset().filter(product_id=product.id).select_related(
            "product"
        )
        branch_marker = None
        if branch:
            branch_marker = f"Branch: {branch.name}"
            qs = qs.filter(Q(source=branch_marker) | Q(destination=branch_marker))

        posted_rows = stocklogs_to_history_rows(qs.order_by("date", "pk"))

        fg_kwargs = {"product_id": product.id}
        if branch:
            fg_kwargs["branch_id"] = branch.id
        else:
            branch_ids = get_user_branch_ids(request.user)
            if branch_ids is not None:
                fg_kwargs["branch_ids"] = branch_ids
        fg_rows = extra_fg_issue_history_rows(**fg_kwargs)

        all_rows = attach_running_balances(
            posted_rows + fg_rows,
            signed_delta_fn=lambda fake: history_view._signed_delta(fake, branch_marker),
        )
        pending_count = sum(1 for r in all_rows if r.status_key == "pending")
        rejected_count = sum(1 for r in all_rows if r.status_key == "rejected")
        if len(all_rows) > 300:
            all_rows = all_rows[-300:]

        rows = [
            {
                # Match Stock History template: show Asia/Colombo local time.
                "date": timezone.localtime(row.date).strftime("%Y-%m-%d %H:%M"),
                "action": row.get_action_type_display(),
                "status": row.status_label,
                "status_css": row.status_css,
                "quantity": str(row.quantity),
                "balance": "—" if not row.affects_balance else f"{row.balance_qty:.2f}",
                "source": row.source or "—",
                "destination": row.destination or "—",
                "affects_balance": row.affects_balance,
            }
            for row in all_rows
        ]
        full_url = f"{reverse('stock-history')}?product={product.id}"
        if branch:
            full_url += f"&branch={branch.id}"
        subtitle = (
            f"Movements for {branch.name}" if branch else "Receiving, transfers, and issues"
        )
        if pending_count or rejected_count:
            bits = []
            if pending_count:
                bits.append(f"Pending {pending_count}")
            if rejected_count:
                bits.append(f"Rejected {rejected_count}")
            subtitle += " · " + " · ".join(bits) + " (listed, do not change balance)"
        return JsonResponse(
            {
                "product": {
                    "id": product.id,
                    "name": product.name,
                    "unit": product.unit or "",
                },
                "branch": {"id": branch.id, "name": branch.name} if branch else None,
                "subtitle": subtitle,
                "full_url": full_url,
                "pending_count": pending_count,
                "rejected_count": rejected_count,
                "rows": rows,
            }
        )


class ProductGrnModalAPI(LoginRequiredMixin, PermissionRequiredMixin, View):
    """JSON GRN price rows for Stock Overview product modal."""

    permission_required = "suppliers.view_grn"

    def get(self, request, product_id):
        product = get_object_or_404(Product, pk=product_id)
        branch = _resolve_modal_branch(request)
        qs = (
            GRNItem.objects.filter(
                product_id=product.id,
                grn__status__in=[GRN.Status.CONFIRMED, GRN.Status.DRAFT],
            )
            .select_related("grn", "grn__supplier", "grn__branch")
            .order_by("grn__date", "grn_id", "id")
        )
        branch_ids = get_user_branch_ids(request.user)
        if branch_ids is not None:
            qs = qs.filter(Q(grn__branch_id__in=branch_ids) | Q(grn__branch_id__isnull=True))
        if branch:
            qs = qs.filter(grn__branch_id=branch.id)

        grn_type = (request.GET.get("grn_type") or "").strip()
        valid_types = {choice[0] for choice in GRN.GRNType.choices}
        if grn_type in valid_types:
            qs = qs.filter(grn__grn_type=grn_type)

        rows = []
        draft_count = 0
        confirmed_count = 0
        for item in qs[:300]:
            grn = item.grn
            status_key = grn.status or GRN.Status.DRAFT
            if status_key == GRN.Status.DRAFT:
                draft_count += 1
                status_css = "draft"
            else:
                confirmed_count += 1
                status_css = "confirmed"
            rows.append(
                {
                    "grn_id": grn.id,
                    "grn_number": grn.grn_number,
                    "supplier": grn.supplier.name if grn.supplier_id else "—",
                    "branch": grn.branch.name if grn.branch_id else "—",
                    "type": grn.get_grn_type_display(),
                    "date": grn.date.isoformat() if grn.date else "",
                    "quantity": str(item.quantity or Decimal("0")),
                    "rate": format_money(item.unit_price or Decimal("0")),
                    "issue_price": format_money(item.issuing_price or Decimal("0")),
                    "line_total": format_money(item.total_price or Decimal("0")),
                    "status": grn.get_status_display(),
                    "status_css": status_css,
                    "print_url": reverse("grn-print", args=[grn.id]),
                }
            )

        full_url = f"{reverse('grn-list')}?product={product.id}"
        if branch:
            full_url += f"&branch={branch.id}"
        if grn_type in valid_types:
            full_url += f"&grn_type={grn_type}"
        subtitle = (
            f"GRNs for {branch.name}"
            if branch
            else "Compare rate and issue price by receiving layer"
        )
        if draft_count:
            subtitle += f" · Draft {draft_count} (not in stock yet)"

        return JsonResponse(
            {
                "product": {
                    "id": product.id,
                    "name": product.name,
                    "unit": product.unit or "",
                },
                "branch": {"id": branch.id, "name": branch.name} if branch else None,
                "subtitle": subtitle,
                "full_url": full_url,
                "draft_count": draft_count,
                "confirmed_count": confirmed_count,
                "rows": rows,
            }
        )


class CorrectNegativeBranchStockView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Superuser tool: preview / apply adjustment GRNs for negative branch available."""

    template_name = "stock_management/correct_negative_stock.html"

    def test_func(self):
        return self.request.user.is_superuser

    def handle_no_permission(self):
        messages.error(self.request, "Only superusers can correct negative branch stock.")
        return redirect("stock-overview")

    def _filter_ids(self, request):
        branch_raw = (request.POST.get("branch") or request.GET.get("branch") or "").strip()
        product_raw = (request.POST.get("product") or request.GET.get("product") or "").strip()
        branch_id = int(branch_raw) if branch_raw.isdigit() else None
        product_id = int(product_raw) if product_raw.isdigit() else None
        return branch_id, product_id

    def _grn_type(self, request):
        raw = (request.POST.get("grn_type") or request.GET.get("grn_type") or "farmer_goods").strip()
        if raw == "corporate":
            return GRN.GRNType.CORPORATE
        return GRN.GRNType.FARMER_GOODS

    def _supplier_id(self, request):
        raw = (request.POST.get("supplier") or request.GET.get("supplier") or "").strip()
        return int(raw) if raw.isdigit() else None

    def _build_context(self, request):
        from stock_management.grn_log_cleanup import (
            find_duplicate_grn_stock_logs,
            find_missing_grn_stock_logs,
        )
        from suppliers.farmer_goods_pool_sync import find_farmer_goods_pool_sync
        from suppliers.models import Supplier
        from suppliers.negative_branch_stock import (
            find_negative_branch_stock,
            resolve_adjustment_supplier,
        )

        branch_id, product_id = self._filter_ids(request)
        grn_type = self._grn_type(request)
        supplier_id = self._supplier_id(request)
        corrections_by_branch, sync_only, flat_rows = find_negative_branch_stock(
            branch_id=branch_id, product_id=product_id
        )
        supplier_error = ""
        supplier = None
        try:
            supplier = resolve_adjustment_supplier(
                supplier_id,
                "corporate" if grn_type == GRN.GRNType.CORPORATE else "farmer_goods",
            )
        except ValidationError as exc:
            supplier_error = str(exc)

        dup_plan = find_duplicate_grn_stock_logs(
            product_id=product_id, branch_id=branch_id
        )
        missing_grn_logs = find_missing_grn_stock_logs(
            product_id=product_id, branch_id=branch_id
        )
        # Pool is product-global (branch filter does not apply).
        pool_rows = find_farmer_goods_pool_sync(product_id=product_id)
        return {
            "rows": flat_rows,
            "grn_line_count": sum(len(v) for v in corrections_by_branch.values()),
            "grn_branch_count": len(corrections_by_branch),
            "sync_count": len(sync_only),
            "filters": {
                "branch": str(branch_id or ""),
                "product": str(product_id or ""),
                "supplier": str(supplier_id or (supplier.id if supplier else "")),
                "grn_type": grn_type,
            },
            "branches": Branch.objects.order_by("name"),
            "products": Product.objects.order_by("name"),
            "suppliers": Supplier.objects.filter(status=Supplier.Status.ACTIVE)
            .exclude(category=Supplier.Category.RAW_MILK_SUPPLIER)
            .order_by("name"),
            "selected_supplier": supplier,
            "supplier_error": supplier_error,
            "has_work": bool(flat_rows),
            "dup_grn_groups": dup_plan["groups"],
            "dup_grn_delete_count": dup_plan["delete_count"],
            "dup_grn_ref_count": len(dup_plan["set_references"]),
            # Button + table only when there are preview rows (same pattern as other cards).
            "has_dup_grn_work": bool(dup_plan["groups"]),
            "missing_grn_logs": missing_grn_logs,
            "has_missing_grn_logs": bool(missing_grn_logs),
            "pool_sync_rows": pool_rows,
            "has_pool_sync_work": bool(pool_rows),
        }

    def get(self, request):
        return render(request, self.template_name, self._build_context(request))

    def post(self, request):
        from stock_management.grn_log_cleanup import (
            apply_duplicate_grn_stock_log_cleanup,
            apply_missing_grn_stock_log_backfill,
        )
        from suppliers.farmer_goods_pool_sync import apply_farmer_goods_pool_sync
        from suppliers.negative_branch_stock import (
            apply_negative_branch_stock_corrections,
            resolve_adjustment_supplier,
        )

        if not request.user.is_superuser:
            messages.error(request, "Only superusers can correct negative branch stock.")
            return redirect("stock-overview")

        action = (request.POST.get("action") or "").strip()
        branch_id, product_id = self._filter_ids(request)
        grn_type = self._grn_type(request)
        supplier_id = self._supplier_id(request)

        def _redirect_self():
            q = reverse("stock-correct-negative")
            return redirect(
                "%s?branch=%s&product=%s&supplier=%s&grn_type=%s"
                % (q, branch_id or "", product_id or "", supplier_id or "", grn_type)
            )

        if action == "backfill_grn_logs":
            try:
                result = apply_missing_grn_stock_log_backfill(
                    product_id=product_id,
                    branch_id=branch_id,
                    actor=request.user,
                )
            except ValidationError as exc:
                messages.error(request, str(exc))
                return render(request, self.template_name, self._build_context(request))
            if not result["created"]:
                messages.success(request, "No missing GRN stock logs to backfill.")
            else:
                messages.success(
                    request,
                    "Backfilled %s missing GRN Stock History row(s). "
                    "History balance should now match confirmed GRNs."
                    % result["created"],
                )
            return _redirect_self()

        if action == "cleanup_grn_logs":
            try:
                result = apply_duplicate_grn_stock_log_cleanup(
                    product_id=product_id,
                    branch_id=branch_id,
                    actor=request.user,
                )
            except ValidationError as exc:
                messages.error(request, str(exc))
                return render(request, self.template_name, self._build_context(request))
            messages.success(
                request,
                "Duplicate GRN stock logs cleaned: deleted %s row(s), "
                "set %s reference(s)."
                % (result["deleted"], result["updated_refs"]),
            )
            return _redirect_self()

        if action == "sync_fg_pool":
            try:
                result = apply_farmer_goods_pool_sync(
                    product_id=product_id, actor=request.user
                )
            except ValidationError as exc:
                messages.error(request, str(exc))
                return render(request, self.template_name, self._build_context(request))
            if not result["count"]:
                messages.success(request, "Farmer goods pool already in sync.")
            else:
                messages.success(
                    request,
                    "Farmer goods pool synced for %s product(s): "
                    "updated %s, created %s. Pool is now FG GRN + stock "
                    "corrections − applied issues."
                    % (result["count"], result["updated"], result["created"]),
                )
            return _redirect_self()

        if action != "apply":
            if action:
                messages.error(
                    request,
                    "Unknown action %r — nothing was changed. Refresh the page and try again."
                    % action,
                )
            else:
                messages.error(
                    request,
                    "No action received — nothing was changed. Refresh the page and try again.",
                )
            return _redirect_self()

        try:
            supplier = resolve_adjustment_supplier(
                supplier_id,
                "corporate" if grn_type == GRN.GRNType.CORPORATE else "farmer_goods",
            )
            result = apply_negative_branch_stock_corrections(
                actor=request.user,
                supplier=supplier,
                grn_type=grn_type,
                branch_id=branch_id,
                product_id=product_id,
            )
        except ValidationError as exc:
            messages.error(request, str(exc))
            return render(request, self.template_name, self._build_context(request))

        if not result["created_grns"] and not result["synced"]:
            messages.success(request, "No negative branch stock found.")
        else:
            grn_part = (
                "Created GRN(s): %s." % (", ".join(result["created_grns"]))
                if result["created_grns"]
                else "No GRNs needed."
            )
            messages.success(
                request,
                "Stock correction applied. %s Synced %s BranchStock row(s); %s adjustment line(s)."
                % (grn_part, result["synced"], result["line_count"]),
            )
        return redirect("stock-correct-negative")


class StockLogDocumentModalAPI(LoginRequiredMixin, PermissionRequiredMixin, View):
    """JSON document details for a Stock History action click (GRN / issue / transfer)."""

    permission_required = "stock_management.view_stocklog"

    def get(self, request, pk=None):
        from stock_management.log_documents import (
            resolve_fg_issue_document,
            resolve_grn_document,
            resolve_stock_log_document,
        )

        doc_type = (request.GET.get("doc_type") or "").strip()
        doc_id_raw = (request.GET.get("doc_id") or "").strip()
        if doc_type and doc_id_raw.isdigit():
            doc_id = int(doc_id_raw)
            if doc_type == "grn":
                grn = get_object_or_404(
                    GRN.objects.select_related("supplier", "branch"), pk=doc_id
                )
                return JsonResponse(resolve_grn_document(grn))
            if doc_type == "fg_issue":
                issue = get_object_or_404(
                    FarmerGoodsIssue.objects.select_related("product", "from_branch"),
                    pk=doc_id,
                )
                return JsonResponse(resolve_fg_issue_document(issue))

        if pk is None:
            return JsonResponse({"error": "Missing document."}, status=400)

        qs = StockLog.objects.select_related("product")
        branch_ids = get_user_branch_ids(request.user)
        if branch_ids is not None:
            branch_names = set(
                Branch.objects.filter(id__in=branch_ids).values_list("name", flat=True)
            )
            markers = [f"Branch: {name}" for name in branch_names]
            qs = qs.filter(Q(source__in=markers) | Q(destination__in=markers))
        log = get_object_or_404(qs, pk=pk)
        return JsonResponse(resolve_stock_log_document(log))


class StockCountAccessMixin(UserPassesTestMixin):
    def test_func(self):
        user = self.request.user
        return bool(
            user.is_superuser
            or user.has_perm("stock_management.view_stockcount")
            or user.has_perm("stock_management.add_stockcount")
            or user.has_perm("stock_management.change_stockcount")
            or user.has_perm("stock_management.view_mainstock")
        )


class StockCountListView(LoginRequiredMixin, StockCountAccessMixin, ListView):
    model = StockCount
    template_name = "stock_management/stock_count_list.html"
    context_object_name = "counts"

    def get_queryset(self):
        qs = StockCount.objects.select_related("branch", "created_by", "posted_by").annotate(
            line_count=Count("lines")
        )
        branch_ids = get_user_branch_ids(self.request.user)
        if branch_ids is not None:
            qs = qs.filter(branch_id__in=branch_ids)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        draft_count = qs.filter(status=StockCount.Status.DRAFT).count()
        posted_count = qs.filter(status=StockCount.Status.POSTED).count()
        context["count_summary"] = {
            "total": draft_count + posted_count,
            "draft": draft_count,
            "posted": posted_count,
        }
        context["create_form"] = StockCountCreateForm(user=self.request.user)
        context["open_create_modal"] = self.request.GET.get("new") == "1"
        return context


class StockCountCreateView(LoginRequiredMixin, StockCountAccessMixin, FormView):
    template_name = "stock_management/stock_count_create.html"
    form_class = StockCountCreateForm

    def get(self, request, *args, **kwargs):
        # Prefer the list-page modal; keep URL for bookmarks / deep links.
        return redirect(f"{reverse('stock-count-list')}?new=1")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def _wants_json(self):
        accept = (self.request.headers.get("Accept") or "").lower()
        return (
            self.request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in accept
        )

    def form_valid(self, form):
        try:
            count = create_stock_count(
                branch=form.cleaned_data["branch"],
                date=form.cleaned_data["date"],
                remarks=form.cleaned_data.get("remarks") or "",
                created_by=self.request.user,
                actor=self.request.user,
            )
        except ValidationError as exc:
            msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            form.add_error(None, msg)
            return self.form_invalid(form)
        redirect_url = reverse("stock-count-edit", kwargs={"pk": count.pk})
        if self._wants_json():
            return JsonResponse(
                {
                    "ok": True,
                    "count_number": count.count_number,
                    "redirect_url": redirect_url,
                    "message": (
                        f"Draft stock count {count.count_number} created. "
                        "Enter physical quantities."
                    ),
                }
            )
        messages.success(
            self.request,
            f"Draft stock count {count.count_number} created. Enter physical quantities.",
        )
        return redirect(redirect_url)

    def form_invalid(self, form):
        if self._wants_json():
            errors = {
                field: [str(e) for e in errs]
                for field, errs in form.errors.items()
            }
            return JsonResponse(
                {
                    "ok": False,
                    "errors": errors,
                    "error": form.non_field_errors()[0]
                    if form.non_field_errors()
                    else "Could not create stock count.",
                },
                status=400,
            )
        return super().form_invalid(form)


class StockCountEditView(LoginRequiredMixin, StockCountAccessMixin, View):
    template_name = "stock_management/stock_count_form.html"

    def _get_count(self):
        qs = StockCount.objects.select_related("branch", "correction_grn").prefetch_related(
            "lines__product"
        )
        branch_ids = get_user_branch_ids(self.request.user)
        if branch_ids is not None:
            qs = qs.filter(branch_id__in=branch_ids)
        return get_object_or_404(qs, pk=self.kwargs["pk"])

    def get(self, request, pk):
        count = self._get_count()
        lines = list(count.lines.select_related("product"))
        counted = 0
        excess = 0
        shortage = 0
        for line in lines:
            if line.physical_qty is None:
                continue
            counted += 1
            variance = line.variance
            if variance is None:
                continue
            if variance > 0:
                excess += 1
            elif variance < 0:
                shortage += 1

        shortage_issues = []
        if count.status == StockCount.Status.POSTED:
            shortage_issues = list(
                StockIssue.objects.filter(
                    issued_to_type=StockIssue.IssuedToType.STOCKTAKE,
                    issued_to_id=count.pk,
                )
                .select_related("product")
                .order_by("product__name", "id")
            )

        return render(
            request,
            self.template_name,
            {
                "count": count,
                "lines": lines,
                "can_post": request.user.is_superuser
                or request.user.has_perm("stock_management.post_stockcount")
                or request.user.has_perm("stock_management.change_stockcount"),
                "line_summary": {
                    "total": len(lines),
                    "counted": counted,
                    "remaining": max(len(lines) - counted, 0),
                    "excess": excess,
                    "shortage": shortage,
                },
                "shortage_issues": shortage_issues,
            },
        )

    def post(self, request, pk):
        count = self._get_count()
        if count.status != StockCount.Status.DRAFT:
            messages.error(request, "Posted stock counts cannot be edited.")
            if self._wants_json(request):
                return JsonResponse(
                    {"ok": False, "error": "Posted stock counts cannot be edited."},
                    status=400,
                )
            return redirect("stock-count-edit", pk=count.pk)

        action = (request.POST.get("action") or "save").strip()
        if action == "refresh_book":
            try:
                refresh_stock_count_book_quantities(count=count, actor=request.user)
                messages.success(request, "Book quantities refreshed from current Available.")
            except ValidationError as exc:
                messages.error(
                    request,
                    exc.messages[0] if getattr(exc, "messages", None) else str(exc),
                )
            return redirect("stock-count-edit", pk=count.pk)

        line_quantities = {}
        for key, value in request.POST.items():
            if not key.startswith("physical_"):
                continue
            line_id = key.replace("physical_", "", 1)
            if line_id.isdigit():
                line_quantities[int(line_id)] = value

        try:
            save_stock_count_physical_quantities(
                count=count,
                line_quantities=line_quantities,
                actor=request.user,
            )
        except ValidationError as exc:
            msg = exc.messages[0] if getattr(exc, "messages", None) else str(exc)
            if self._wants_json(request):
                return JsonResponse({"ok": False, "error": msg}, status=400)
            messages.error(request, msg)
            return redirect("stock-count-edit", pk=count.pk)

        if action == "autosave":
            line_id = next(iter(line_quantities.keys()), None)
            variance = None
            physical = None
            book = None
            if line_id is not None:
                line = count.lines.filter(pk=line_id).first()
                if line is not None:
                    book = line.book_qty
                    physical = line.physical_qty
                    variance = line.variance
            return JsonResponse(
                {
                    "ok": True,
                    "line_id": line_id,
                    "book_qty": str(book) if book is not None else None,
                    "physical_qty": str(physical) if physical is not None else None,
                    "variance": str(variance) if variance is not None else None,
                }
            )

        if action == "post":
            can_post = (
                request.user.is_superuser
                or request.user.has_perm("stock_management.post_stockcount")
                or request.user.has_perm("stock_management.change_stockcount")
            )
            if not can_post:
                messages.error(request, "You do not have permission to post stock counts.")
                return redirect("stock-count-edit", pk=count.pk)
            try:
                posted = post_stock_count(count=count, actor=request.user)
                messages.success(
                    request,
                    f"Stock count {posted.count_number} posted. "
                    "Shortages written off and excesses posted as stock corrections.",
                )
                return redirect("stock-count-edit", pk=posted.pk)
            except ValidationError as exc:
                messages.error(
                    request,
                    exc.messages[0] if getattr(exc, "messages", None) else str(exc),
                )
                return redirect("stock-count-edit", pk=count.pk)

        messages.success(request, "Physical quantities saved.")
        return redirect("stock-count-edit", pk=count.pk)

    @staticmethod
    def _wants_json(request):
        accept = (request.headers.get("Accept") or "").lower()
        return (
            request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or "application/json" in accept
        )


class StockCountDeleteView(LoginRequiredMixin, StockCountAccessMixin, View):
    def post(self, request, pk):
        qs = StockCount.objects.all()
        branch_ids = get_user_branch_ids(request.user)
        if branch_ids is not None:
            qs = qs.filter(branch_id__in=branch_ids)
        count = get_object_or_404(qs, pk=pk)
        if count.status != StockCount.Status.DRAFT:
            messages.error(request, "Only draft stock counts can be deleted.")
            return redirect("stock-count-list")
        if not (
            request.user.is_superuser
            or request.user.has_perm("stock_management.delete_stockcount")
        ):
            messages.error(request, "You do not have permission to delete stock counts.")
            return redirect("stock-count-list")
        number = count.count_number
        count.delete()
        messages.success(request, f"Draft stock count {number} deleted.")
        return redirect("stock-count-list")


class StockCountPrintView(LoginRequiredMixin, StockCountAccessMixin, DetailView):
    model = StockCount
    template_name = "stock_management/stock_count_print.html"
    context_object_name = "count"

    def get_queryset(self):
        qs = StockCount.objects.select_related("branch", "created_by", "posted_by").prefetch_related(
            "lines__product"
        )
        branch_ids = get_user_branch_ids(self.request.user)
        if branch_ids is not None:
            qs = qs.filter(branch_id__in=branch_ids)
        return qs

