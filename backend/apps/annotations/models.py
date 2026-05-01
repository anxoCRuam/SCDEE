"""
Annotation model.

Annotations are records that correctors create on exam instances
during grading. Three types supported:
- TEXT: rich text payload stored inline (backend doesn't interpret it).
- STYLUS: JSON strokes with coordinates, pressure, and color.
- AUDIO: binary audio file stored in MinIO.

An annotation can be associated with a specific problem or be
general to the instance (e.g. comments on the cover page).

The `is_grade_annotation` flag triggers automatic OCR to extract
a numeric grade when the annotation is created.

References: RF-10.1 through RF-10.7
"""

from django.conf import settings
from django.db import models

from apps.core.models import TimestampedModel


class AnnotationType(models.TextChoices):
    """Type of annotation content."""

    TEXT = "TEXT", "Rich text"
    STYLUS = "STYLUS", "Stylus strokes (JSON)"
    AUDIO = "AUDIO", "Audio recording"


class Annotation(TimestampedModel):
    """A corrector's annotation on an exam instance.

    Text and stylus payloads are stored inline in the `payload` field.
    Audio payloads are stored in MinIO, referenced by `storage_ref`.

    Attributes:
        instance: FK to the exam instance.
        annotation_type: TEXT, STYLUS, or AUDIO.
        payload: Inline content (text or JSON strokes). Empty for audio.
        storage_ref: MinIO key for binary payloads (audio). Empty for text/stylus.
        page_number: Which page the annotation is on (1-based).
        x: X coordinate on the page (percentage, 0-100).
        y: Y coordinate on the page (percentage, 0-100).
        problem: Optional FK to the specific problem. Null = general annotation.
        author: FK to the corrector who created this annotation.
        is_grade_annotation: If True, triggers OCR to extract a numeric grade.
    """

    instance = models.ForeignKey(
        "instances.ExamInstance",
        on_delete=models.CASCADE,
        related_name="annotations",
    )
    annotation_type = models.CharField(
        max_length=10,
        choices=AnnotationType.choices,
    )
    payload = models.TextField(
        blank=True,
        default="",
        help_text="Inline content: rich text or JSON strokes. Empty for audio.",
    )
    storage_ref = models.CharField(
        max_length=500,
        blank=True,
        default="",
        help_text="MinIO key for audio payloads.",
    )
    page_number = models.PositiveIntegerField(
        default=1,
        help_text="1-based page number where the annotation is placed.",
    )
    x = models.FloatField(
        default=0.0,
        help_text="X coordinate on the page (percentage 0-100).",
    )
    y = models.FloatField(
        default=0.0,
        help_text="Y coordinate on the page (percentage 0-100).",
    )
    problem = models.ForeignKey(
        "exams.Problem",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="annotations",
        help_text="Optional problem association. Null = general annotation.",
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="annotations_created",
    )
    is_grade_annotation = models.BooleanField(
        default=False,
        help_text="If True, OCR is triggered to extract a numeric grade.",
    )

    class Meta:
        ordering = ["page_number", "y", "x"]

    def __str__(self) -> str:
        return f"{self.annotation_type} on page {self.page_number} by {self.author.email}"
