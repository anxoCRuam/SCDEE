"""
Security utility: rate limiting.

- Rate limiting configured via DRF throttling (RNF-6)

References: RNF-6
"""

from __future__ import annotations

import logging

from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

logger = logging.getLogger(__name__)


# ── Rate limiting (RNF-6) ────────────────────────────────────
# Uses DRF's built-in throttling, configured in settings.
# Custom throttle classes for different user types.


class LoginRateThrottle(AnonRateThrottle):
    """Strict rate limit for login attempts (brute force protection)."""

    scope = "login"


class StandardUserThrottle(UserRateThrottle):
    """Standard rate limit for authenticated users."""

    scope = "user"


class ManagerThrottle(UserRateThrottle):
    """Higher rate limit for managers (bulk operations)."""

    scope = "manager"
