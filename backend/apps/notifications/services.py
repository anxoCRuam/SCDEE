"""
Notification business logic.

- create_notification(): creates the in-app row and (if enabled) enqueues
  the email mirror (RF-13.1, RF-13.2).
- mark_read() / mark_all_read() (RF-13.3).
- archive_by_course(): called during course transition (RF-13.5).
- ``notify_*`` helpers: render the in-app title/message in the recipient's
  language by activating the org's default language while building the
  strings, so the in-app text is consistent with the email mirror.

References: RF-13.1 through RF-13.7
"""

from __future__ import annotations

import logging

from django.utils import translation
from django.utils.translation import gettext as _

from apps.notifications.email import email_language_for
from apps.notifications.models import (
    Notification,
    NotificationStatus,
    NotificationType,
)

logger = logging.getLogger(__name__)


def create_notification(
    *,
    user,
    notification_type: str,
    title: str,
    message: str,
    course=None,
    metadata: dict | None = None,
) -> Notification:
    """Create an in-app notification and optionally enqueue email (RF-13.1, RF-13.2)."""
    notification = Notification.objects.create(
        user=user,
        notification_type=notification_type,
        title=title,
        message=message,
        status=NotificationStatus.UNREAD,
        course=course,
        metadata=metadata or {},
    )

    if getattr(user, "email_notifications_enabled", True):
        _enqueue_email_mirror(notification)

    return notification


def mark_read(notification_ids: list[str], user) -> int:
    """Mark notifications as read (RF-13.3). Returns count updated."""
    return Notification.objects.filter(
        pk__in=notification_ids,
        user=user,
        status=NotificationStatus.UNREAD,
    ).update(status=NotificationStatus.READ)


def mark_all_read(user) -> int:
    """Mark all unread notifications as read for a user."""
    return Notification.objects.filter(
        user=user,
        status=NotificationStatus.UNREAD,
    ).update(status=NotificationStatus.READ)


def archive_by_course(course) -> int:
    """Archive all notifications for a course (RF-13.5).

    Called during course transition by the courses service.
    """
    return (
        Notification.objects.filter(
            course=course,
        )
        .exclude(
            status=NotificationStatus.ARCHIVED,
        )
        .update(status=NotificationStatus.ARCHIVED)
    )


# ── Domain-specific notification helpers ─────────────────────


def notify_grades_published(exam, students: list) -> int:
    """Notify students that grades have been published (RF-13.1)."""
    count = 0
    course = exam.subject.course if hasattr(exam, "subject") else None
    metadata = {"exam_id": str(exam.pk), "exam_name": exam.name}

    for student in students:
        with translation.override(email_language_for(student)):
            title = _("Grades published: %(exam)s") % {"exam": exam.name}
            message = _("The grades for the exam '%(exam)s' have been published.") % {
                "exam": exam.name
            }
        create_notification(
            user=student,
            notification_type=NotificationType.GRADES_PUBLISHED,
            title=title,
            message=message,
            course=course,
            metadata=metadata,
        )
        count += 1

    return count


def notify_review_opened(exam, students: list) -> int:
    """Notify students that the review window has opened (RF-13.1)."""
    count = 0
    course = exam.subject.course if hasattr(exam, "subject") else None
    metadata = {"exam_id": str(exam.pk), "exam_name": exam.name}

    for student in students:
        with translation.override(email_language_for(student)):
            title = _("Review opened: %(exam)s") % {"exam": exam.name}
            message = _("The review window for the exam '%(exam)s' is now open.") % {
                "exam": exam.name
            }
        create_notification(
            user=student,
            notification_type=NotificationType.REVIEW_OPENED,
            title=title,
            message=message,
            course=course,
            metadata=metadata,
        )
        count += 1

    return count


def notify_review_result(instance) -> None:
    """Notify the student of the review result (RF-12.8)."""
    if not instance.student:
        return

    exam = instance.exam
    course = exam.subject.course if hasattr(exam, "subject") else None
    metadata = {
        "exam_id": str(exam.pk),
        "exam_name": exam.name,
        "instance_id": str(instance.pk),
        "total_score": str(instance.total_score) if instance.total_score is not None else "",
    }

    with translation.override(email_language_for(instance.student)):
        title = _("Review result: %(exam)s") % {"exam": exam.name}
        message = _(
            "The review for the exam '%(exam)s' has concluded. Your final grade is %(score)s."
        ) % {"exam": exam.name, "score": metadata["total_score"]}

    create_notification(
        user=instance.student,
        notification_type=NotificationType.REVIEW_RESULT,
        title=title,
        message=message,
        course=course,
        metadata=metadata,
    )


