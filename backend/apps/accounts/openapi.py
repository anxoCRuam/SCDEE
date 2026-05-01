"""
Reusable OpenAPI examples and schemas for the ``accounts`` app.

Lives outside the views to keep view modules focused on logic. Imported
on demand from each view's ``@extend_schema`` decorator.

References: RF-1.x, RF-2.x.
"""

from __future__ import annotations

from drf_spectacular.utils import OpenApiExample

# ════════════════════════════════════════════════════════════════════
# Authentication examples (RF-1.2 / RF-1.3 / RF-1.4)
# ════════════════════════════════════════════════════════════════════


LOGIN_REQUEST_EXAMPLE = OpenApiExample(
    name="LoginRequest",
    summary="Standard login",
    description="Email + password from the institutional account.",
    value={
        "email": "professor@uam.es",
        "password": "S3cretP@ssw0rd!",
    },
    request_only=True,
)

LOGIN_RESPONSE_EXAMPLE = OpenApiExample(
    name="LoginSuccess",
    summary="Token pair + user metadata",
    description=(
        "On success, the response carries the JWT pair plus the basic "
        "user fields the frontend needs to skip an extra ``GET /profile/`` "
        "call. The ``user.organization_id`` is what ties subsequent "
        "requests to a tenant."
    ),
    value={
        "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
        "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
        "user": {
            "id": "5e1f8a3a-9b7d-4e1a-9c1d-1234567890ab",
            "email": "professor@uam.es",
            "first_name": "María",
            "last_name": "García",
            "is_staff": False,
            "is_superadmin": False,
            "organization_id": "11111111-2222-3333-4444-555555555555",
        },
    },
    response_only=True,
    status_codes=["200"],
)


REFRESH_REQUEST_EXAMPLE = OpenApiExample(
    name="RefreshRequest",
    summary="Token refresh",
    value={
        "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    },
    request_only=True,
)

REFRESH_RESPONSE_EXAMPLE = OpenApiExample(
    name="RefreshSuccess",
    summary="Rotated token pair",
    description=(
        "The previous refresh token is revoked (added to the Redis "
        "blacklist) and replaced with the one returned here. The "
        "frontend MUST discard the old refresh token immediately."
    ),
    value={
        "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
        "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
        # ``user`` is intentionally not included on refresh — the
        # frontend already has it from the original login.
        "user": None,
    },
    response_only=True,
    status_codes=["200"],
)


LOGOUT_REQUEST_EXAMPLE = OpenApiExample(
    name="LogoutRequest",
    summary="Revoke a refresh token",
    description=(
        "Idempotent — sending an already-revoked or expired token "
        "still returns 204. The access token is not invalidated by "
        "this call (it expires naturally within minutes)."
    ),
    value={
        "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    },
    request_only=True,
)
