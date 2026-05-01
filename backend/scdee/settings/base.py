"""
Django base settings shared across all environments.

Imports from specialised modules to keep the file manageable.
"""

from ._paths import *  # noqa: F401, F403 – provides BASE_DIR and env

# ============================================================
# Core Django
# ============================================================
SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

# ============================================================
# Applications
# ============================================================
DJANGO_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "corsheaders",
    "drf_spectacular",
    "django_celery_beat",
    "django_filters",
    "storages",
]

LOCAL_APPS = [
    "apps.core",
    "apps.audit",
    "apps.organizations",
    "apps.accounts",
    "apps.courses",
    "apps.subjects",
    "apps.exams",
    "apps.instances",
    "apps.grading",
    "apps.annotations",
    "apps.ingestion",
    "apps.reviews",
    "apps.notifications",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# ============================================================
# Middleware
# ============================================================
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    # CSRF deliberately omitted: this is a stateless JWT API.
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "apps.core.middleware.ClearTenantContextMiddleware",
    "apps.core.middleware.AntiCacheMiddleware",
]

ROOT_URLCONF = "scdee.urls"
WSGI_APPLICATION = "scdee.wsgi.application"
ASGI_APPLICATION = "scdee.asgi.application"

# No Django templates needed — this is a pure API backend.
TEMPLATES = []

# ============================================================
# Custom User model (RF-2, RNF-13)
# ============================================================
AUTH_USER_MODEL = "accounts.User"

# Default PK type: UUID for all models that don't override it.
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ============================================================
# Static files (only for Swagger UI / ReDoc)
# ============================================================
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# ============================================================
# Password hashing (RF-16.4)
# ============================================================
# Argon2 is the winner of the Password Hashing Competition (2015).
# It's memory-hard, making GPU/ASIC brute-force attacks expensive.
# bcrypt is kept as fallback for password verification during migration.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
]

# ── Import everything from specialised modules ──────────────
from .cache import *  # noqa: E402, F403
from .celery import *  # noqa: E402, F403
from .database import *  # noqa: E402, F403
from .email import *  # noqa: E402, F403
from .ingestion import *  # noqa: E402, F403
from .internationalization import *  # noqa: E402, F403
from .logging import *  # noqa: E402, F403
from .rest_framework import *  # noqa: E402, F403
from .security import *  # noqa: E402, F403
from .spectacular import *  # noqa: E402, F403
from .storage import *  # noqa: E402, F403
