from django.apps import apps as django_apps
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import LoginRequiredMixin, PermissionRequiredMixin, UserPassesTestMixin
from django.contrib.auth.models import Group, Permission
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from canmee_dairies.mixins import RedirectGetDeleteMixin
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views import View
from datetime import datetime, time, timedelta
from urllib.parse import urlencode
from django.views.generic import (
    CreateView,
    DeleteView,
    FormView,
    ListView,
    TemplateView,
    UpdateView,
)
from django.views.generic.detail import SingleObjectMixin

from .forms import (
    AdminSetPasswordForm,
    AssignRolesForm,
    GroupForm,
    GroupPermissionsForm,
    ProfileForm,
    SelfUserForm,
    StaffUserChangeForm,
    StaffUserCreationForm,
)
from branches.models import Branch
from branches.utils import get_allowed_branches_qs

from .models import AuditEvent, UserProfile

User = get_user_model()


class UserListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = User
    template_name = "user_management/user_list.html"
    context_object_name = "user_list"
    permission_required = "auth.view_user"
    ordering = ["username"]

    def _base_queryset(self):
        return (
            User.objects.filter(is_superuser=False)
            .prefetch_related("groups", "assigned_branches")
            .order_by(*self.ordering)
        )

    def _apply_search_filters(self, qs):
        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(username__icontains=q)
                | Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(email__icontains=q)
            )
        role = (self.request.GET.get("role") or "").strip()
        if role.isdigit():
            qs = qs.filter(groups__id=int(role))
        branch = (self.request.GET.get("branch") or "").strip()
        if branch.isdigit():
            qs = qs.filter(assigned_branches__id=int(branch))
        return qs.distinct()

    def get_queryset(self):
        qs = self._apply_search_filters(self._base_queryset())
        status = (self.request.GET.get("status") or "").strip()
        if status == "active":
            qs = qs.filter(is_active=True)
        elif status == "inactive":
            qs = qs.filter(is_active=False)
        elif status == "staff":
            qs = qs.filter(is_staff=True)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["search_query"] = (self.request.GET.get("q") or "").strip()
        ctx["filter_role"] = (self.request.GET.get("role") or "").strip()
        ctx["filter_status"] = (self.request.GET.get("status") or "").strip()
        ctx["filter_branch"] = (self.request.GET.get("branch") or "").strip()
        ctx["all_groups"] = Group.objects.order_by("name")
        if self.request.user.is_superuser:
            ctx["filter_branches"] = Branch.objects.order_by("code", "name")
        else:
            ctx["filter_branches"] = get_allowed_branches_qs(self.request.user).order_by(
                "code", "name"
            )
        tab_base = self._apply_search_filters(self._base_queryset())
        ctx["status_tab_counts"] = {
            "all": tab_base.count(),
            "active": tab_base.filter(is_active=True).count(),
            "inactive": tab_base.filter(is_active=False).count(),
            "staff": tab_base.filter(is_staff=True).count(),
        }
        return ctx


class UserRolesUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "auth.change_user"

    def post(self, request, pk):
        target = get_object_or_404(User, pk=pk)
        if target.is_superuser and not request.user.is_superuser:
            return JsonResponse(
                {"ok": False, "error": "Only a superuser can change roles for another superuser."},
                status=403,
            )
        group_ids = [int(v) for v in request.POST.getlist("groups") if str(v).isdigit()]
        groups = list(Group.objects.filter(pk__in=group_ids).order_by("name"))
        target.groups.set(groups)
        roles = [{"id": g.pk, "name": g.name} for g in groups]
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": True, "roles": roles})
        messages.success(request, f"Roles updated for {target.get_username()}.")
        return redirect("user_management:user-list")


class UserStatusUpdateView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "auth.change_user"

    def post(self, request, pk):
        target = get_object_or_404(User, pk=pk)
        if target.pk == request.user.pk:
            return JsonResponse(
                {"ok": False, "error": "You cannot change your own status here."},
                status=400,
            )
        if target.is_superuser and not request.user.is_superuser:
            return JsonResponse(
                {"ok": False, "error": "Only a superuser can change status for another superuser."},
                status=403,
            )
        raw = (request.POST.get("is_active") or "").strip().lower()
        if raw not in {"1", "0", "true", "false"}:
            return JsonResponse({"ok": False, "error": "Invalid status."}, status=400)
        is_active = raw in {"1", "true"}
        target.is_active = is_active
        target.save(update_fields=["is_active"])
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return JsonResponse({"ok": True, "is_active": is_active})
        messages.success(request, f"Status updated for {target.get_username()}.")
        return redirect("user_management:user-list")


class UserCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = User
    form_class = StaffUserCreationForm
    template_name = "user_management/user_form.html"
    permission_required = "auth.add_user"
    success_url = reverse_lazy("user_management:user-list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = "Add user"
        return ctx

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, "User created.")
        return super().form_valid(form)


class UserUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = User
    form_class = StaffUserChangeForm
    template_name = "user_management/user_form.html"
    permission_required = "auth.change_user"
    success_url = reverse_lazy("user_management:user-list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = "Edit user"
        return ctx

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        messages.success(self.request, "User updated.")
        return super().form_valid(form)


class UserAdminPasswordView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    form_class = AdminSetPasswordForm
    template_name = "user_management/user_set_password.html"
    permission_required = "auth.change_user"

    def dispatch(self, request, *args, **kwargs):
        self.target_user = get_object_or_404(User, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["target_user"] = self.target_user
        return ctx

    def form_valid(self, form):
        self.target_user.set_password(form.cleaned_data["new_password1"])
        self.target_user.save(update_fields=["password"])
        messages.success(self.request, "Password updated for this user.")
        return redirect("user_management:user-update", pk=self.target_user.pk)


class UserDeleteView(LoginRequiredMixin, PermissionRequiredMixin, RedirectGetDeleteMixin, DeleteView):
    model = User
    permission_required = "auth.delete_user"
    success_url = reverse_lazy("user_management:user-list")
    context_object_name = "target_user"

    def get_queryset(self):
        return User.objects.all()

    def dispatch(self, request, *args, **kwargs):
        obj = get_object_or_404(User, pk=kwargs["pk"])
        if obj.pk == request.user.pk:
            messages.error(request, "You cannot delete your own account.")
            return redirect("user_management:user-list")
        if obj.is_superuser and not request.user.is_superuser:
            messages.error(request, "Only a superuser can delete another superuser.")
            return redirect("user_management:user-list")
        return super().dispatch(request, *args, **kwargs)

    def form_valid(self, form):
        messages.success(self.request, "User deleted.")
        return super().form_valid(form)


class GroupListView(LoginRequiredMixin, PermissionRequiredMixin, ListView):
    model = Group
    template_name = "user_management/group_list.html"
    context_object_name = "groups"
    permission_required = "auth.view_group"
    ordering = ["name"]


class GroupCreateView(LoginRequiredMixin, PermissionRequiredMixin, CreateView):
    model = Group
    form_class = GroupForm
    template_name = "user_management/group_form.html"
    permission_required = "auth.add_group"
    success_url = reverse_lazy("user_management:group-list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = "Add role"
        return ctx

    def form_valid(self, form):
        messages.success(self.request, "Role created.")
        return super().form_valid(form)


class GroupUpdateView(LoginRequiredMixin, PermissionRequiredMixin, UpdateView):
    model = Group
    form_class = GroupForm
    template_name = "user_management/group_form.html"
    permission_required = "auth.change_group"
    success_url = reverse_lazy("user_management:group-list")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["form_title"] = "Edit role"
        return ctx

    def form_valid(self, form):
        messages.success(self.request, "Role updated.")
        return super().form_valid(form)


class GroupPermissionsView(LoginRequiredMixin, PermissionRequiredMixin, SingleObjectMixin, FormView):
    model = Group
    form_class = GroupPermissionsForm
    template_name = "user_management/group_permissions.html"
    permission_required = "auth.change_group"

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        return super().get(request, *args, **kwargs)

    def get_success_url(self):
        return reverse_lazy("user_management:group-list")

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        return super().post(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.request.method == "GET":
            kwargs.setdefault("initial", {})
            kwargs["initial"]["permissions"] = self.get_object().permissions.all()
        return kwargs

    def form_valid(self, form):
        group = self.get_object()
        group.permissions.set(form.cleaned_data["permissions"])
        messages.success(self.request, "Role permissions saved.")
        return redirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        self.object = self.get_object()
        ctx = super().get_context_data(**kwargs)
        perms = list(
            Permission.objects.select_related("content_type").order_by(
                "content_type__app_label",
                "content_type__model",
                "codename",
            )
        )
        by_app = {}
        for p in perms:
            by_app.setdefault(p.content_type.app_label, []).append(p)
        permissions_by_app = []
        for app_label in sorted(by_app.keys()):
            try:
                app_title = django_apps.get_app_config(app_label).verbose_name
            except LookupError:
                app_title = app_label.replace("_", " ").title()
            permissions_by_app.append(
                {
                    "app_label": app_label,
                    "app_title": str(app_title),
                    "permissions": by_app[app_label],
                }
            )
        ctx["permissions_by_app"] = permissions_by_app
        if self.request.method == "POST":
            ctx["selected_permission_ids"] = {
                int(x) for x in self.request.POST.getlist("permissions") if str(x).isdigit()
            }
        else:
            ctx["selected_permission_ids"] = set(
                self.object.permissions.values_list("id", flat=True)
            )
        return ctx


class GroupDeleteView(LoginRequiredMixin, PermissionRequiredMixin, RedirectGetDeleteMixin, DeleteView):
    model = Group
    permission_required = "auth.delete_group"
    success_url = reverse_lazy("user_management:group-list")
    context_object_name = "role"

    def form_valid(self, form):
        messages.success(self.request, "Role deleted.")
        return super().form_valid(form)


class RolePermissionMatrixView(LoginRequiredMixin, PermissionRequiredMixin, TemplateView):
    template_name = "user_management/role_permission_matrix.html"
    permission_required = "auth.change_group"

    def _get_permissions_by_app(self):
        perms = list(
            Permission.objects.select_related("content_type").order_by(
                "content_type__app_label",
                "codename",
            )
        )
        grouped = {}
        for p in perms:
            grouped.setdefault(p.content_type.app_label, []).append(p)
        return grouped

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["roles"] = Group.objects.prefetch_related("permissions").order_by("name")
        ctx["permissions_by_app"] = self._get_permissions_by_app()
        return ctx

    def post(self, request, *args, **kwargs):
        roles = Group.objects.all()
        for role in roles:
            selected_ids = request.POST.getlist(f"role_{role.id}_permissions")
            role.permissions.set([pid for pid in selected_ids if str(pid).isdigit()])
        messages.success(request, "Role permissions matrix updated.")
        return redirect("user_management:role-permission-matrix")


class AssignRolesView(LoginRequiredMixin, PermissionRequiredMixin, FormView):
    form_class = AssignRolesForm
    template_name = "user_management/assign_roles.html"
    permission_required = "auth.change_group"
    success_url = reverse_lazy("user_management:assign-roles")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user_queryset"] = User.objects.order_by("username")
        return kwargs

    def get_initial(self):
        initial = super().get_initial()
        uid = self.request.GET.get("user")
        if uid and uid.isdigit():
            u = User.objects.filter(pk=int(uid)).first()
            if u:
                initial["user"] = u.pk
                initial["groups"] = u.groups.all()
        return initial

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["all_groups"] = Group.objects.order_by("name")
        ctx["users"] = User.objects.order_by("username")
        preselected = (self.request.GET.get("user") or "").strip()
        ctx["preselected_user_id"] = preselected if preselected.isdigit() else ""
        if not ctx["preselected_user_id"] and self.request.method == "GET":
            form = ctx.get("form")
            if form and form.initial.get("user"):
                ctx["preselected_user_id"] = str(form.initial["user"])
        return ctx

    def form_valid(self, form):
        user = form.cleaned_data["user"]
        user.groups.set(form.cleaned_data["groups"])
        messages.success(self.request, f"Roles updated for {user.get_username()}.")
        return super().form_valid(form)


class AssignRolesUserDataView(LoginRequiredMixin, PermissionRequiredMixin, View):
    permission_required = "auth.change_group"

    def get(self, request):
        uid = (request.GET.get("user") or "").strip()
        if not uid.isdigit():
            return JsonResponse({"ok": False, "error": "Invalid user."}, status=400)
        user = get_object_or_404(User.objects.prefetch_related("groups"), pk=int(uid))
        roles = list(user.groups.order_by("name"))
        branches = list(user.assigned_branches.order_by("code", "name"))
        return JsonResponse(
            {
                "ok": True,
                "user": {
                    "id": user.pk,
                    "username": user.get_username(),
                    "display_name": user.get_full_name() or user.get_username(),
                    "email": user.email or "",
                    "is_active": user.is_active,
                    "is_staff": user.is_staff,
                    "role_ids": [g.pk for g in roles],
                    "roles": [{"id": g.pk, "name": g.name} for g in roles],
                    "branches": [b.name for b in branches],
                },
            }
        )


class ProfileUpdateView(LoginRequiredMixin, TemplateView):
    template_name = "user_management/profile.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        profile, _ = UserProfile.objects.get_or_create(user=self.request.user)
        user = self.request.user
        ctx["profile"] = profile
        ctx["user_groups"] = user.groups.order_by("name")
        ctx.setdefault(
            "user_form",
            SelfUserForm(instance=user, prefix="u"),
        )
        ctx.setdefault(
            "profile_form",
            ProfileForm(instance=profile, prefix="p"),
        )
        return ctx

    def post(self, request, *args, **kwargs):
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        if request.POST.get("p-clear_avatar"):
            if profile.avatar:
                profile.avatar.delete(save=False)
            profile.avatar = None
            profile.save(update_fields=["avatar"])
            messages.success(request, "Profile picture removed.")
            return redirect("user_management:profile")

        user_form = SelfUserForm(request.POST, instance=request.user, prefix="u")
        profile_form = ProfileForm(
            request.POST,
            request.FILES,
            instance=profile,
            prefix="p",
        )
        if user_form.is_valid() and profile_form.is_valid():
            with transaction.atomic():
                user_form.save()
                profile_form.save()
            messages.success(request, "Your profile was updated.")
            return redirect("user_management:profile")
        ctx = self.get_context_data(user_form=user_form, profile_form=profile_form)
        ctx["profile"] = profile
        ctx["user_groups"] = request.user.groups.order_by("name")
        return self.render_to_response(ctx)


AUTH_ACTIONS = ("login", "logout", "login_failed")
CREATE_ACTIONS = ("create", "created")
DELETE_ACTIONS = ("delete", "deleted")
LIST_LIMIT = 2000


class SuperuserRequiredMixin(UserPassesTestMixin):
    def test_func(self):
        return self.request.user.is_authenticated and self.request.user.is_superuser


class AuditLogListView(LoginRequiredMixin, SuperuserRequiredMixin, ListView):
    model = AuditEvent
    template_name = "user_management/audit_log_list.html"
    context_object_name = "audit_events"

    def _range_key(self):
        key = (self.request.GET.get("range") or "").strip().lower()
        if key in {"today", "7d", "14d", "30d", "all"}:
            return key
        if self.request.GET.get("date_from") or self.request.GET.get("date_to"):
            return "custom"
        return "14d"

    def _date_bounds(self):
        tz = timezone.get_current_timezone()
        today = timezone.localdate()
        key = self._range_key()
        date_from = parse_date((self.request.GET.get("date_from") or "").strip())
        date_to = parse_date((self.request.GET.get("date_to") or "").strip())
        if key == "all":
            return None, None, key
        if key == "today":
            date_from = date_to = today
        elif key == "7d":
            date_from, date_to = today - timedelta(days=6), today
        elif key == "30d":
            date_from, date_to = today - timedelta(days=29), today
        elif key == "14d":
            date_from, date_to = today - timedelta(days=13), today
        elif key == "custom":
            if date_from and date_to and date_from > date_to:
                date_from, date_to = date_to, date_from
            if not date_from and not date_to:
                date_from, date_to = today - timedelta(days=13), today
                key = "14d"
        start = (
            timezone.make_aware(datetime.combine(date_from, time.min), tz)
            if date_from
            else None
        )
        end = (
            timezone.make_aware(datetime.combine(date_to, time.max), tz)
            if date_to
            else None
        )
        return start, end, key

    def get_queryset(self):
        cached = getattr(self, "_audit_qs", None)
        if cached is not None:
            return cached
        qs = AuditEvent.objects.select_related("user").all()
        start, end, _key = self._date_bounds()
        if start:
            qs = qs.filter(created_at__gte=start)
        if end:
            qs = qs.filter(created_at__lte=end)

        user_id = (self.request.GET.get("user") or "").strip()
        if user_id.isdigit():
            qs = qs.filter(user_id=int(user_id))

        module = (self.request.GET.get("module") or "").strip()
        if module:
            qs = qs.filter(module=module)

        action = (self.request.GET.get("action") or "").strip()
        if action:
            qs = qs.filter(action=action)

        bucket = (self.request.GET.get("kind") or "all").strip().lower()
        if bucket == "auth":
            qs = qs.filter(action__in=AUTH_ACTIONS)
        elif bucket == "create":
            qs = qs.filter(action__in=CREATE_ACTIONS)
        elif bucket == "delete":
            qs = qs.filter(action__in=DELETE_ACTIONS)
        elif bucket == "error":
            qs = qs.filter(status_code__gte=400)
        elif bucket == "update":
            qs = qs.exclude(action__in=AUTH_ACTIONS + CREATE_ACTIONS + DELETE_ACTIONS).filter(
                Q(status_code__isnull=True) | Q(status_code__lt=400)
            )

        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(username__icontains=q)
                | Q(user_display__icontains=q)
                | Q(summary__icontains=q)
                | Q(path__icontains=q)
                | Q(module__icontains=q)
                | Q(action__icontains=q)
                | Q(ip_address__icontains=q)
            )
        self._audit_qs = qs.order_by("-created_at", "-id")
        return self._audit_qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        filtered = self.get_queryset()
        start, end, range_key = self._date_bounds()
        kpis = filtered.aggregate(
            total=Count("id"),
            users=Count("user_id", distinct=True),
            creates=Count("id", filter=Q(action__in=CREATE_ACTIONS)),
            errors=Count("id", filter=Q(status_code__gte=400)),
        )
        events = list(filtered[: LIST_LIMIT + 1])
        truncated = len(events) > LIST_LIMIT
        events = events[:LIST_LIMIT]
        ctx["audit_events"] = events
        ctx["list_truncated"] = truncated
        ctx["list_limit"] = LIST_LIMIT
        ctx["kpis"] = kpis
        ctx["range_key"] = range_key
        ctx["date_from"] = start.date().isoformat() if start else ""
        ctx["date_to"] = end.date().isoformat() if end else ""
        ctx["filter_user"] = (self.request.GET.get("user") or "").strip()
        ctx["filter_module"] = (self.request.GET.get("module") or "").strip()
        ctx["filter_action"] = (self.request.GET.get("action") or "").strip()
        ctx["filter_kind"] = (self.request.GET.get("kind") or "all").strip().lower() or "all"
        ctx["search_query"] = (self.request.GET.get("q") or "").strip()
        ctx["filter_users"] = User.objects.order_by("username")
        ctx["filter_modules"] = list(
            AuditEvent.objects.exclude(module="")
            .order_by("module")
            .values_list("module", flat=True)
            .distinct()
        )
        ctx["filter_actions"] = list(
            AuditEvent.objects.exclude(action="")
            .order_by("action")
            .values_list("action", flat=True)
            .distinct()
        )
        ctx["kind_counts"] = {
            "all": kpis["total"],
            "create": kpis["creates"],
            "update": filtered.exclude(
                action__in=AUTH_ACTIONS + CREATE_ACTIONS + DELETE_ACTIONS
            )
            .filter(Q(status_code__isnull=True) | Q(status_code__lt=400))
            .count(),
            "delete": filtered.filter(action__in=DELETE_ACTIONS).count(),
            "auth": filtered.filter(action__in=AUTH_ACTIONS).count(),
            "error": kpis["errors"],
        }
        link = {}
        if range_key and range_key != "custom":
            link["range"] = range_key
        if ctx["date_from"]:
            link["date_from"] = ctx["date_from"]
        if ctx["date_to"]:
            link["date_to"] = ctx["date_to"]
        if ctx["filter_user"]:
            link["user"] = ctx["filter_user"]
        if ctx["filter_module"]:
            link["module"] = ctx["filter_module"]
        if ctx["filter_action"]:
            link["action"] = ctx["filter_action"]
        if ctx["search_query"]:
            link["q"] = ctx["search_query"]
        ctx["filter_base"] = urlencode(link)
        range_link = {k: v for k, v in link.items() if k not in {"range", "date_from", "date_to"}}
        ctx["range_base"] = urlencode(range_link)
        return ctx
