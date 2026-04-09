"""
Organization model — top-level multi-tenant container.

Every piece of business data in the system belongs to exactly one
Organization. The Organization is the tenant boundary: users from
one organization can never see data from another.

An Organization represents:
    - A university or institute (in self-hosted mode).
    - A customer (in future SaaS mode).

Defined in Fase 0 because the User model has a FK to Organization,
and AUTH_USER_MODEL must be set before the first migration.
Full CRUD endpoints are implemented in Fase 1.

References: RF-2.1, RNF-13
"""

import uuid

from django.db import models


class PlanChoices(models.TextChoices):
    """Subscription plan for the organization."""

    FREE = "FREE", "Free"
    PREMIUM = "PREMIUM", "Premium"


class AuthModeChoices(models.TextChoices):
    """Authentication mode for the organization.

    JWT is the only mode in v1.0. SSO modes are defined here
    for schema preparation (RF-1.5) but not implemented.
    """

    JWT = "JWT", "JWT Credentials"
    # SAML = "SAML", "SAML 2.0 SSO"
    # OIDC = "OIDC", "OpenID Connect SSO"


class Organization(models.Model):
    """A tenant organization (university, institute, or SaaS customer).

    This model is NOT based on OrganizationOwnedModel because it IS
    the top-level entity — it doesn't belong to another organization.

    Attributes:
        name: Human-readable name (e.g. "Universidad Autónoma de Madrid").
        subdomain: Unique identifier for URL routing (e.g. "uam").
        plan: Subscription tier (FREE or PREMIUM).
        auth_mode: Active authentication mechanism (JWT in v1.0).
        is_active: Whether the organization is operational.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    name = models.CharField(max_length=255)
    subdomain = models.CharField(
        max_length=63,
        unique=True,
        db_index=True,
        help_text="Unique subdomain identifier for URL routing.",
    )
    plan = models.CharField(
        max_length=20,
        choices=PlanChoices.choices,
        default=PlanChoices.FREE,
    )
    auth_mode = models.CharField(
        max_length=20,
        choices=AuthModeChoices.choices,
        default=AuthModeChoices.JWT,
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name
