from django.urls import path

from . import views

app_name = "user_management"

urlpatterns = [
    path("", views.UserListView.as_view(), name="user-list"),
    path("new/", views.UserCreateView.as_view(), name="user-create"),
    path("<int:pk>/edit/", views.UserUpdateView.as_view(), name="user-update"),
    path("<int:pk>/roles/", views.UserRolesUpdateView.as_view(), name="user-roles-update"),
    path("<int:pk>/status/", views.UserStatusUpdateView.as_view(), name="user-status-update"),
    path("<int:pk>/password/", views.UserAdminPasswordView.as_view(), name="user-set-password"),
    path("<int:pk>/delete/", views.UserDeleteView.as_view(), name="user-delete"),
    path("roles/", views.GroupListView.as_view(), name="group-list"),
    path("roles/new/", views.GroupCreateView.as_view(), name="group-create"),
    path("roles/<int:pk>/edit/", views.GroupUpdateView.as_view(), name="group-update"),
    path(
        "roles/<int:pk>/permissions/",
        views.GroupPermissionsView.as_view(),
        name="group-permissions",
    ),
    path("roles/<int:pk>/delete/", views.GroupDeleteView.as_view(), name="group-delete"),
    path("permissions/", views.RolePermissionMatrixView.as_view(), name="role-permission-matrix"),
    path("assign-roles/", views.AssignRolesView.as_view(), name="assign-roles"),
    path("assign-roles/user-data/", views.AssignRolesUserDataView.as_view(), name="assign-roles-user-data"),
    path("audit-logs/", views.AuditLogListView.as_view(), name="audit-logs"),
    path("profile/", views.ProfileUpdateView.as_view(), name="profile"),
]
