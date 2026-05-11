"""
Serializers for user management endpoints.

Separated into input (write) and output (read) serializers:
- CreateUserSerializer: validates input for POST /users/
- UpdateUserSerializer: validates input for PATCH /users/{id}/
- UserResponseSerializer: shapes output for all user endpoints
- ResetPasswordResponseSerializer: confirms password reset

The DNI is NEVER included in the output serializer by default.
It's only returned when the view explicitly adds it (profile
endpoint or export). This prevents accidental exposure of
sensitive data (RF-16.3).

References: RF-2.2, RF-2.4, RF-2.5, RF-2.6
"""

from rest_framework import serializers


class CreateUserSerializer(serializers.Serializer):
    """Input for POST /api/v1/users/ (RF-2.2).

    All fields are validated here. The view delegates to
    user_service.create_user() for business logic.
    """

    email = serializers.EmailField(
        required=True,
        help_text="User's email address (unique per organization).",
    )
    first_name = serializers.CharField(
        required=True,
        max_length=150,
        help_text="User's given name.",
    )
    last_name = serializers.CharField(
        required=True,
        max_length=150,
        help_text="User's family name(s).",
    )
    dni = serializers.CharField(
        required=False,
        default="",
        max_length=20,
        help_text="National ID document. Will be encrypted (AES-256-GCM).",
    )
    nia = serializers.CharField(
        required=False,
        default="",
        max_length=50,
        help_text="Institutional identifier (unique per organization if provided).",
    )
    is_staff = serializers.BooleanField(
        required=False,
        default=False,
        help_text="Whether the user is an organization manager.",
    )


class UpdateUserSerializer(serializers.Serializer):
    """Input for PATCH /api/v1/users/{id}/ (RF-2.4, RF-2.5, RF-2.6).

    All fields are optional (PATCH semantics). Only provided fields
    are updated. The view detects which fields changed for auditing.
    """

    first_name = serializers.CharField(
        required=False,
        max_length=150,
    )
    last_name = serializers.CharField(
        required=False,
        max_length=150,
    )
    email = serializers.EmailField(
        required=False,
    )
    nia = serializers.CharField(
        required=False,
        max_length=50,
        allow_blank=True,
    )
    dni = serializers.CharField(
        required=False,
        max_length=20,
        allow_blank=True,
        help_text="New DNI value. Will be re-encrypted.",
    )
    is_staff = serializers.BooleanField(
        required=False,
        help_text="Promote/demote to organization manager (RF-2.6).",
    )
    is_active = serializers.BooleanField(
        required=False,
        help_text="Set to false to deactivate (soft delete, RF-2.5).",
    )


class UserResponseSerializer(serializers.Serializer):
    """Output shape for user data in all responses.

    The DNI field is NEVER included here. When needed (own profile,
    export), the view adds it explicitly to the response data.
    This prevents accidental exposure.
    """

    id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)
    first_name = serializers.CharField(read_only=True)
    last_name = serializers.CharField(read_only=True)
    nia = serializers.CharField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    is_staff = serializers.BooleanField(read_only=True)
    organization_id = serializers.UUIDField(read_only=True)
    email_notifications_enabled = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)
