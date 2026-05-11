"""
Thread-local tenant context for multi-tenant isolation.

This module provides a request-scoped storage for the current
organization_id. It's used by:

1. OrganizationMiddleware — sets the org ID from the authenticated user.
2. TenantManager — reads it to auto-filter querysets.

Why thread-local?
    Each Django request runs in its own thread (with gunicorn sync workers).
    Thread-local storage ensures org isolation without passing organization_id
    through every function signature. The middleware clears it after each
    request to prevent leakage between requests.

Why not contextvars?
    contextvars is the modern Python alternative (async-safe). However,
    since we use WSGI (gunicorn sync) in v1.0, threading.local is simpler
    and well-tested. If we migrate to ASGI, this module should switch to
    contextvars.

References: RNF-9, RNF-13
"""

import threading
from uuid import UUID

_thread_locals = threading.local()


def set_current_organization_id(organization_id: UUID | None) -> None:
    """Set the organization ID for the current request context.

    Called by OrganizationMiddleware at the start of each request.

    Args:
        organization_id: The UUID of the authenticated user's organization,
            or None for unauthenticated requests / superadmin operations.
    """
    _thread_locals.organization_id = organization_id


def get_current_organization_id() -> UUID | None:
    """Retrieve the organization ID for the current request context.

    Returns:
        The UUID of the current organization, or None if not set
        (unauthenticated request, Celery task, or management command).
    """
    return getattr(_thread_locals, "organization_id", None)


def clear_current_organization_id() -> None:
    """Clear the organization ID from the current thread.

    Called by OrganizationMiddleware in the response phase to prevent
    leakage between requests that share the same thread.
    """
    _thread_locals.organization_id = None
