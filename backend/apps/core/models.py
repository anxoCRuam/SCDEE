"""
Abstract base models for SCDEE.

These provide a consistent foundation for all concrete models:
    - UUID primary keys (security: no sequential ID exposure)
    - Automatic timestamps (audit trail for every entity)
    - Multi-tenant isolation (organization-scoped queries by default)

Inheritance hierarchy:
    UUIDModel
        └── TimestampedModel
                └── OrganizationOwnedModel  ← most business models extend this

References: RNF-9, RNF-13
"""

import uuid

from django.db import models

from apps.core.tenancy.managers import TenantManager, UnfilteredManager


class UUIDModel(models.Model):
    """Abstract model using UUID v4 as primary key.

    Why UUID instead of auto-increment?
        - Security: sequential IDs leak information (total count, creation order).
        - Portability: UUIDs are globally unique across databases and services.
        - Distributed: safe for future horizontal scaling without ID conflicts.
    """

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    class Meta:
        abstract = True


class TimestampedModel(UUIDModel):
    """Abstract model with UUID pk and automatic timestamps.

    created_at is set once on INSERT and never changes.
    updated_at is refreshed on every UPDATE.
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
        ordering = ["-created_at"]


class OrganizationOwnedModel(TimestampedModel):
    """Abstract model for entities that belong to an organization.

    Provides automatic multi-tenant isolation through TenantManager:
    - `MyModel.objects.all()` → filtered by current request's organization
    - `MyModel.unfiltered.all()` → no tenant filter (admin/system operations)

    Every business entity that belongs to an organization should extend this.
    System-wide entities (AuditLog, Organization itself) should NOT.
    """

    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.CASCADE,
        related_name="%(class)s_set",
        db_index=True,
    )

    # Default manager: auto-filters by current organization.
    objects = TenantManager()

    # Escape hatch for superadmin queries and background tasks.
    unfiltered = UnfilteredManager()

    class Meta:
        abstract = True
