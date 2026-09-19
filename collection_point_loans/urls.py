from django.urls import path

from .views import (
    BranchCollectionPointLookupView,
    CollectionPointLoanAccountExcelView,
    CollectionPointLoanAccountPDFView,
    CollectionPointLoanAccountView,
    CollectionPointLoanApproveView,
    CollectionPointLoanCreateView,
    CollectionPointLoanDeleteView,
    CollectionPointLoanDetailView,
    CollectionPointLoanInstalmentManualSettleView,
    CollectionPointLoanListView,
    CollectionPointLoanRejectView,
    CollectionPointLoanSchedulePaidUpdateView,
    CollectionPointLoanSubmitView,
    CollectionPointLoanUpdateView,
    CollectionPointLoanRegisterPDFView,
)

urlpatterns = [
    path("", CollectionPointLoanListView.as_view(), name="cp-loan-list"),
    path("export/pdf/", CollectionPointLoanRegisterPDFView.as_view(), name="cp-loan-list-export-pdf"),
    path("add/", CollectionPointLoanCreateView.as_view(), name="cp-loan-add"),
    path("<int:pk>/", CollectionPointLoanDetailView.as_view(), name="cp-loan-detail"),
    path("<int:pk>/account/", CollectionPointLoanAccountView.as_view(), name="cp-loan-account"),
    path(
        "<int:pk>/account/export/excel/",
        CollectionPointLoanAccountExcelView.as_view(),
        name="cp-loan-account-export-excel",
    ),
    path(
        "<int:pk>/account/export/pdf/",
        CollectionPointLoanAccountPDFView.as_view(),
        name="cp-loan-account-export-pdf",
    ),
    path("<int:pk>/edit/", CollectionPointLoanUpdateView.as_view(), name="cp-loan-edit"),
    path("<int:pk>/delete/", CollectionPointLoanDeleteView.as_view(), name="cp-loan-delete"),
    path("<int:pk>/submit/", CollectionPointLoanSubmitView.as_view(), name="cp-loan-submit"),
    path("<int:pk>/approve/", CollectionPointLoanApproveView.as_view(), name="cp-loan-approve"),
    path("<int:pk>/reject/", CollectionPointLoanRejectView.as_view(), name="cp-loan-reject"),
    path(
        "<int:pk>/schedule-paid/",
        CollectionPointLoanSchedulePaidUpdateView.as_view(),
        name="cp-loan-schedule-paid",
    ),
    path(
        "schedule/<int:pk>/manual-settle/",
        CollectionPointLoanInstalmentManualSettleView.as_view(),
        name="cp-loan-instalment-manual-settle",
    ),
    path(
        "ajax/points-by-branch/",
        BranchCollectionPointLookupView.as_view(),
        name="cp-loan-points-by-branch",
    ),
]
