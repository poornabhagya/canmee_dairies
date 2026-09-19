"""Inline static text assets into templates (shared-hosting /static 503 workaround)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from django import template
from django.conf import settings
from django.utils.safestring import mark_safe

register = template.Library()


def _candidate_paths(relative_path: str) -> list[Path]:
    rel = relative_path.lstrip("/\\")
    paths: list[Path] = []
    for directory in getattr(settings, "STATICFILES_DIRS", []) or []:
        paths.append(Path(directory) / rel)
    static_root = getattr(settings, "STATIC_ROOT", None)
    if static_root:
        paths.append(Path(static_root) / rel)
    return paths


@lru_cache(maxsize=32)
def _read_static_text_cached(relative_path: str) -> str:
    return _read_static_text_uncached(relative_path)


def _read_static_text_uncached(relative_path: str) -> str:
    for path in _candidate_paths(relative_path):
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return f"/* missing static file: {relative_path} */\n"


@register.simple_tag
def inline_static(relative_path: str):
    """Return file contents marked safe for use inside <style> / <script>."""
    if settings.DEBUG:
        content = _read_static_text_uncached(relative_path)
    else:
        content = _read_static_text_cached(relative_path)
    # Prevent early </script> termination if a JS file ever contains that sequence.
    if relative_path.endswith(".js"):
        content = content.replace("</script>", "<\\/script>")
    return mark_safe(content)
