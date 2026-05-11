"""
Exam instance and page models.

ExamInstance: The materialization of an exam for a student — their scanned
pages, metadata, grades, and lifecycle state. The PDF is composed on demand
from ExamPage records, never stored as a single file.

ExamPage: A single scanned page stored in MinIO. Pages are the atomic unit
of ingestion — they arrive one by one from the scanner and are assembled
into instances based on QR identification.

State machine (RF-7.5):
    ASSEMBLING → RECEIVED → QUEUED → PENDING_GRADING → GRADED →
    PUBLISHED → IN_REVIEW → FINALIZED → ARCHIVED
                          → PENDING_REVIEW (if review requested)

    Retrocesos: GRADED→PENDING_GRADING, PUBLISHED→GRADED, FINALIZED→PENDING_REVIEW

Issue types (RF-7.6):
    Page-level: QR_READ_ERROR, QR_MISMATCH, ORPHAN_PAGE, EXTRA_PAGE
    Instance-level: MISSING_PAGE, STUDENT_NOT_IDENTIFIED, DUPLICATE_STUDENT

References: RF-7.1 through RF-7.15
"""

from django.conf import settings
from django.db import models

from apps.core.models.base_models import OrganizationOwnedModel, TimestampedModel

# ── Enums ────────────────────────────────────────────────────


class InstanceStatus(models.TextChoices):
    """Lifecycle states for an exam instance."""

    ASSEMBLING = "ASSEMBLING", "Assembling pages"
    RECEIVED = "RECEIVED", "All pages received"
    QUEUED = "QUEUED", "Queued for recognition"
    PENDING_GRADING = "PENDING_GRADING", "Pending grading"
    GRADED = "GRADED", "Graded"
    PUBLISHED = "PUBLISHED", "Published to student"
    IN_REVIEW = "IN_REVIEW", "In review period"
    PENDING_REVIEW = "PENDING_REVIEW", "Pending review resolution"
    FINALIZED = "FINALIZED", "Finalized"
    ARCHIVED = "ARCHIVED", "Archived"


class PageStatus(models.TextChoices):
    """Status of a single scanned page."""

    PENDING_RECOGNITION = "PENDING_RECOGNITION", "Pending recognition"
    RECOGNIZED = "RECOGNIZED", "Recognition complete"
    ORPHAN = "ORPHAN", "Orphan (no QR, assigned by proximity)"
    DISCARDED = "DISCARDED", "Discarded by coordinator"


class PageIssueType(models.TextChoices):
    """Issue types for individual pages."""

    QR_READ_ERROR = "QR_READ_ERROR", "QR code could not be decoded"
    QR_MISMATCH = "QR_MISMATCH", "QR indicates different exam/model/page"
    ORPHAN_PAGE = "ORPHAN_PAGE", "Page assigned by temporal proximity"
    EXTRA_PAGE = "EXTRA_PAGE", "Page number exceeds expected count"


class InstanceIssueType(models.TextChoices):
    """Issue types at the instance level."""

    MISSING_PAGE = "MISSING_PAGE", "Expected pages not received"
    STUDENT_NOT_IDENTIFIED = "STUDENT_NOT_IDENTIFIED", "No student identified"
    DUPLICATE_STUDENT = "DUPLICATE_STUDENT", "Student already has another instance"


# ── Transition table ─────────────────────────────────────────

# Valid transitions: current_status → set of allowed target statuses.
VALID_TRANSITIONS: dict[str, set[str]] = {
    InstanceStatus.ASSEMBLING: {InstanceStatus.RECEIVED},
    InstanceStatus.RECEIVED: {InstanceStatus.QUEUED},
    InstanceStatus.QUEUED: {InstanceStatus.PENDING_GRADING},
    InstanceStatus.PENDING_GRADING: {InstanceStatus.GRADED},
    InstanceStatus.GRADED: {
        InstanceStatus.PUBLISHED,
        InstanceStatus.PENDING_GRADING,  # Retroceso
    },
    InstanceStatus.PUBLISHED: {
        InstanceStatus.IN_REVIEW,
        InstanceStatus.GRADED,  # Retroceso
    },
    InstanceStatus.IN_REVIEW: {
        InstanceStatus.FINALIZED,
        InstanceStatus.PENDING_REVIEW,
    },
    InstanceStatus.PENDING_REVIEW: {InstanceStatus.GRADED},
    InstanceStatus.FINALIZED: {
        InstanceStatus.ARCHIVED,
        InstanceStatus.PENDING_REVIEW,  # Retroceso
    },
    InstanceStatus.ARCHIVED: set(),  # Terminal state
}


