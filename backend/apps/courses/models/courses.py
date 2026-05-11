"""
Academic course model.

A course represents an academic year (e.g. "2025-2026") within an
organization. At any point, exactly ONE course is active per org;
all previous courses are archived and read-only.

The course is the top-level container for the academic hierarchy:
    Organization → Course → Subject → Exam → ExamInstance

Course lifecycle:
    ACTIVE  → manager creates a new course → current becomes ARCHIVED
    ARCHIVED → read-only (gestores can browse, no writes allowed)

Key design decisions:
    - Uses OrganizationOwnedModel for automatic tenant isolation.
    - `is_active` is the fast check; `status` carries richer semantics
      for future states if needed.
    - Label uniqueness is scoped to organization (same label in
      different orgs is fine).
    - Dates are informational (RF-3.1: "no disparan automatismos").

References: RF-3.1, RF-3.3
"""

from django.db import models

from apps.core.models.base_models import OrganizationOwnedModel


class AcademicCourse(OrganizationOwnedModel):
    """An academic year/period within an organization.

    Exactly one course can be active per organization at any time.
    Creating a new course automatically archives the previous one
    and triggers the transition process (RF-3.3).

    Attributes:
        label: Human-readable identifier (e.g. "2025-2026").
            Unique within the organization.
        start_date: Informational start date of the academic period.
        end_date: Informational end date (optional).
        is_active: True for the current course, False for archived.
    """

    label = models.CharField(
        max_length=50,
        help_text='Academic period label (e.g. "2025-2026").',
    )
    start_date = models.DateField(
        null=True,
        blank=True,
        help_text="Informational start date. Does not trigger automation.",
    )
    end_date = models.DateField(
        null=True,
        blank=True,
        help_text="Informational end date. Does not trigger automation.",
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text="True for the current course, False for archived ones.",
    )

    class Meta:
        constraints = [
            # Same label cannot exist twice in the same organization.
            models.UniqueConstraint(
                fields=["label", "organization"],
                name="unique_course_label_per_org",
            ),
        ]
        ordering = ["-is_active", "-created_at"]

    def __str__(self) -> str:
        return f"{self.label} ({'ACTIVE' if self.is_active else 'ARCHIVED'})"
