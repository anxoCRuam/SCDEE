"""
Serializers for authentication endpoints.

These serializers handle input validation only. The actual authentication
logic lives in the auth plugin (JWTAuthPlugin). Serializers never touch
the database or generate tokens directly.

Separation rationale:
    - Serializers validate shape and type of input data.
    - The plugin handles business rules (credential check, blacklist).
    - Views orchestrate the flow and call audit logging.

References: RF-1.2, RF-1.3, RF-1.4
"""

from rest_framework import serializers


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
            "is_staff": user.is_staff,
            "is_superadmin": user.is_superadmin,
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
