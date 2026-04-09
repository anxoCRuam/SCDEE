"""
Production-specific Django settings.

Extends base.py with strict security, performance optimizations,
and no debug facilities.
"""

from .base import *  # noqa: F401, F403

# ============================================================
# Core
# ============================================================
DEBUG = False

# ALLOWED_HOSTS must be explicitly set via environment variable.
# No wildcards allowed in production.

# ============================================================
# Security — strict (RNF-6)
# ============================================================
# Force HTTPS redirect at the Django level.
SECURE_SSL_REDIRECT = True

# HTTP Strict Transport Security: tell browsers to only use HTTPS
# for the next year, including subdomains. Preload submits the domain
# to browser HSTS preload lists for maximum protection.
SECURE_HSTS_SECONDS = 31_536_000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True

# Ensure the proxy passes the correct protocol header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ============================================================
# Logging — JSON format for log aggregation
# ============================================================
LOGGING["root"]["level"] = "INFO"  # type: ignore[index]

# ============================================================
# Performance
# ============================================================
# Persistent DB connections (10 min). In production behind a
# connection pooler (PgBouncer), this can be set to None (unlimited).
DATABASES["default"]["CONN_MAX_AGE"] = 600  # type: ignore[index]
