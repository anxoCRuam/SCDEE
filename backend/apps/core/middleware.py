"""
Multi-tenant middleware for automatic organization-scoped queries.

This middleware sits after authentication in the middleware chain.
1. Clears org_id after the response, regardless of success or error.

    Exemptions:
        - Unauthenticated requests: context stays None (public endpoints).
        - Superadmin users: context stays None (cross-org access).
        - Celery tasks: no middleware runs; tasks must set context explicitly.

2. AntiCacheMiddleware: adds no-store headers to sensitive responses (RF-16.7)

References: RNF-13, RNF-16.7
"""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

from apps.core.tenancy.tenant_context import clear_current_organization_id


class ClearTenantContextMiddleware:
    """Inject organization_id into thread-local context per request."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # NOTE: tenant context is SET by JWTAuthentication.authenticate()
        # (apps/accounts/backends.py), which is the first place where the
        # authenticated user is actually available. Django middleware runs
        # before DRF authentication, so request.user is AnonymousUser here.
        # This middleware is only responsible for CLEARING the context after
        # each response to prevent thread-local leakage between requests.
        try:
            response = self.get_response(request)
        finally:
            # Always clear context to prevent leakage when threads are reused.
            clear_current_organization_id()

        return response


# ── Anti-cache middleware (RF-16.7) ──────────────────────────


class AntiCacheMiddleware:
    """Add no-store/no-cache headers to responses with sensitive data.

    Applied to all responses — the browser and proxies must not
    cache API responses containing personal data, grades, or PDFs.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        # Apply to all API responses.
        if request.path.startswith("/api/"):
            response["Cache-Control"] = "no-store, no-cache, must-revalidate"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"

        return response
