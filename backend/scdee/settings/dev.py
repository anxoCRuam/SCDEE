"""
Development-specific Django settings.

Extends base.py with relaxed security, verbose logging,
and tools optimized for local development.
"""

from .base import *  # noqa: F401, F403

# ============================================================
# Debug mode
# ============================================================
DEBUG = True
ALLOWED_HOSTS = ["*"]

# ============================================================
# Logging — human-readable in dev
# ============================================================
# Override JSON formatter with simple text for easier reading in terminal.
LOGGING["handlers"]["console"]["formatter"] = "simple"  # type: ignore[index]
LOGGING["root"]["level"] = "DEBUG"  # type: ignore[index]

# Show SQL queries in console (uncomment if needed)
# LOGGING["loggers"]["django.db.backends"]["level"] = "DEBUG"

# ============================================================
# CORS — allow all origins in development
# ============================================================
CORS_ALLOW_ALL_ORIGINS = True

# ============================================================
# Email — use Mailpit (captures without sending)
# ============================================================
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
# Mailpit SMTP runs on port 1025, web UI on 8025.

# ============================================================
# Security — relaxed for local dev
# ============================================================
# These are strict in production (see prod.py).
SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False

# ============================================================
# DRF — add browsable API for convenience
# ============================================================
REST_FRAMEWORK["DEFAULT_RENDERER_CLASSES"] = [  # type: ignore[index]
    "rest_framework.renderers.JSONRenderer",
    "rest_framework.renderers.BrowsableAPIRenderer",
]