def notify_review_requests_summary(corrector, exam, request_count: int) -> None:
    """Notify the corrector with the grouped review requests summary (RF-12.5)."""
    course = exam.subject.course if hasattr(exam, "subject") else None
    metadata = {
        "exam_id": str(exam.pk),
        "exam_name": exam.name,
        "request_count": request_count,
    }

    with translation.override(email_language_for(corrector)):
        title = _("Review requests pending: %(exam)s") % {"exam": exam.name}
        message = _("%(count)s review request(s) are awaiting your attention for '%(exam)s'.") % {
            "count": request_count,
            "exam": exam.name,
        }

    create_notification(
        user=corrector,
        notification_type=NotificationType.REVIEW_REQUESTS_SUMMARY,
        title=title,
        message=message,
        course=course,
        metadata=metadata,
    )


def notify_assignment_created(corrector, exam, instance_count: int) -> None:
    """Notify a corrector that they have been assigned to an exam (RF-13.7)."""
    course = exam.subject.course if hasattr(exam, "subject") else None
    metadata = {
        "exam_id": str(exam.pk),
        "exam_name": exam.name,
        "instance_count": instance_count,
    }

    with translation.override(email_language_for(corrector)):
        title = _("Grading assignment: %(exam)s") % {"exam": exam.name}
        message = _(
            "You have been assigned as a grader for '%(exam)s' (%(count)s instance(s))."
        ) % {"exam": exam.name, "count": instance_count}

    create_notification(
        user=corrector,
        notification_type=NotificationType.ASSIGNMENT_CREATED,
        title=title,
        message=message,
        course=course,
        metadata=metadata,
    )


def notify_ingestion_issue(coordinator, exam, issue_summary: dict) -> None:
    """Notify a coordinator of ingestion incidents (RF-7.6, RF-9.11, RF-13.7)."""
    course = exam.subject.course if hasattr(exam, "subject") else None
    metadata = {
        "exam_id": str(exam.pk),
        "exam_name": exam.name,
        **issue_summary,
    }

    with translation.override(email_language_for(coordinator)):
        title = _("Ingestion incident: %(exam)s") % {"exam": exam.name}
        message = _("Ingestion incidents have been detected for '%(exam)s': %(detail)s.") % {
            "exam": exam.name,
            "detail": issue_summary.get("detail", _("see the incidents panel")),
        }

    create_notification(
        user=coordinator,
        notification_type=NotificationType.INGESTION_ISSUE,
        title=title,
        message=message,
        course=course,
        metadata=metadata,
    )


def notify_auto_deletion_reminder(
    manager,
    *,
    days_until_deletion: int,
    exam_count: int,
    notice_key: str | None = None,
) -> None:
    """Notify a manager of an upcoming automatic deletion (RF-15.4).

    ``notice_key`` is stored in the notification metadata so the
    scheduler can detect — and suppress — duplicate notices for the
    same retention window.
    """
    metadata = {
        "days_until_deletion": days_until_deletion,
        "exam_count": exam_count,
    }
    if notice_key:
        metadata["notice_key"] = notice_key

    with translation.override(email_language_for(manager)):
        title = _("Upcoming automatic deletion")
        message = _(
            "An automatic deletion will run in %(days)s day(s); "
            "%(exams)s exam(s) will be removed."
        ) % {"days": days_until_deletion, "exams": exam_count}

    create_notification(
        user=manager,
        notification_type=NotificationType.DELETION_REMINDER,
        title=title,
        message=message,
        metadata=metadata,
    )


# ── Email mirror ─────────────────────────────────────────────


def _enqueue_email_mirror(notification: Notification) -> None:
    """Enqueue an email task for a notification (RF-13.2)."""
    try:
        from apps.notifications.tasks import send_notification_email

        send_notification_email.delay(str(notification.pk))
    except Exception as exc:
        # Email failure must never affect the in-app notification.
        logger.warning("Failed to enqueue email for notification %s: %s", notification.pk, exc)
