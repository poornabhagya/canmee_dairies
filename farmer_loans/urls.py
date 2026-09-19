from django.urls import path

from .views import (
    FarmerLoanApproveView,
    BranchFarmerLookupView,
    FarmerLoanCreateView,
    FarmerLoanDeleteView,
    FarmerLoanDetailView,
    FarmerLoanInstalmentManualSettleView,
    FarmerLoanListView,
    FarmerLoanRejectView,
    FarmerLoanSchedulePaidUpdateView,
    FarmerLoanSubmitView,
    FarmerLoanUpdateView,
)

urlpatterns = [
    path("", FarmerLoanListView.as_view(), name="farmer-loan-list"),
    path("add/", FarmerLoanCreateView.as_view(), name="farmer-loan-add"),
    path("<int:pk>/", FarmerLoanDetailView.as_view(), name="farmer-loan-detail"),
    path("<int:pk>/edit/", FarmerLoanUpdateView.as_view(), name="farmer-loan-edit"),
    path("<int:pk>/delete/", FarmerLoanDeleteView.as_view(), name="farmer-loan-delete"),
    path("<int:pk>/submit/", FarmerLoanSubmitView.as_view(), name="farmer-loan-submit"),
    path("<int:pk>/approve/", FarmerLoanApproveView.as_view(), name="farmer-loan-approve"),
    path("<int:pk>/reject/", FarmerLoanRejectView.as_view(), name="farmer-loan-reject"),
    path(
        "<int:pk>/schedule-paid/",
        FarmerLoanSchedulePaidUpdateView.as_view(),
        name="farmer-loan-schedule-paid",
    ),
    path(
        "schedule/<int:pk>/manual-settle/",
        FarmerLoanInstalmentManualSettleView.as_view(),
        name="farmer-loan-instalment-manual-settle",
    ),
    path("ajax/farmers-by-branch/", BranchFarmerLookupView.as_view(), name="farmer-loan-farmers-by-branch"),
]
