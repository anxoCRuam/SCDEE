"""
Serializers for permissions.

References: RF-5
"""

from rest_framework import serializers

from apps.subjects.models.permissions import ALL_PERMISSION_KEYS

# ── Permission serializers ───────────────────────────────────


class UpdatePermissionsSerializer(serializers.Serializer):
    """Input for PATCH /api/v1/memberships/{id}/permissions/ (RF-5.3, RF-5.4).

    Values: true (grant), false (revoke), null (restore default).
    Only recognized permission keys are accepted.
    """

    permissions = serializers.DictField(
        child=serializers.BooleanField(allow_null=True),
        required=True,
        help_text="Dict of permission_name → bool|null.",
    )

    def validate_permissions(self, value: dict) -> dict:
        """Reject unknown permission keys."""
        unknown = set(value.keys()) - set(ALL_PERMISSION_KEYS)
        if unknown:
            raise serializers.ValidationError(f"Unknown permissions: {', '.join(sorted(unknown))}")
        return value


class EffectivePermissionsSerializer(serializers.Serializer):
    """Output for GET /api/v1/memberships/{id}/effective-permissions/ (RF-5.5)."""

    permissions = serializers.DictField(
        child=serializers.BooleanField(),
        read_only=True,
    )
    role = serializers.CharField(read_only=True)
    has_overrides = serializers.BooleanField(read_only=True)
    is_staff_bypass = serializers.BooleanField(
        read_only=True,
        help_text="True if the user has manager bypass (all permissions via is_staff).",
    )
