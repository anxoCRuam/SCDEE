"""
Custom model managers for multi-tenant query isolation.

TenantManager is the default manager for OrganizationOwnedModel.
It reads the current organization_id from thread-local context
(set by OrganizationMiddleware) and automatically filters every
queryset to that organization.

This means:
    User.objects.all()  →  SELECT * FROM users WHERE organization_id = <current>
    User.objects.filter(is_active=True)  →  ... WHERE
        organization_id = <current> AND is_active = true

To bypass the filter (e.g., for superadmin operations or Celery tasks):
    User.unfiltered.all()  →  SELECT * FROM users (no tenant filter)

References: RNF-9, RNF-13
"""

from django.db import models

from apps.core.tenant_context import get_current_organization_id


class TenantQuerySet(models.QuerySet):
    """QuerySet that auto-filters by the current organization."""

    def _filter_by_tenant(self) -> models.QuerySet:
        """Apply tenant filter if an organization context is active."""
        org_id = get_current_organization_id()
        if org_id is not None:
            return super().filter(organization_id=org_id)
        return super().all()


class TenantManager(models.Manager):
    """Default manager that scopes all queries to the current organization.

    The tenant filter is applied lazily when the queryset is evaluated,
    not when it's created. This allows chaining additional filters
    before the SQL is generated.
    """

    def get_queryset(self) -> models.QuerySet:
        """Return a queryset filtered by the current organization."""
        qs = super().get_queryset()
        org_id = get_current_organization_id()
        if org_id is not None:
            return qs.filter(organization_id=org_id)
        return qs


class UnfilteredManager(models.Manager):
    """Manager that bypasses tenant filtering.

    Use for:
        - Superadmin operations that span organizations.
        - Celery tasks where no request context exists.
        - Data migrations and management commands.

    Usage:
        MyModel.unfiltered.all()       # No tenant filter
        MyModel.unfiltered.filter(...)  # Explicit filters only
    """

    pass
