"""
Permissions

The permission system uses a layered approach (RF-5):
    1. is_staff → full access to all subjects in the org
    2. SubjectMembership.role → base permissions (COORDINATOR > TEACHER > STUDENT)
    3. permission_overrides (JSONField) → per-membership overrides

References: RF-5
"""

from django.db import models
from rest_framework.permissions import BasePermission


class MembershipRole(models.TextChoices):
    """Role within a subject context."""

    COORDINATOR = "COORDINATOR", "Coordinator"
    TEACHER = "TEACHER", "Teacher"
    STUDENT = "STUDENT", "Student"


# ── Default permissions per role (RF-5.2) ────────────────────
# These are the base permissions before any overrides.
# In Fase 10, OrganizationConfig will allow per-org customization.

DEFAULT_PERMISSIONS: dict[str, dict[str, bool]] = {
    MembershipRole.COORDINATOR: {
        "can_create_exams": True,
        "can_assign_correctors": True,
        "can_create_rubric": True,
        "can_resolve_issues": True,
        "can_publish_grades": True,
        "can_manage_reviews": True,
        "can_view_all_instances": True,
        "can_export_grades": True,
        "can_manage_members": True,
        "can_delegate_perms": True,
        "can_manage_groups": True,
    },
    MembershipRole.TEACHER: {
        "can_create_exams": False,
        "can_create_rubric": True,
        "can_assign_correctors": False,
        "can_resolve_issues": False,
        "can_publish_grades": False,
        "can_manage_reviews": False,
        "can_view_all_instances": True,
        "can_export_grades": False,
        "can_manage_members": False,
        "can_delegate_perms": False,
        "can_manage_groups": False,
    },
    MembershipRole.STUDENT: {
        "can_create_exams": False,
        "can_create_rubric": False,
        "can_assign_correctors": False,
        "can_resolve_issues": False,
        "can_publish_grades": False,
        "can_manage_reviews": False,
        "can_view_all_instances": False,
        "can_export_grades": False,
        "can_manage_members": False,
        "can_delegate_perms": False,
        "can_manage_groups": False,
    },
}

ALL_PERMISSION_KEYS = list(DEFAULT_PERMISSIONS[MembershipRole.COORDINATOR].keys())


def get_default_permissions(role: str, organization=None) -> dict[str, bool]:
    """Get the default permissions for a role.

    Resolution order (RF-15.5):
        1. ``organization.config.role_permission_defaults[role]`` if configured.
        2. The hard-coded ``DEFAULT_PERMISSIONS`` table.

    Per-org overrides are merged on top of the code defaults so that a partial
    override (only one or two keys configured for a role) still yields a
    complete permission dict with the rest filled in by the code defaults.

    Args:
        role: MembershipRole value.
        organization: Optional Organization instance whose config should be
            consulted. Falls back to the code defaults if not provided or if
            the organization has no override for this role.

    Returns:
        Dict of permission_name → bool with all 13 known keys.
    """
    base = dict(DEFAULT_PERMISSIONS.get(role, DEFAULT_PERMISSIONS[MembershipRole.STUDENT]))

    if organization is None:
        return base

    config = getattr(organization, "config", None)
    if config is None:
        return base

    org_defaults = (config.role_permission_defaults or {}).get(role)
    if not isinstance(org_defaults, dict):
        return base

    for key, value in org_defaults.items():
        if key in base and isinstance(value, bool):
            base[key] = value
    return base


def SubjectPermission(permission_name: str):  # noqa: N802
    class _SubjectPermission(BasePermission):
        def has_permission(self, request, view):
            if request.user.is_staff:
                return True

            membership = self._get_membership(request, view)
            if membership is None:
                return False

            permissions = membership.get_effective_permissions()
            return permissions.get(permission_name, False)

        def _get_membership(self, request, view):
            subject = self._get_subject(request, view)
            if subject is None:
                return None

            from .subjects import SubjectMembership

            return SubjectMembership.objects.filter(
                subject=subject,
                user=request.user,
                is_active=True,
            ).first()

        def _get_subject(self, request, view):
            from apps.exams.models.exams import Exam

            exam_pk = view.kwargs.get("exam_pk") or request.parser_context["kwargs"].get("exam_pk")
            if exam_pk:
                try:
                    exam = Exam.objects.get(pk=exam_pk)
                    return exam.subject
                except Exam.DoesNotExist:
                    return None

            subject_pk = view.kwargs.get("subject_pk") or request.parser_context["kwargs"].get(
                "subject_pk"
            )
            if subject_pk:
                from .subjects import Subject

                try:
                    return Subject.objects.get(pk=subject_pk)
                except Subject.DoesNotExist:
                    return None

            return None

    return _SubjectPermission
