"""
Security headers, CORS, JWT, and encryption (RNF-6, RF-1, RF-16.3).
"""

from ._paths import env

# ============================================================
# Security headers
# ============================================================
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_BROWSER_XSS_FILTER = False

# ============================================================
# CORS (RNF-6)
# ============================================================
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=["http://localhost:3000"],
)
CORS_ALLOW_CREDENTIALS = False
CORS_ALLOW_HEADERS = [
    "accept",
    "accept-encoding",
    "authorization",
    "content-type",
    "origin",
    "x-requested-with",
]

# ============================================================
# Encryption (RF-16.3)
# ============================================================
ENCRYPTION_MASTER_KEY = env("ENCRYPTION_MASTER_KEY")

# ============================================================
# JWT Configuration (RF-1.2, RF-1.3)
# ============================================================
JWT_ACCESS_TOKEN_LIFETIME_MINUTES = env.int("JWT_ACCESS_TOKEN_LIFETIME_MINUTES", default=15)
JWT_REFRESH_TOKEN_LIFETIME_DAYS = env.int("JWT_REFRESH_TOKEN_LIFETIME_DAYS", default=7)
JWT_ALGORITHM = "HS256"

# Active authentication plugin (Strategy pattern, RF-1.1).
AUTH_PLUGIN_CLASS = "apps.accounts.plugins.jwtV1.jwt_plugin.JWTAuthPlugin"
