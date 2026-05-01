"""
Custom DRF permission classes.

These permissions enforce the two main access levels in SCDEE:

1. IsSuperAdmin: System-level access (create organizations, cross-org ops).
   Only users with is_superadmin=True pass this check.

2. IsOrgManager: Organization-level management (user CRUD, course config).
   Only users with is_staff=True within their organization pass.

3. IsAuthenticated: Any active, authenticated user (DRF built-in, used
   for profile, viewing own data, etc.).

Contextual permissions (COORDINATOR, TEACHER, STUDENT) are handled
per-subject via SubjectMembership in Fase 3.

Why custom classes instead of DRF's IsAdminUser?
    DRF's IsAdminUser checks Django's is_staff, which in our system
    means "organization manager", not "Django admin". We need
    explicit separate checks for superadmin vs org manager.

References: RF-2.1, RF-2.2, RF-2.6
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission
from rest_framework.request import Request
from rest_framework.views import APIView


class IsSuperAdmin(BasePermission):
    """Allow access only to system superadmins.

    Superadmins can:
    - Create and manage organizations (RF-2.1)
    - Access data across all organizations
    - Create other superadmins (via manage.py only)

    Users with is_superadmin=True bypass tenant filtering
    (OrganizationMiddleware returns None for their org context).
    """

    message = "SUPERADMIN_REQUIRED"

    def has_permission(self, request: Request, view: APIView) -> bool:
        return bool(
            request.user
            and request.user.is_authenticated
            and getattr(request.user, "is_superadmin", False)
        )


class IsOrgManager(BasePermission):
    """Allow access only to organization managers (is_staff=True).

    Managers can:
    - Create, modify, and deactivate users (RF-2.2 through RF-2.8)
    - Manage courses, subjects, exams within their organization
    - Import/export users (RF-2.3, RF-2.13)
    - View audit logs for their organization (RF-16.2)

    This permission does NOT check which organization — that's handled
    automatically by the TenantManager which filters queries by the
    authenticated user's organization.
    """

    message = "ORG_MANAGER_REQUIRED"

    def has_permission(self, request: Request, view: APIView) -> bool:
        return bool(
            request.user
            and request.user.is_authenticated
            and getattr(request.user, "is_staff", False)
        )
