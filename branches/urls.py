from django.urls import path

from .views import (
    BranchCreateView,
    BranchDeleteView,
    BranchDetailView,
    BranchListView,
    BranchMilkCollectionQuickDeleteView,
    BranchOpeningStockUpdateView,
    BranchStockAdjustmentCreateView,
    BranchStockAdjustmentDeleteView,
    BranchStockAdjustmentUpdateView,
    BranchQuickCreateView,
    BranchQuickUpdateView,
    BranchRouteQuickCreateView,
    BranchRouteQuickDeleteView,
    BranchRouteQuickUpdateView,
    BranchUpdateView,
)

urlpatterns = [
    path("", BranchListView.as_view(), name="branch-list"),
    path("add/", BranchCreateView.as_view(), name="branch-add"),
    path("quick-add/", BranchQuickCreateView.as_view(), name="branch-quick-add"),
    path(
        "<int:pk>/collections/<int:collection_id>/quick-delete/",
        BranchMilkCollectionQuickDeleteView.as_view(),
        name="branch-collection-quick-delete",
    ),
    path("<int:pk>/quick-edit/", BranchQuickUpdateView.as_view(), name="branch-quick-edit"),
    path("<int:pk>/edit/", BranchUpdateView.as_view(), name="branch-edit"),
    path("<int:pk>/delete/", BranchDeleteView.as_view(), name="branch-delete"),
    path("<int:pk>/routes/quick-add/", BranchRouteQuickCreateView.as_view(), name="branch-route-quick-add"),
    path(
        "<int:pk>/opening-stock/",
        BranchOpeningStockUpdateView.as_view(),
        name="branch-opening-stock-update",
    ),
    path(
        "<int:pk>/stock-adjustments/add/",
        BranchStockAdjustmentCreateView.as_view(),
        name="branch-stock-adjustment-add",
    ),
    path(
        "<int:pk>/stock-adjustments/<int:adjustment_id>/edit/",
        BranchStockAdjustmentUpdateView.as_view(),
        name="branch-stock-adjustment-edit",
    ),
    path(
        "<int:pk>/stock-adjustments/<int:adjustment_id>/delete/",
        BranchStockAdjustmentDeleteView.as_view(),
        name="branch-stock-adjustment-delete",
    ),
    path(
        "<int:pk>/routes/<int:route_id>/quick-edit/",
        BranchRouteQuickUpdateView.as_view(),
        name="branch-route-quick-edit",
    ),
    path(
        "<int:pk>/routes/<int:route_id>/quick-delete/",
        BranchRouteQuickDeleteView.as_view(),
        name="branch-route-quick-delete",
    ),
    path("<int:pk>/", BranchDetailView.as_view(), name="branch-detail"),
]
