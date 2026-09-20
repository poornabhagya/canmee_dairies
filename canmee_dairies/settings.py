"""
Django settings for canmee_dairies project.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/5.2/ref/settings/
"""

from pathlib import Path
import os
import sys

# Determine the project path and the parent folder name
PROJECT_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENT_FOLDER_NAME = os.path.basename(os.path.dirname(PROJECT_PATH))

if "runserver" in sys.argv:
    try:
        from .import_windows_modules import *  # noqa: F401,F403
    except ImportError as e:
        print(f"Warning: Could not import Windows modules: {e}")
        print("Continuing without Windows-specific features...")

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "django-insecure-t)*4(2&z8b0sj6jjfhxddowri3zhx(xt4x-*@bnuj)-32@4j1g"

# Browser deploy helper (/deploy/) — see canmee_dairies/deploy.py
# Enable:  DJANGO_DEPLOY_ENABLED=1  +  DJANGO_DEPLOY_KEY=secret  → restart app
# Disable: DJANGO_DEPLOY_ENABLED=0  (or remove) → restart app
#          OR create empty file deploy.disabled in project root (works without restart)
# https://admin.canmeedairies.lk/deploy/?key=CanmeeDeploy2026-xK9mP2vL8qR4nW7)

# Check if the parent folder name ends with "dev" or "prod"
if PARENT_FOLDER_NAME.endswith("prod"):
    DEBUG = False
elif PARENT_FOLDER_NAME.endswith("dev"):
    DEBUG = True
else:
    # Default to DEBUG mode if the parent folder name does not end with "dev" or "prod"
    DEBUG = True

# SECURITY WARNING: don't run with debug turned on in production!
if DEBUG:
    ALLOWED_HOSTS = ["*"]
else:
    default_allowed_hosts = [
        "127.0.0.1",
        "localhost",
        "canmeedairies.lk",
        "www.canmeedairies.lk",
        "admin.canmeedairies.lk",
        "www.admin.canmeedairies.lk",
    ]
    env_allowed_hosts = os.getenv("DJANGO_ALLOWED_HOSTS", "")
    if env_allowed_hosts.strip():
        ALLOWED_HOSTS = [host.strip() for host in env_allowed_hosts.split(",") if host.strip()]
    else:
        ALLOWED_HOSTS = default_allowed_hosts

CSRF_TRUSTED_ORIGINS = [
    "http://127.0.0.1:8000",
    "http://localhost:8000",
    "http://127.0.0.1",
    "http://localhost",
    "https://127.0.0.1:8000",
    "https://localhost:8000",
    "https://*.loca.lt",
    "https://*.ngrok.io",
    "https://canmeedairies.lk",
    "https://www.canmeedairies.lk",
    "https://admin.canmeedairies.lk",
]

INSTALLED_APPS = [
    "canmee_dairies.apps.CanmeeDairiesConfig",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "django_filters",
    "widget_tweaks",
    "authentication",
    "user_management",
    "masters",
    "milk_collections",
    "dispatch",
    "reports",
    "hrm",
    "attendance",
    "branches",
    "stock_management",
    "suppliers",
    "farmer_loans",
    "collection_point_loans",
    "assets",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serves /static/ in production without relying on a web-server Alias.
    # Requires: pip install whitenoise  (see requirements.txt)
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "user_management.middleware.AuditLogMiddleware",
    "attendance.middleware.AttendancePortalMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "canmee_dairies.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [os.path.join(BASE_DIR, "templates")],
        "APP_DIRS": True,
        "OPTIONS": {
            "builtins": [
                "canmee_dairies.templatetags.format_extras",
            ],
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "canmee_dairies.context_processors.milk_conversion",
                "canmee_dairies.context_processors.dispatch_notifications",
                "canmee_dairies.context_processors.payment_sheet_nav",
                "canmee_dairies.context_processors.asset_module_nav",
                "attendance.context_processors.attendance_nav",
            ],
        },
    },
]

WSGI_APPLICATION = "canmee_dairies.wsgi.application"
ASGI_APPLICATION = "canmee_dairies.asgi.application"

## Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.mysql",
        "NAME": os.getenv("DB_NAME", "canmee_dairies"),
        "USER": os.getenv("DB_USER", "root"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": os.getenv("DB_PORT", "3306"),
        "OPTIONS": {
            "charset": "utf8mb4",
        },
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Colombo"
USE_I18N = True
USE_TZ = True

DATE_INPUT_FORMATS = ["%d-%m-%Y", "%d/%m/%Y"]

if DEBUG:
    FORCE_SCRIPT_NAME = None
    STATIC_URL = "/static/"
    MEDIA_URL = "/media/"
else:
    # Shared-host deployments may run either at domain root or behind a URL prefix.
    # Use DJANGO_FORCE_SCRIPT_NAME only when a prefix is explicitly configured.
    FORCE_SCRIPT_NAME = os.getenv("DJANGO_FORCE_SCRIPT_NAME") or None
    if FORCE_SCRIPT_NAME:
        _url_prefix = FORCE_SCRIPT_NAME.rstrip("/")
        STATIC_URL = f"{_url_prefix}/static/"
        MEDIA_URL = f"{_url_prefix}/media/"
    else:
        STATIC_URL = "/static/"
        MEDIA_URL = "/media/"

STATIC_ROOT = os.path.join(BASE_DIR, "collected_static/")
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, "static"),
]
# CompressedStaticFilesStorage avoids hashed filenames (keeps {% static %} URLs stable).
# Gzip matters on this host: uncompressed ~50KB /static files often get LiteSpeed 503s.
STATICFILES_STORAGE = "whitenoise.storage.CompressedStaticFilesStorage"
WHITENOISE_MAX_AGE = 31536000 if not DEBUG else 0

MEDIA_ROOT = os.path.join(BASE_DIR, "media/")

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "attendance.backends.EmployeeNICBackend",
]

APPEND_SLASH = True


def _load_key_value_file(filename):
    """Read KEY=value lines from a file in the project root (e.g. telegram.env)."""
    path = os.path.join(BASE_DIR, filename)
    if not os.path.isfile(path):
        return {}
    values = {}
    with open(path, encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


_telegram_file = _load_key_value_file("telegram.env")

# Override via environment or telegram.env in production. Dev falls back to these defaults.
_TELEGRAM_BOT_TOKEN_DEFAULT = "8145567341:AAEbc7rr_y9m5sLkWz2W9j_Hz0hXnkE_oWg"
_TELEGRAM_NOTIFICATION_GROUP_ID_DEFAULT = "-1004352811541"


def _telegram_setting(name, default=""):
    return os.getenv(name) or _telegram_file.get(name) or default


TELEGRAM_BOT_TOKEN = _telegram_setting("TELEGRAM_BOT_TOKEN", _TELEGRAM_BOT_TOKEN_DEFAULT)
TELEGRAM_NOTIFICATION_GROUP_ID = _telegram_setting(
    "TELEGRAM_NOTIFICATION_GROUP_ID",
    _TELEGRAM_NOTIFICATION_GROUP_ID_DEFAULT,
)
# Shared hosts often lack a full CA bundle; default off unless explicitly enabled.
TELEGRAM_SSL_VERIFY = os.getenv("TELEGRAM_SSL_VERIFY", "false").lower() not in (
    "0",
    "false",
    "no",
)

# New-dispatch Telegram alerts: on in prod (_prod), off in dev (_dev). Override via env if needed.
_telegram_dispatch_env = os.getenv("TELEGRAM_DISPATCH_NOTIFICATIONS_ENABLED", "").strip().lower()
if _telegram_dispatch_env in ("1", "true", "yes"):
    TELEGRAM_DISPATCH_NOTIFICATIONS_ENABLED = True
elif _telegram_dispatch_env in ("0", "false", "no"):
    TELEGRAM_DISPATCH_NOTIFICATIONS_ENABLED = False
else:
    TELEGRAM_DISPATCH_NOTIFICATIONS_ENABLED = PARENT_FOLDER_NAME.endswith("prod")

# CEFT / SLIPS bank payment upload (collection point payment sheet).
CEFT_DEBIT_ACCOUNT_NO = os.getenv("CEFT_DEBIT_ACCOUNT_NO", "230020113992")