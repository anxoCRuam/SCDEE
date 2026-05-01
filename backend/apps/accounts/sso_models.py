"""
SSO configuration placeholder models (RF-1.5).

These tables store per-organization IdP configuration for future
SAML 2.0 and OIDC integration. In v1.0 they are empty — the
Organization.auth_mode field defaults to 'JWT'.

When SSO is implemented:
1. A new SSOAuthPlugin subclasses BaseAuthPlugin.
2. The plugin reads SamlConfig/OidcConfig for the org.
3. After validating the external assertion, it emits the standard
   JWT token, ensuring the rest of the system works identically.

References: RF-1.5
"""

from django.db import models

from apps.core.models import TimestampedModel


class SamlConfig(TimestampedModel):
    """SAML 2.0 IdP configuration per organization (future).

    Stores the metadata and certificates needed to validate
    SAML assertions from an external Identity Provider.
    """

    organization = models.OneToOneField(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="saml_config",
    )
    entity_id = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="IdP entity ID (from IdP metadata).",
    )
    sso_url = models.URLField(
        blank=True,
        default="",
        help_text="IdP Single Sign-On service URL.",
    )
    slo_url = models.URLField(
        blank=True,
        default="",
        help_text="IdP Single Logout service URL.",
    )
    x509_cert = models.TextField(
        blank=True,
        default="",
        help_text="IdP X.509 certificate for signature validation.",
    )
    attribute_mapping = models.JSONField(
        default=dict,
        blank=True,
        help_text='Mapping of SAML attributes to user fields. E.g. {"email": "urn:oid:..."}',
    )
    is_active = models.BooleanField(default=False)

    class Meta:
        verbose_name = "SAML Configuration"

    def __str__(self) -> str:
        return f"SAML config for {self.organization_id}"


class OidcConfig(TimestampedModel):
    """OIDC (OpenID Connect) configuration per organization (future).

    Stores the client credentials and endpoints for OIDC
    authentication with an external Identity Provider.
    """

    organization = models.OneToOneField(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="oidc_config",
    )
    client_id = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )
    client_secret = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="Stored encrypted. Decrypted at runtime.",
    )
    discovery_url = models.URLField(
        blank=True,
        default="",
        help_text="OIDC discovery endpoint (.well-known/openid-configuration).",
    )
    authorization_url = models.URLField(blank=True, default="")
    token_url = models.URLField(blank=True, default="")
    userinfo_url = models.URLField(blank=True, default="")
    attribute_mapping = models.JSONField(
        default=dict,
        blank=True,
        help_text="Mapping of OIDC claims to user fields.",
    )
    scopes = models.CharField(
        max_length=255,
        blank=True,
        default="openid email profile",
    )
    is_active = models.BooleanField(default=False)

    class Meta:
        verbose_name = "OIDC Configuration"

    def __str__(self) -> str:
        return f"OIDC config for {self.organization_id}"
