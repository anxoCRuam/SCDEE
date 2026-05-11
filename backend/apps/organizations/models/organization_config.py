"""
Organization configuration model (RF-15.1, RF-15.2).

Stores per-organization configurable parameters that can be
modified at runtime without restart. Cached in Redis with
invalidation on write.

Created automatically when an Organization is first accessed
via get_org_config().

References: RF-15.1, RF-15.2, RF-15.5
"""

from django.db import models

from apps.core.models.base_models import TimestampedModel


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
        default=dict,
        blank=True,
        help_text=(
            "CSV column order and naming for grade export. "
            "Format: { base_field: exported_name }. "
            'Example: {"student__nia": "NIA", "student__last_name": "Student Last Name", '
            '"group": "Group", "total_score": "Final Grade"}. '
            "base_field can be any attribute path from ExamInstance (e.g. "
            "student__nia, exam__subject__name, model__label, total_score, ...)."
        ),
    )

    class Meta:
        verbose_name = "Organization Configuration"

    def __str__(self) -> str:
        return f"Config for {self.organization_id}"
