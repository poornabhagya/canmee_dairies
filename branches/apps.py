from django.apps import AppConfig


class BranchesConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "branches"

    def ready(self):
        from . import signals  # noqa: F401
