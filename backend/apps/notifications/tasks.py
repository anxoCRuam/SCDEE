"""
Celery tasks for notification email dispatch (RF-13.2, RF-13.7).

Sends the email mirror for an in-app Notification. The body is
rendered from a template under ``apps/notifications/templates/`` so
every notification type has a localised subject, plain-text body
and (optional) HTML body. The active language is resolved from the
recipient's organisation configuration.

References: RF-13.2, RF-13.7, RNF-10
"""

from __future__ import annotations

import logging

from celery import shared_task

from apps.notifications.email import email_language_for, send_templated_email

logger = logging.getLogger(__name__)


# Mapping NotificationType → template stem (matches the file names in
# apps/notifications/templates/notifications/<stem>{,_subject}.{txt,html}).
_TEMPLATE_BY_TYPE: dict[str, str] = {
    "GRADES_PUBLISHED": "grades_published",
    "REVIEW_OPENED": "review_opened",
    "REVIEW_RESULT": "review_result",
    "REVIEW_REQUESTS_SUMMARY": "review_requests_grouped",
    "ASSIGNMENT_CREATED": "assignment_created",
    "INGESTION_ISSUE": "ingestion_incident",
    "COURSE_TRANSITION": "course_transition",
    "DELETION_REMINDER": "auto_deletion_reminder",
    "GENERAL": "general",
}


@shared_task(
    queue="notifications",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_notification_email(self, notification_id: str) -> None:
    """Send the email mirror for an in-app notification (RF-13.2)."""
    from apps.notifications.models import Notification

    try:
        notification = Notification.objects.select_related("user").get(pk=notification_id)
    except Notification.DoesNotExist:
        logger.error("Notification %s not found for email.", notification_id)
        return

    user = notification.user
    if not user.email:
        logger.info("Notification %s skipped: user has no email.", notification_id)
        return

    template = _TEMPLATE_BY_TYPE.get(notification.notification_type, "general")
    language = email_language_for(user)
    context = {
        "user": user,
        "notification": notification,
        "title": notification.title,
        "message": notification.message,
        # Splat metadata so templates can use ``{{ exam_name }}``,
        # ``{{ deadline }}``, etc. directly.
        **(notification.metadata or {}),
    }

    try:
        send_templated_email(
            template_name=template,
            recipient=user.email,
            language=language,
            context=context,
        )
        logger.info("Email sent for notification %s to %s.", notification_id, user.email)
    except Exception as exc:
        logger.error("Email send failed for notification %s: %s", notification_id, exc)
        raise self.retry(exc=exc) from exc
