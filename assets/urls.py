from django.urls import path

from . import views

urlpatterns = [
    path("", views.AssetListView.as_view(), name="asset-list"),
    path("export/pdf/", views.AssetRegisterPDFView.as_view(), name="asset-register-pdf"),  # classified FAR
    path("add/", views.AssetCreateView.as_view(), name="asset-add"),
    path("quick-add/", views.AssetQuickCreateView.as_view(), name="asset-quick-add"),
    path("types/", views.AssetTypeListView.as_view(), name="asset-type-list"),
    path("types/add/", views.AssetTypeCreateView.as_view(), name="asset-type-add"),
    path("types/<int:pk>/edit/", views.AssetTypeUpdateView.as_view(), name="asset-type-edit"),
    path("types/<int:pk>/delete/", views.AssetTypeDeleteView.as_view(), name="asset-type-delete"),
    path("categories/", views.AssetCategoryListView.as_view(), name="asset-category-list"),
    path("categories/add/", views.AssetCategoryCreateView.as_view(), name="asset-category-add"),
    path(
        "categories/<int:pk>/edit/",
        views.AssetCategoryUpdateView.as_view(),
        name="asset-category-edit",
    ),
    path(
        "categories/<int:pk>/delete/",
        views.AssetCategoryDeleteView.as_view(),
        name="asset-category-delete",
    ),
    path("locations/", views.AssetLocationListView.as_view(), name="asset-location-list"),
    path("locations/add/", views.AssetLocationCreateView.as_view(), name="asset-location-add"),
    path(
        "locations/<int:pk>/edit/",
        views.AssetLocationUpdateView.as_view(),
        name="asset-location-edit",
    ),
    path(
        "locations/<int:pk>/delete/",
        views.AssetLocationDeleteView.as_view(),
        name="asset-location-delete",
    ),
    path("labels/", views.AssetLabelGenerateView.as_view(), name="asset-label-generate"),
    path("labels/<int:pk>/print/", views.AssetLabelPrintView.as_view(), name="asset-label-print"),
    path("verifications/", views.AssetVerificationListView.as_view(), name="asset-verification-list"),
    path("policies/", views.DepreciationPolicyListView.as_view(), name="asset-policy-list"),
    path("policies/add/", views.DepreciationPolicyCreateView.as_view(), name="asset-policy-add"),
    path(
        "policies/<int:pk>/edit/",
        views.DepreciationPolicyUpdateView.as_view(),
        name="asset-policy-edit",
    ),
    path("depreciation/", views.DepreciationRunView.as_view(), name="asset-depreciation-run"),
    path("settings/", views.AssetSettingsView.as_view(), name="asset-settings"),
    path("transfer/", views.AssetBulkTransferView.as_view(), name="asset-bulk-transfer"),
    path("<int:pk>/json/", views.AssetDetailJsonView.as_view(), name="asset-detail-json"),
    path("<int:pk>/quick-edit/", views.AssetQuickUpdateView.as_view(), name="asset-quick-edit"),
    path("<int:pk>/quick-status/", views.AssetQuickStatusView.as_view(), name="asset-quick-status"),
    path("<int:pk>/", views.AssetDetailView.as_view(), name="asset-detail"),
    path("<int:pk>/edit/", views.AssetUpdateView.as_view(), name="asset-edit"),
    path("<int:pk>/delete/", views.AssetDeleteView.as_view(), name="asset-delete"),
    path("<int:pk>/verify/", views.AssetVerifyView.as_view(), name="asset-verify"),
    path("<int:pk>/value/", views.AssetValueView.as_view(), name="asset-value"),
    path("<int:pk>/transfer/", views.AssetTransferView.as_view(), name="asset-transfer"),
]
