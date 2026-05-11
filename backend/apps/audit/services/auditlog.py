"""
Audit logging service.

Provides a single entry point for creating audit records from
anywhere in the codebase. All business operations that modify data
should call `log_event()` — this is enforced by convention and
code review, not by the framework.

Usage:
    from apps.audit.services import log_event

    log_event(
        event_type="USER_CREATED",
        actor=request.user,
        organization=request.user.organization,
        entity=new_user,
        ip_address=get_client_ip(request),
        payload={"email": new_user.email},
    )

The `get_client_ip()` helper handles proxy headers (X-Forwarded-For)
so the real client IP is recorded even behind a reverse proxy.

References: RF-16.1
"""

import logging

from django.db import models
from django.http import HttpRequest

from apps.audit.models.auditlog import AuditLog

logger = logging.getLogger(__name__)

# ── Event type constants ─────────────────────────────────────
# Defined here as module-level constants for discoverability.
# Each app may define additional constants in its own module.

# Authentication
LOGIN_SUCCESS = "LOGIN_SUCCESS"
LOGIN_FAILURE = "LOGIN_FAILURE"
LOGOUT = "LOGOUT"
TOKEN_REFRESH = "TOKEN_REFRESH"  # noqa: S105
# Users
USER_CREATED = "USER_CREATED"
USER_UPDATED = "USER_UPDATED"
USER_DEACTIVATED = "USER_DEACTIVATED"
USER_BULK_IMPORTED = "USER_BULK_IMPORTED"
PASSWORD_RESET = "PASSWORD_RESET"  # noqa: S105

# Organizations
ORG_CREATED = "ORG_CREATED"
ORG_CONFIG_UPDATED = "ORG_CONFIG_UPDATED"

# Courses
COURSE_CREATED = "COURSE_CREATED"
COURSE_UPDATED = "COURSE_UPDATED"
COURSE_TRANSITION = "COURSE_TRANSITION"

# Subjects
SUBJECT_CREATED = "SUBJECT_CREATED"
SUBJECT_UPDATED = "SUBJECT_UPDATED"
MEMBERSHIP_CHANGED = "MEMBERSHIP_CHANGED"
PERMISSIONS_CHANGED = "PERMISSIONS_CHANGED"

# Exams
EXAM_CREATED = "EXAM_CREATED"
EXAM_UPDATED = "EXAM_UPDATED"
EXAM_DELETED = "EXAM_DELETED"

# Instances
INSTANCE_STATE_CHANGED = "INSTANCE_STATE_CHANGED"
INSTANCE_DELETED = "INSTANCE_DELETED"
PDF_ACCESSED = "PDF_ACCESSED"
SENSITIVE_DATA_READ = "SENSITIVE_DATA_READ"

# Grades
GRADE_MODIFIED = "GRADE_MODIFIED"
GRADES_PUBLISHED = "GRADES_PUBLISHED"

# Export / Import
DATA_EXPORTED = "DATA_EXPORTED"
DATA_IMPORTED = "DATA_IMPORTED"

# System
AUTO_DELETION_EXECUTED = "AUTO_DELETION_EXECUTED"


def log_event(
    *,
    event_type: str,
    actor: models.Model | None = None,
    organization: models.Model | None = None,
    entity: models.Model | None = None,
    ip_address: str | None = None,
    payload: dict | None = None,
) -> AuditLog:
    """Create an immutable audit log entry.

    Args:
        event_type: Symbolic identifier (use constants above).
        actor: The user who performed the action. None for system events.
        organization: The organization scope. None for global events.
        entity: The affected model instance (extracts type + ID).
        ip_address: Client IP address. Use get_client_ip() to extract.
        payload: Additional event-specific data (old/new values, counts, etc.).

    Returns:
        The created AuditLog instance.
    """
    entry = AuditLog(
        event_type=event_type,
        actor=actor,
        organization=organization,
        entity_type=entity.__class__.__name__ if entity else "",
        entity_id=str(entity.pk) if entity else "",
        ip_address=ip_address,
        payload=payload or {},
    )
    entry.save()

    logger.info(
        "Audit: %s by %s on %s(%s)",
        event_type,
        actor.pk if actor else "system",
        entry.entity_type,
        entry.entity_id,
        extra={"audit_event": event_type},
    )

    return entry


def get_client_ip(request: HttpRequest) -> str:
    """Extract the real client IP from the request.

    Checks X-Forwarded-For header first (for requests behind a
    reverse proxy / load balancer), then falls back to REMOTE_ADDR.

    Args:
        request: The Django HTTP request.

    Returns:
        The client's IP address as a string.
    """
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded_for:
        # X-Forwarded-For can contain a chain: "client, proxy1, proxy2"
        # The first one is the real client IP.
        return forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")
