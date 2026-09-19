from django.contrib.auth.views import LoginView, LogoutView, PasswordChangeView
from django.urls import reverse_lazy

from .forms import StyledPasswordChangeForm


class UserLoginView(LoginView):
    template_name = "registration/login.html"


class UserLogoutView(LogoutView):
    """Logout via GET or POST (navbar uses a link; Django's LogoutView is POST-only)."""

    http_method_names = ["get", "post", "head", "options"]

    def get(self, request, *args, **kwargs):
        return self.post(request, *args, **kwargs)


class UserPasswordChangeView(PasswordChangeView):
    template_name = "registration/password_change_form.html"
    form_class = StyledPasswordChangeForm
    success_url = reverse_lazy("password_change_done")