def is_valid_transition(current: str, target: str) -> bool:
    """Check if a state transition is legal."""
    return target in VALID_TRANSITIONS.get(current, set())


# ── ExamInstance ─────────────────────────────────────────────


class ExamInstance(OrganizationOwnedModel):
    """A student's exam instance composed of scanned pages.

    Attributes:
        exam: FK to the exam this instance belongs to.
        student: Optional FK — may be null until identification.
        model: Optional FK to the exam model (detected from QR).
        status: Current lifecycle state.
        has_issues: Boolean flag — blocks PUBLISHED transition if True.
        issue_types: JSON list of active instance-level issue type strings.
        total_score: Computed sum of grades (min 0).
        expected_pages: How many pages this instance should have.
        version: Optimistic concurrency control for grading.
    """

    exam = models.ForeignKey(
        "exams.Exam",
        on_delete=models.CASCADE,
        related_name="instances",
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="exam_instances",
    )
    model = models.ForeignKey(
        "exams.ExamModel",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="instances",
    )
    status = models.CharField(
        max_length=20,
        choices=InstanceStatus.choices,
        default=InstanceStatus.ASSEMBLING,
        db_index=True,
    )
    has_issues = models.BooleanField(
        default=False,
        db_index=True,
    )
    issue_types = models.JSONField(
        default=list,
        blank=True,
        help_text="List of active instance-level issue type strings.",
    )
    total_score = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Computed sum of grades (min 0). Null if not yet graded.",
    )
    expected_pages = models.PositiveIntegerField(
        default=0,
        help_text="Number of expected pages based on model's PageProfiles.",
    )

    class Meta:
        constraints = [
            # A student can only have one active instance per exam.
            models.UniqueConstraint(
                fields=["exam", "student"],
                condition=models.Q(student__isnull=False),
                name="unique_student_per_exam",
            ),
        ]
        indexes = [
            models.Index(
                fields=["exam", "status"],
                name="idx_instance_exam_status",
            ),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        student_label = self.student.email if self.student else "unidentified"
        return f"Instance {student_label} ({self.status})"

    def add_issue(self, issue_type: str) -> None:
        """Add an instance-level issue."""
        current = list(self.issue_types or [])
        if issue_type not in current:
            current.append(issue_type)
            self.issue_types = current
            self.has_issues = True

    def remove_issue(self, issue_type: str) -> None:
        """Remove an instance-level issue and recalculate has_issues."""
        current = list(self.issue_types or [])
        if issue_type in current:
            current.remove(issue_type)
            self.issue_types = current
        # Also check page-level issues.
        page_issues = (
            self.pages.filter(issue_type__isnull=False)
            .exclude(status=PageStatus.DISCARDED)
            .exists()
        )
        self.has_issues = bool(current) or page_issues

    def recalculate_issues(self) -> None:
        """Recalculate has_issues from instance + page issues."""
        instance_issues = bool(self.issue_types)
        page_issues = (
            self.pages.exclude(issue_type="").exclude(status=PageStatus.DISCARDED).exists()
        )
        self.has_issues = instance_issues or page_issues


# ── ExamPage ─────────────────────────────────────────────────


class ExamPage(TimestampedModel):
    """A single scanned page stored in MinIO.

    Pages are the atomic unit of ingestion. They arrive individually
    and are assembled into instances. A page may be temporarily
    orphaned if its QR is unreadable.

    Attributes:
        instance: FK to the instance (nullable for orphan pages).
        page_number: Order within the instance.
        storage_ref: MinIO object key for the scanned image.
        status: Recognition status.
        issue_type: Nullable — set when a page-level issue is detected.
        recognized_at: Timestamp of when recognition completed.
    """

    instance = models.ForeignKey(
        ExamInstance,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="pages",
    )
    page_number = models.PositiveIntegerField(
        default=0,
        help_text="1-based order within the instance.",
    )
    storage_ref = models.CharField(
        max_length=500,
        help_text="MinIO object key for the scanned image.",
    )
    status = models.CharField(
        max_length=25,
        choices=PageStatus.choices,
        default=PageStatus.PENDING_RECOGNITION,
        db_index=True,
    )
    issue_type = models.CharField(
        max_length=25,
        choices=PageIssueType.choices,
        blank=True,
        default="",  # <-- sin null, con cadena vacía como "sin issue"
        help_text="Page-level issue type, if any.",
    )
    recognized_at = models.DateTimeField(
        null=True,
        blank=True,
    )
    # Organization for orphan pages (no instance yet).
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="orphan_pages",
    )

    class Meta:
        ordering = ["page_number"]

    def __str__(self) -> str:
        inst = self.instance_id or "orphan"
        return f"Page {self.page_number} of {inst} ({self.status})"
