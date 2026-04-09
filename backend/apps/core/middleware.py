"""
Multi-tenant middleware for automatic organization-scoped queries.

This middleware sits after authentication in the middleware chain.
For each request, it:
1. Checks if the user is authenticated.
2. Extracts the user's organization_id.
3. Stores it in thread-local context (tenant_context).
4. Clears it after the response, regardless of success or error.

Downstream, TenantManager reads this context to auto-filter queries,
ensuring that users of one organization can never see data from another.

Exemptions:
    - Unauthenticated requests: context stays None (public endpoints).
    - Superadmin users: context stays None (cross-org access).
    - Celery tasks: no middleware runs; tasks must set context explicitly.

References: RNF-9, RNF-13, Paso 0.4
"""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from apps.core.tenant_context import (
    clear_current_organization_id,
    set_current_organization_id,
)


class OrganizationMiddleware:
    """Inject organization_id into thread-local context per request."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        organization_id = self._resolve_organization_id(request)
        set_current_organization_id(organization_id)

        try:
            response = self.get_response(request)
        finally:
            # Always clear context to prevent leakage when threads are reused.
            clear_current_organization_id()

        return response

    def _resolve_organization_id(self, request: HttpRequest):
        """Extract organization_id from the authenticated user.

        Returns None if:
            - The user is not authenticated (anonymous).
            - The user is a superadmin (needs cross-org access).
            - The user has no organization (shouldn't happen, but defensive).

        Returns:
            UUID of the organization, or None.
        """
        user = getattr(request, "user", None)

        if user is None or not getattr(user, "is_authenticated", False):
            return None

        # Superadmins operate across organizations — no tenant filter.
        if getattr(user, "is_superadmin", False):
            return None

        return getattr(user, "organization_id", None)
