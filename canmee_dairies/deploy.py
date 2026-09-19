import os
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.http import HttpResponse

_DEPLOY_ON = frozenset({"1", "true", "yes", "on", "enabled"})
_DEPLOY_OFF = frozenset({"0", "false", "no", "off", "disabled"})


def deploy_disabled_flag_path():
    return Path(settings.BASE_DIR) / "deploy.disabled"


def deploy_is_enabled():
    """Read env on each request so disable takes effect after app restart."""
    if deploy_disabled_flag_path().exists():
        return False
    raw = os.getenv("DJANGO_DEPLOY_ENABLED", "").strip().lower()
    if not raw or raw in _DEPLOY_OFF:
        return False
    return raw in _DEPLOY_ON


def deploy_key():
    return (os.getenv("DJANGO_DEPLOY_KEY") or "").strip()


def deploy_disabled_reason():
    if deploy_disabled_flag_path().exists():
        return "Deploy endpoint is disabled (deploy.disabled file exists in project root)."
    raw = os.getenv("DJANGO_DEPLOY_ENABLED")
    if raw is None or not str(raw).strip():
        return "Deploy endpoint is disabled (DJANGO_DEPLOY_ENABLED is not set)."
    normalized = str(raw).strip().lower()
    if normalized in _DEPLOY_OFF:
        return f"Deploy endpoint is disabled (DJANGO_DEPLOY_ENABLED={raw!r})."
    if normalized not in _DEPLOY_ON:
        return f"Deploy endpoint is disabled (DJANGO_DEPLOY_ENABLED={raw!r} is not recognized)."
    if not deploy_key():
        return "Deploy endpoint is misconfigured (DJANGO_DEPLOY_KEY is not set)."
    return "Deploy endpoint is disabled."


def deploy(request):
    """
    Run migrate + collectstatic via browser when SSH is unavailable.

    Enable (hosting panel → restart app):
      DJANGO_DEPLOY_ENABLED=1
      DJANGO_DEPLOY_KEY=your-long-random-secret

    Disable (any one of these):
      - DJANGO_DEPLOY_ENABLED=0  (or remove the variable) → restart app
      - Create empty file: deploy.disabled in project root (no restart needed)

    Visit: /deploy/?key=YOUR_KEY
    """
    if not deploy_is_enabled():
        return HttpResponse(deploy_disabled_reason(), status=403)

    expected = deploy_key()
    if request.GET.get("key") != expected:
        return HttpResponse("Forbidden", status=403)

    output = []
    for label, command, kwargs in (
        ("Migration", "migrate", {}),
        ("Collectstatic", "collectstatic", {"interactive": False}),
    ):
        try:
            call_command(command, **kwargs)
            output.append(f"{label} completed.")
        except Exception as exc:
            output.append(f"{label} error: {exc}")

    return HttpResponse("<br>".join(output))
