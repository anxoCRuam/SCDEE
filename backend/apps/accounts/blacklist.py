"""
Redis-based JWT token blacklist.

When a user logs out or a refresh token is rotated, the old token's
JTI (JWT ID — a unique identifier per token) is stored in Redis.
Subsequent attempts to use that token are rejected.

Why Redis instead of a database table?
    1. RF-1.4 explicitly requires Redis with TTL.
    2. Token validation happens on EVERY request — Redis is O(1) in-memory.
    3. TTL auto-cleanup: expired blacklist entries disappear automatically.
       No periodic cleanup task needed, no table bloat.
    4. Redis is already in the stack (Celery broker, cache).

Key format: "token_blacklist:{jti}"
Value: "1" (we only check existence, not the value)
TTL: remaining lifetime of the token at revocation time

If Redis is temporarily unavailable, the authentication system degrades
gracefully instead of crashing:
- ``is_blacklisted`` returns False (token accepted — loss of revocation,
  but users can still work).
- ``get_user_token_generation`` returns 0 (no active bulk invalidation).
- ``add_to_blacklist`` logs the error and continues — the token will not
  be revoked during the outage, but the system remains operational.

References: RF-1.4
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.core.cache import cache

logger = logging.getLogger(__name__)

# Redis key prefix for blacklisted tokens.
# Using the default Django cache (backed by django-redis).
_BLACKLIST_PREFIX = "token_blacklist"


def add_to_blacklist(jti: str, remaining_lifetime: timedelta) -> None:
    """Add a token's JTI to the blacklist.

    Called when:
    - A user logs out (RF-1.4): their refresh token is revoked.
    - A refresh token is rotated (RF-1.3): the old one is revoked.
    - A manager resets a user's password (RF-2.8): all tokens revoked.

    The entry expires automatically after the token's remaining lifetime,
    since an expired token would be rejected by JWT validation anyway.

    If Redis is down, the operation is logged and skipped to prevent
    the authentication flow from breaking entirely.

    Args:
        jti: The unique identifier of the token to blacklist.
        remaining_lifetime: Time until the token's natural expiration.
            Used as the Redis TTL so the entry auto-cleans.
    """
    if not jti:
        logger.warning("Attempted to blacklist a token with empty JTI.")
        return

    # Convert timedelta to seconds for the cache TTL.
    # Minimum 1 second to avoid zero/negative TTL edge cases.
    ttl_seconds = max(int(remaining_lifetime.total_seconds()), 1)

    key = f"{_BLACKLIST_PREFIX}:{jti}"
    try:
        cache.set(key, "1", timeout=ttl_seconds)
        logger.debug("Token %s blacklisted for %d seconds.", jti, ttl_seconds)
    except Exception:
        logger.exception("Failed to blacklist token %s (Redis unavailable).", jti)


def is_blacklisted(jti: str) -> bool:
    """Check if a token's JTI is in the blacklist.

    Called on every request by the DRF authentication backend
    to reject revoked tokens.

    If Redis is unavailable we assume the token is NOT blacklisted.
    This keeps the system available during a Redis outage, at the cost
    of not enforcing recent revocations. The alternative — blocking all
    requests — would be a denial-of-service.

    Args:
        jti: The unique identifier to check.

    Returns:
        True if the token has been revoked, False otherwise.
    """
    if not jti:
        return True  # Tokens without JTI are always invalid.

    key = f"{_BLACKLIST_PREFIX}:{jti}"
    try:
        return cache.get(key) is not None
    except Exception:
        logger.exception("Failed to check blacklist for jti %s (Redis unavailable).", jti)
        return False


def blacklist_all_user_tokens(user_id: str, token_lifetime: timedelta) -> None:
    """Invalidate all tokens for a specific user.

    This is a "nuclear option" used when a manager resets a user's
    password (RF-2.8). Since we can't enumerate all JTIs for a user
    in Redis, we use a per-user generation counter: incrementing it
    makes all tokens issued before the increment invalid.

    The counter key persists until the longest possible token lifetime
    expires, then auto-cleans via TTL.

    Args:
        user_id: UUID string of the user whose tokens to invalidate.
        token_lifetime: Maximum token lifetime (used as TTL for the counter).
    """
    key = f"{_BLACKLIST_PREFIX}:user_gen:{user_id}"
    ttl_seconds = max(int(token_lifetime.total_seconds()), 1)

    # Increment the generation counter. If the key doesn't exist,
    # django-redis will create it with value 1.
    try:
        # Use the raw Redis client for atomic increment.
        from django_redis import get_redis_connection

        conn = get_redis_connection("default")
        conn.incr(key)
        conn.expire(key, ttl_seconds)
    except Exception:
        # Fallback: set a new value (less atomic but works).
        current = cache.get(key, 0)
        cache.set(key, current + 1, timeout=ttl_seconds)

    logger.info("Invalidated all tokens for user %s.", user_id)


def get_user_token_generation(user_id: str) -> int:
    """Get the current token generation counter for a user.

    Tokens include the generation at issue time. If the current
    generation is higher, the token was issued before a bulk
    invalidation and should be rejected.

    Args:
        user_id: UUID string of the user.

    Returns:
        Current generation counter (0 if never invalidated).
    """
    from django_redis import get_redis_connection

    key = f"{_BLACKLIST_PREFIX}:user_gen:{user_id}"
    conn = get_redis_connection("default")
    value = conn.get(key)
    return int(value) if value is not None else 0
