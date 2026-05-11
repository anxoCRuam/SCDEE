"""
Serializers for organization management.

The creation endpoint accepts org data + initial manager data in
a single request, creating both in one transaction. This ensures
we never have an organization without a manager.

References: RF-2.1
"""

from rest_framework import serializers

from apps.organizations.models.organization import PlanChoices


class InitialManagerSerializer(serializers.Serializer):
    """Data for the first manager created with the organization."""

    email = serializers.EmailField(required=True)
    first_name = serializers.CharField(required=True, max_length=150)
    last_name = serializers.CharField(required=True, max_length=150)
    dni = serializers.CharField(required=False, default="", max_length=20)
    nia = serializers.CharField(required=False, default="", max_length=50)


class CreateOrganizationSerializer(serializers.Serializer):
    """Input for POST /api/v1/organizations/ (RF-2.1).

    Creates an organization and its initial manager in one request.
    Only accessible by superadmins.
    """

    name = serializers.CharField(
        required=True,
        max_length=255,
        help_text="Organization display name (e.g. 'Universidad Autónoma de Madrid').",
    )
    subdomain = serializers.SlugField(
        required=True,
        max_length=63,
        help_text="Unique identifier for URL routing (e.g. 'uam').",
    )
    plan = serializers.ChoiceField(
        choices=PlanChoices.choices,
        default=PlanChoices.FREE,
        help_text="Subscription plan (FREE or PREMIUM).",
    )
    initial_manager = InitialManagerSerializer(
        required=True,
        help_text="Data for the organization's first manager.",
    )


class OrganizationResponseSerializer(serializers.Serializer):
    """Output shape for organization responses."""

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    subdomain = serializers.CharField(read_only=True)
    plan = serializers.CharField(read_only=True)
    auth_mode = serializers.CharField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
