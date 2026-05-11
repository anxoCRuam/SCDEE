"""
Serializers for authentication endpoints.

These serializers handle input validation only. The actual authentication
logic lives in the auth plugin (JWTAuthPlugin). Serializers never touch
the database or generate tokens directly.

Separation rationale:
    - Serializers validate shape and type of input data.
    - The plugin handles business rules (credential check, blacklist).
    - Views orchestrate the flow and call audit logging.

Reusable OpenAPI examples and schemas for the ``accounts`` app.

References: RF-1.2, RF-1.3, RF-1.4
"""

from __future__ import annotations

from drf_spectacular.utils import OpenApiExample
from rest_framework import serializers

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


class LoginRequestSerializer(serializers.Serializer):
    """Input for POST /auth/login/.

    Validates that email and password are present and non-empty.
    Does NOT verify credentials — that's the plugin's job.
    """

    email = serializers.EmailField(
        required=True,
        help_text="User's email address (login identifier).",
    )
    password = serializers.CharField(
        required=True,
        write_only=True,
        trim_whitespace=False,
        help_text="User's password. Sent only in the request, never in responses.",
    )


class TokenPairResponseSerializer(serializers.Serializer):
    """Output shape for login and refresh responses.

    Used by drf-spectacular to document the response schema.
    These fields are read-only and never used for input validation.
    """

    access_token = serializers.CharField(read_only=True)
    refresh_token = serializers.CharField(read_only=True)
    user = serializers.SerializerMethodField(read_only=True)

    def get_user(self, obj: dict) -> dict | None:
        """Include basic user data in the login response.

        This saves the frontend an extra GET /profile/ request
        after login. Only included if user data is in the context.
        """
        user = obj.get("user")
        if user is None:
            return None
        return {
            "id": str(user.pk),
            "email": user.email,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "organization_id": str(user.organization_id) if user.organization_id else None,
        }


class RefreshRequestSerializer(serializers.Serializer):
    """Input for POST /auth/refresh/.

    The client sends the current refresh token to get a new pair.
    """

    refresh_token = serializers.CharField(
        required=True,
        trim_whitespace=False,
        help_text="Current valid refresh token.",
    )


class LogoutRequestSerializer(serializers.Serializer):
    """Input for POST /auth/logout/.

    The client sends the refresh token to invalidate.
    The access token will expire naturally (short-lived).
    """

    refresh_token = serializers.CharField(
        required=True,
        trim_whitespace=False,
        help_text="Refresh token to revoke.",
    )
