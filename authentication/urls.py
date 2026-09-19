from django.contrib.auth import views as auth_views
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin
from django.urls import path
from django.views.generic import RedirectView

from .views import UserLoginView, UserLogoutView, UserPasswordChangeView


class RolePermissionRedirectView(LoginRequiredMixin, PermissionRequiredMixin, RedirectView):
    """Legacy URL `/auth/roles/` → user management assign roles."""

    permission_required = "auth.change_group"
    pattern_name = "user_management:assign-roles"


urlpatterns = [
    path('login/', UserLoginView.as_view(), name='login'),
    path('logout/', UserLogoutView.as_view(), name='logout'),
    path('roles/', RolePermissionRedirectView.as_view(), name='role-permissions'),
    path('password-change/', UserPasswordChangeView.as_view(), name='password_change'),
    path('password-change/done/', auth_views.PasswordChangeDoneView.as_view(template_name='registration/password_change_done.html'), name='password_change_done'),
    path('password-reset/', auth_views.PasswordResetView.as_view(template_name='registration/password_reset_form.html'), name='password_reset'),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(template_name='registration/password_reset_done.html'), name='password_reset_done'),
    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(template_name='registration/password_reset_confirm.html'), name='password_reset_confirm'),
    path('reset/done/', auth_views.PasswordResetCompleteView.as_view(template_name='registration/password_reset_complete.html'), name='password_reset_complete'),
]
