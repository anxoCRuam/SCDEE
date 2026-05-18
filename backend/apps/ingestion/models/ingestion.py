# apps/ingestion/models/ingestion.py

import uuid

from django.db import models

from apps.core.models.base_models import TimestampedModel


class IngestionBatch(TimestampedModel):
    """A group of pages that arrived together (e.g. a multi-page PDF).

    Keeping pages of the same batch linked simplifies later assembly:
    pages that belong to the same physical scan are almost certainly
    from the same student and should be kept together during matching.

    Attributes:
        organization: The organisation that owns this batch.
        source: How the batch entered the system (``watcher`` or ``manual``).
        student_id: Optional – if the submitter already knows the student.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source = models.CharField(
        max_length=20,
        choices=[("watcher", "SFTP Watcher"), ("manual", "Manual upload")],
        default="watcher",
    )
    student_id = models.UUIDField(
        null=True,
        blank=True,
        help_text="If known in advance (e.g. professor uploads a student's exam).",
    )
    # Total pages in the batch (denormalised for convenience)
    total_pages = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Batch {self.id} ({self.source}, {self.total_pages} pages)"
