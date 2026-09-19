from django.contrib.auth.mixins import PermissionRequiredMixin
from django.shortcuts import redirect


class AnyPermissionRequiredMixin(PermissionRequiredMixin):
    """User needs at least one permission from permission_required (OR, not AND)."""

    def has_permission(self):
        perms = self.get_permission_required()
        if isinstance(perms, str):
            perms = (perms,)
        user = self.request.user
        return any(user.has_perm(perm) for perm in perms)


class RedirectGetDeleteMixin:
    """Skip HTML confirm page; clients use SweetAlert + POST only."""

    def get(self, request, *args, **kwargs):
        return redirect(str(self.success_url))


class ModelFormPageTitleMixin:
    """Expose model_verbose_name for templates (Django forbids _meta in template variables)."""

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        model = getattr(self, "model", None)
        if model is not None:
            context["model_verbose_name"] = model._meta.verbose_name
        return context
