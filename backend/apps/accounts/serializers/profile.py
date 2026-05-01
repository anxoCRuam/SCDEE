"""
Serializers for the user's own profile endpoints.

The profile is a singleton resource: it always refers to the
currently authenticated user. No ID is needed in the URL.

Output differences from UserResponseSerializer:
    - Includes decrypted DNI (user can see their own, RF-2.10).
    - Includes email_notifications_enabled preference.
    - Will include subject memberships when Fase 3 is implemented.

Input is restricted: users can only change their email (RF-2.9)
and notification preferences (RF-2.11). Name, NIA, is_staff, etc.
are managed by the organization manager, not the user themselves.

References: RF-2.9, RF-2.10, RF-2.11
"""

from rest_framework import serializers


class ProfileResponseSerializer(serializers.Serializer):
    """Output for GET /api/v1/profile/ (RF-2.10).

    Includes all personal data visible to the user, including
    their own decrypted DNI. Subject memberships will be added
    in Fase 3 when SubjectMembership is implemented.
    """

    id = serializers.UUIDField(read_only=True)
    email = serializers.EmailField(read_only=True)
    first_name = serializers.CharField(read_only=True)
    last_name = serializers.CharField(read_only=True)
    nia = serializers.CharField(read_only=True)
    dni = serializers.CharField(
        read_only=True,
        help_text="Decrypted DNI. Only visible to the user themselves and managers.",
    )
    is_active = serializers.BooleanField(read_only=True)
    is_staff = serializers.BooleanField(read_only=True)
    is_superadmin = serializers.BooleanField(read_only=True)
    organization_id = serializers.UUIDField(read_only=True)
    email_notifications_enabled = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    # Fase 3: subject_memberships will be added here as a nested list.


class UpdateProfileSerializer(serializers.Serializer):
    """Input for PATCH /api/v1/profile/ (RF-2.9, RF-2.11).

    Users can only modify:
    - email: their own email address (RF-2.9)
    - email_notifications_enabled: notification preference (RF-2.11)

    All other fields (name, NIA, is_staff, etc.) are managed by
    the organization manager via PATCH /users/{id}/.
    """

    email = serializers.EmailField(
        required=False,
        help_text="New email address. Must be unique within the organization.",
    )
    email_notifications_enabled = serializers.BooleanField(
        required=False,
        help_text="Toggle email notification delivery (RF-2.11).",
    )
