"""
Internationalised email rendering for notifications and direct mails.

Centralised so every email path — Celery tasks, in-app notification
mirrors, direct messages — goes through the same renderer and the
same template directory.

Templates live under ``apps/notifications/templates/notifications/``
and follow the convention::

    <event>_subject.txt   -- single-line subject (whitespace stripped)
    <event>.txt           -- plain-text body
    <event>.html          -- optional HTML body (multipart if present)

The active language is resolved from the organisation configuration
(``OrganizationConfig.default_language``) for every render. RF-13.7,
RF-15.1 and RNF-10.

References: RF-13.7, RF-13.2, RNF-10
"""

from __future__ import annotations

import logging
from typing import Any

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template import TemplateDoesNotExist
from django.template.loader import render_to_string
from django.utils import translation

logger = logging.getLogger(__name__)


def email_language_for(user) -> str:
    """Resolve the language to use for a given user's email.

    Falls back to ``settings.LANGUAGE_CODE`` (Spanish in v1.0) when no
    organisation context can be obtained.
    """
    if user is None:
        return settings.LANGUAGE_CODE

    organization = getattr(user, "organization", None)
    if organization is None:
        return settings.LANGUAGE_CODE

    config = getattr(organization, "config", None)
    if config is not None and config.default_language:
        return config.default_language

    # OrganizationConfig is created lazily; if it does not exist yet,
    # fall back to system default.
    return settings.LANGUAGE_CODE


def render_email(
    template_name: str,
    *,
    language: str,
    context: dict[str, Any],
) -> tuple[str, str, str | None]:
    """Render subject + plain text body + optional HTML body.

    Args:
        template_name: Stem name (without extension), e.g. ``"welcome"``.
        language: Language code (e.g. ``"es"``, ``"en"``) under which to
            evaluate every ``{% trans %}`` and ``{% blocktrans %}`` tag.
        context: Template context.

    Returns:
        ``(subject, text_body, html_body_or_None)``.
    """
    ctx = dict(context)
    ctx["LANGUAGE_CODE"] = language
    with translation.override(language):
        subject = render_to_string(f"notifications/{template_name}_subject.txt", context).strip()
        text_body = render_to_string(f"notifications/{template_name}.txt", context)
        try:
            html_body = render_to_string(f"notifications/{template_name}.html", context)
        except TemplateDoesNotExist:
            html_body = None
    return subject, text_body, html_body


def send_templated_email(
    *,
    template_name: str,
    recipient: str,
    language: str,
    context: dict[str, Any],
) -> None:
    """Render a template and send the result as a multipart email.

    Always raises on failure — callers (Celery tasks) decide retry semantics.
    """
    subject, text_body, html_body = render_email(template_name, language=language, context=context)

    msg = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    if html_body:
        msg.attach_alternative(html_body, "text/html")
    msg.send(fail_silently=False)
