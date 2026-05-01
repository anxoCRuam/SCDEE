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


Organization configuration model (RF-15.1, RF-15.2).

Stores per-organization configurable parameters that can be
modified at runtime without restart. Cached in Redis with
invalidation on write.

Created automatically when an Organization is first accessed
via get_org_config().

References: RF-15.1, RF-15.2, RF-15.5
"""

import uuid

# apps/organizations/models.py
from django.db import models

from apps.core.models import TimestampedModel


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


class OrganizationConfig(TimestampedModel):
    """Per-organization runtime configuration.

    One-to-one with Organization. Created lazily on first access.
    All fields have sensible defaults.
    """

    organization = models.OneToOneField(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="config",
    )

    # ── Recognition settings ─────────────────────────────────
    default_ocr_engine = models.CharField(
        max_length=50,
        default="easyocr",
        help_text="OCR engine ID (easyocr, tesseract).",
    )
    recognition_confidence_threshold = models.FloatField(
        default=0.7,
        help_text="Minimum confidence for auto-accepting OCR results.",
    )
    assembly_timeout_seconds = models.IntegerField(
        default=600,
        help_text="Seconds before marking ASSEMBLING instances as MISSING_PAGE.",
    )
    # NOTE: orphan-page proximity window is a SYSTEM-wide setting
    # (settings.INGESTION_PROXIMITY_WINDOW_SECONDS), not per-organization,
    # because an unidentified page has no organisation context until QR
    # decoding succeeds. Per-org tuning is meaningless in that flow.

    # ── Content limits ───────────────────────────────────────
    max_audio_duration_minutes = models.IntegerField(
        default=60,
        help_text="Maximum audio annotation duration in minutes.",
    )
    max_page_size_mb = models.IntegerField(
        default=50,
        help_text="Maximum page image size in MB.",
    )

    # ── Maintenance ──────────────────────────────────────────
    auto_delete_frequency_days = models.IntegerField(
        default=365,
        help_text="Days after course archival before automatic deletion.",
    )
    delete_notice_days = models.IntegerField(
        default=30,
        help_text="Days before deletion to notify managers.",
    )

    # ── Localization & URLs ──────────────────────────────────
    default_language = models.CharField(
        max_length=5,
        default="es",
        help_text="Default language for emails and notifications.",
    )
    temp_url_expiration_minutes = models.IntegerField(
        default=15,
        help_text="Expiration time for MinIO presigned URLs.",
    )

    # ── Role permission defaults (RF-15.5) ───────────────────
    role_permission_defaults = models.JSONField(
        default=dict,
        blank=True,
        help_text="Per-role permission defaults override. Empty = use code defaults.",
    )

    # ── Grade export format ──────────────────────────────────
    grade_export_columns = models.JSONField(
        default=list,
        blank=True,
        help_text="CSV column order for grade export. "
        'Empty = default ["nia","name","group","grade"].',
    )

    class Meta:
        verbose_name = "Organization Configuration"

    def __str__(self) -> str:
        return f"Config for {self.organization_id}"
