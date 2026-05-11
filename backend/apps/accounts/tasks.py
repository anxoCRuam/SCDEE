"""
Celery tasks for direct account-related emails (RF-2.7, RF-13.6).

These two emails are sent regardless of the user's notification
preferences (RF-13.6: direct emails bypass preferences). They share
infrastructure with the rest of the notification system through
``apps.notifications.email`` so subject / body live in template files
and are rendered in the recipient's organisation language.

All email tasks run on the ``notifications`` queue to isolate them
from CPU-intensive OCR tasks.

References: RF-2.7, RF-2.8, RF-13.6, RF-13.7
"""

from __future__ import annotations

import logging

from celery import shared_task

from apps.notifications.email import email_language_for, send_templated_email

logger = logging.getLogger(__name__)


def _resolve_language(user_email: str) -> str:
    """Look the user up to obtain the org's email language."""
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    try:
        user = user_model.objects.select_related("organization__config").get(email=user_email)
    except user_model.DoesNotExist:
        return email_language_for(None)
    return email_language_for(user)


@shared_task(
    queue="notifications",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_welcome_email(self, user_email: str, raw_password: str, first_name: str) -> None:
    """Send the welcome email with the generated password (RF-2.7).

    Always sent, irrespective of email-notification preferences (RF-13.6).
    """
    try:
        send_templated_email(
            template_name="welcome",
            recipient=user_email,
            language=_resolve_language(user_email),
            context={
                "first_name": first_name,
                "user_email": user_email,
                "raw_password": raw_password,
            },
        )
        logger.info("Welcome email sent to %s", user_email)
    except Exception as exc:
        logger.error("Failed to send welcome email to %s: %s", user_email, exc)
        raise self.retry(exc=exc) from exc


@shared_task(
    queue="notifications",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
)
def send_password_reset_email(self, user_email: str, raw_password: str, first_name: str) -> None:
    """Send the password-reset email with the new password (RF-2.8)."""
    try:
        send_templated_email(
            template_name="password_reset",
            recipient=user_email,
            language=_resolve_language(user_email),
            context={
                "first_name": first_name,
                "user_email": user_email,
                "raw_password": raw_password,
            },
        )
        logger.info("Password reset email sent to %s", user_email)
    except Exception as exc:
        logger.error("Failed to send password reset email to %s: %s", user_email, exc)
        raise self.retry(exc=exc) from exc
