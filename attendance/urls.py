from django.urls import path

from . import views

app_name = "attendance"

urlpatterns = [
    path("login/", views.AttendanceLoginView.as_view(), name="login"),
    path("set-password/", views.AttendanceSetPasswordView.as_view(), name="set-password"),
    path("logout/", views.AttendanceLogoutView.as_view(), name="logout"),
    path("", views.AttendancePunchView.as_view(), name="punch"),
    path("history/", views.AttendanceMyHistoryView.as_view(), name="my-history"),
    path("profile/", views.AttendanceProfileView.as_view(), name="profile"),
    path(
        "profile/password/",
        views.AttendancePasswordChangeView.as_view(),
        name="password-change",
    ),
    path("admin/", views.AttendanceAdminListView.as_view(), name="admin-list"),
    path("admin/<int:pk>/", views.AttendanceAdminDetailView.as_view(), name="admin-detail"),
]
