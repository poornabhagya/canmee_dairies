from django.apps import AppConfig


class UserManagementConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "user_management"
    verbose_name = "User management"

    def ready(self):
        from . import signals  # noqa: F401
