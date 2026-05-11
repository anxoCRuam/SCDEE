"""
PostgreSQL database configuration (RNF-3, RNF-13).
"""

from ._paths import env

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME", default="scdee"),
        "USER": env("DB_USER", default="scdee"),
        "PASSWORD": env("DB_PASSWORD", default="scdee"),
        "HOST": env("DB_HOST", default="db"),
        "PORT": env.int("DB_PORT", default=5432),
        # Use persistent connections to avoid TCP handshake per request.
        "CONN_MAX_AGE": env.int("DB_CONN_MAX_AGE", default=600),
        "OPTIONS": {
            # Force UTC for consistency regardless of server locale.
            "options": "-c timezone=UTC",
        },
    }
}

# Wrap every HTTP request in a transaction (RNF-13).
ATOMIC_REQUESTS = True
