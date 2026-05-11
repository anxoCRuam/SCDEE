"""
Notification model.

In-app notifications stored in the database, consultable via polling.
Each notification can optionally trigger an email mirror if the user
has email notifications enabled.

Notifications are archived during course transitions (RF-13.5) and
excluded from default listing.

References: RF-13.1 through RF-13.7
"""

from django.conf import settings
from django.db import models

from apps.core.models.base_models import TimestampedModel


class NotificationType(models.TextChoices):
    """Type of notification event."""

    GRADES_PUBLISHED = "GRADES_PUBLISHED", "Grades published"
    REVIEW_OPENED = "REVIEW_OPENED", "Review window opened"
    REVIEW_RESULT = "REVIEW_RESULT", "Review result available"
    REVIEW_REQUESTS_SUMMARY = "REVIEW_REQUESTS_SUMMARY", "Review requests summary"
    ASSIGNMENT_CREATED = "ASSIGNMENT_CREATED", "Grading assignment created"
    INGESTION_ISSUE = "INGESTION_ISSUE", "Ingestion issue detected"
    COURSE_TRANSITION = "COURSE_TRANSITION", "Course transition completed"
    DELETION_REMINDER = "DELETION_REMINDER", "Automatic deletion reminder"
    GENERAL = "GENERAL", "General notification"


class NotificationStatus(models.TextChoices):
    """Status of a notification."""

    UNREAD = "UNREAD", "Unread"
    READ = "READ", "Read"
    ARCHIVED = "ARCHIVED", "Archived"


class Notification(TimestampedModel):
    """An in-app notification for a user.

    Attributes:
        user: The recipient user.
        notification_type: Type of event that generated this notification.
        title: Short summary for display.
        message: Full notification content.
        status: UNREAD, READ, or ARCHIVED.
        course: Optional FK for course-based archival (RF-13.5).
        metadata: Optional JSON with extra context (exam name, counts, etc.).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notifications",
    )
    notification_type = models.CharField(
        max_length=30,
        choices=NotificationType.choices,
        db_index=True,
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    status = models.CharField(
        max_length=10,
        choices=NotificationStatus.choices,
        default=NotificationStatus.UNREAD,
        db_index=True,
    )
    course = models.ForeignKey(
        "courses.AcademicCourse",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
        help_text="For course-based archival during transitions.",
    )
    metadata = models.JSONField(
        default=dict,
        blank=True,
        help_text="Extra context data (exam name, counts, etc.).",
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "status", "-created_at"], name="idx_notif_user_status"),
        ]

    def __str__(self) -> str:
        return f"{self.notification_type}: {self.title[:50]}"
