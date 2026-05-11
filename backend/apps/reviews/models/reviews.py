"""
Exam review models.

ExamReview: A review window for an exam with start/end dates.
    One review per exam in v1.0.

ReviewRequest: A student's request to review specific problems.
    Created during the review window, resolved by correctors after.

Lifecycle:
    1. Coordinator creates ExamReview with dates → instances stay PUBLISHED.
    2. Celery Beat opens review at start_date → instances → IN_REVIEW.
    3. Students submit ReviewRequests during the window.
    4. Celery Beat closes review at end_date:
       - No requests → FINALIZED
       - Has requests → PENDING_REVIEW
    5. Correctors resolve requests → all resolved → FINALIZED.
    6. Student notified of result.

References: RF-12.1 through RF-12.8
"""

from django.conf import settings
from django.db import models

from apps.core.models.base_models import TimestampedModel


class ReviewStatus(models.TextChoices):
    """Status of the review window."""

    SCHEDULED = "SCHEDULED", "Scheduled (not yet open)"
    OPEN = "OPEN", "Open (students can submit requests)"
    CLOSED = "CLOSED", "Closed (requests being processed)"
    COMPLETED = "COMPLETED", "Completed (all requests resolved)"


class ExamReview(TimestampedModel):
    """A review window for an exam (one per exam in v1.0).

    Attributes:
        exam: FK to the exam. Unique in v1.0.
        start_date: When the review window opens.
        end_date: When the review window closes.
        status: Current review status.
        notify_students: Whether to notify students on creation.
    """

    exam = models.OneToOneField(
        "exams.Exam",
        on_delete=models.CASCADE,
        related_name="review",
    )
    start_date = models.DateTimeField(
        help_text="When the review window opens.",
    )
    end_date = models.DateTimeField(
        help_text="When the review window closes.",
    )
    status = models.CharField(
        max_length=15,
        choices=ReviewStatus.choices,
        default=ReviewStatus.SCHEDULED,
        db_index=True,
    )
    notify_students = models.BooleanField(
        default=True,
        help_text="Whether students were notified about this review.",
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Review for {self.exam.name} ({self.status})"


class ReviewRequest(TimestampedModel):
    """A student's request to review a specific problem.

    Attributes:
        review: FK to the ExamReview.
        instance: FK to the student's ExamInstance.
        problem: FK to the problem being contested.
        student_message: Optional message from the student.
        resolved: Whether the corrector has addressed this request.
        resolved_at: When the request was resolved.
        resolver: FK to the corrector who resolved it.
        resolver_message: Optional response from the corrector.
    """

    review = models.ForeignKey(
        ExamReview,
        on_delete=models.CASCADE,
        related_name="requests",
    )
    instance = models.ForeignKey(
        "instances.ExamInstance",
        on_delete=models.CASCADE,
        related_name="review_requests",
    )
    problem = models.ForeignKey(
        "exams.Problem",
        on_delete=models.CASCADE,
        related_name="review_requests",
    )
    student_message = models.TextField(
        blank=True,
        default="",
        help_text="Student's reason for requesting review.",
    )
    resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_reviews",
    )
    resolver_message = models.TextField(
        blank=True,
        default="",
        help_text="Corrector's response to the student.",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["instance", "problem"],
                name="unique_review_request_per_problem",
            ),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        status_label = "resolved" if self.resolved else "pending"
        return f"Review request for {self.problem.name} ({status_label})"
