"""
Subject management business logic.

Handles creation, update, deletion of subjects, groups, and memberships.
Views delegate here; this module never touches request objects.

Key rules:
- Subjects are created in the active course only.
- Creating a subject auto-assigns the coordinator (RF-4.1).
- Changing coordinator demotes old one to TEACHER (RF-4.3).
- Deletion only if no exams exist (RF-4.4).
- Student can only be in one group per subject (RF-4.6).
- Deleting a group nulls student group references (RF-4.5).
- Desassigning a member with exam instances → soft delete (RF-4.7).

References: RF-4.1 through RF-4.7
"""

from __future__ import annotations

from apps.subjects.models.subjects import (
    MembershipRole,
    SubjectMembership,
)
from apps.subjects.services.subjects import SubjectServiceError


def update_permissions(
    *,
    membership: SubjectMembership,
    overrides: dict[str, bool | None],
    grantor_permissions: dict[str, bool],
) -> dict[str, bool]:
    """Update permission overrides for a membership (RF-5.3, RF-5.4).

    Args:
        membership: The SubjectMembership to update.
        overrides: Dict of permission_name → bool (grant/revoke) or None (restore default).
        grantor_permissions: Effective permissions of the user granting.

    Returns:
        The new effective permissions.

    Raises:
        SubjectServiceError: If the grantor tries to delegate a permission they don't have.
    """
    if membership.role == MembershipRole.STUDENT:
        raise SubjectServiceError(
            code="STUDENT_PERMS_FIXED",
            detail="Student permissions are fixed and cannot be overridden.",
        )

    # Validate delegation restriction (RF-5.6).
    for perm_name, value in overrides.items():
        if value is True and not grantor_permissions.get(perm_name, False):
            raise SubjectServiceError(
                code="DELEGATION_EXCEEDED",
                detail=f"Cannot grant '{perm_name}' — you don't have it yourself.",
            )

    # Apply overrides.
    current = dict(membership.permission_overrides or {})
    for perm_name, value in overrides.items():
        if value is None:
            # Null → restore default (remove override).
            current.pop(perm_name, None)
        elif isinstance(value, bool):
            current[perm_name] = value

    membership.permission_overrides = current
    membership.save(update_fields=["permission_overrides", "updated_at"])

    return membership.get_effective_permissions()


def has_subject_permission(user, subject, permission_name: str) -> bool:
    """Return True if user has the given permission on the subject."""
    if user.is_staff or user.is_superadmin:
        return True
    membership = SubjectMembership.objects.filter(
        user=user, subject=subject, is_active=True
    ).first()
    if membership is None:
        return False
    effective = membership.get_effective_permissions()
    return effective.get(permission_name, False)
