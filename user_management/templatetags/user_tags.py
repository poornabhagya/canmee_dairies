from django import template
from django.apps import apps

register = template.Library()


def user_is_admin_or_superuser(user):
    """True for Django superusers or users in the Admin group."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    return user.groups.filter(name__iexact="Admin").exists()


def user_is_branch_manager(user):
    """True for Branch Manager group or employee set as a branch manager."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.groups.filter(name__iexact="Branch Manager").exists():
        return True
    emp = getattr(user, "employee_profile", None)
    if emp is None:
        return False
    Branch = apps.get_model("branches", "Branch")
    return Branch.objects.filter(branch_manager_id=emp.pk).exists()


def user_can_manage_farmer_goods_notifications(user):
    """Admin, superuser, and branch managers can accept/reject pending CP goods."""
    return user_is_admin_or_superuser(user) or user_is_branch_manager(user)


@register.filter(name="is_admin_or_superuser")
def is_admin_or_superuser(user):
    return user_is_admin_or_superuser(user)


@register.filter(name="can_manage_farmer_goods_notifications")
def can_manage_farmer_goods_notifications(user):
    return user_can_manage_farmer_goods_notifications(user)


@register.filter
def user_initials(user):
    if not user or not getattr(user, "is_authenticated", False):
        return "?"
    fn = (user.first_name or "").strip()
    ln = (user.last_name or "").strip()
    if fn and ln:
        return f"{fn[0]}{ln[0]}".upper()
    if fn:
        return (fn[:2] if len(fn) > 1 else fn[0]).upper()
    un = (user.get_username() or "?")[:2]
    return un.upper()


@register.filter
def username_display(user):
    """Show username with first letter capitalized (e.g. canmeeda → Canmeeda)."""
    if not user or not getattr(user, "is_authenticated", False):
        return ""
    name = (user.get_username() or "").strip()
    if not name:
        return ""
    return name[0].upper() + name[1:]


@register.filter
def avatar_url(user):
    if not user or not getattr(user, "is_authenticated", False):
        return ""
    UserProfile = apps.get_model("user_management", "UserProfile")
    try:
        p = user.profile
    except UserProfile.DoesNotExist:
        return ""
    if p.avatar:
        return p.avatar.url
    return ""
