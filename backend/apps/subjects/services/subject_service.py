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

import logging
from typing import Any

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from apps.courses.models import AcademicCourse
from apps.exams.models import Exam
from apps.grading.models import Grade
from apps.instances.models import ExamInstance
from apps.subjects.models import (
    MembershipRole,
    Subject,
    SubjectGroup,
    SubjectMembership,
)

logger = logging.getLogger(__name__)
User = get_user_model()


class SubjectServiceError(Exception):
    """Raised when a subject operation violates business rules."""

    def __init__(self, code: str, detail: str = "", field: str = "") -> None:
        self.code = code
        self.field = field
        super().__init__(detail or code)


# ── Subject CRUD ─────────────────────────────────────────────


def create_subject(
    *,
    organization,
    name: str,
    code: str,
    semester: str = "",
    coordinator_id: str,
) -> Subject:
    """Create a subject in the active course with auto-assigned coordinator.

    Args:
        organization: The org instance.
        name: Subject display name.
        code: Unique code within the course.
        semester: Free-form period label.
        coordinator_id: UUID of the user to assign as coordinator.

    Returns:
        The created Subject instance.

    Raises:
        SubjectServiceError: If code is duplicate, coordinator invalid, or no active course.
    """
    # Get the active course for this organization.
    active_course = AcademicCourse.unfiltered.filter(
        organization=organization, is_active=True
    ).first()
    if active_course is None:
        raise SubjectServiceError(
            code="NO_ACTIVE_COURSE",
            detail="Cannot create a subject without an active course.",
        )

    # Validate coordinator exists and belongs to the org.
    try:
        coordinator = User.objects.get(pk=coordinator_id, organization=organization)
    except User.DoesNotExist:
        raise SubjectServiceError(
            code="COORDINATOR_NOT_FOUND",
            detail=f"User {coordinator_id} not found in this organization.",
            field="coordinator_id",
        ) from None

    if not coordinator.is_active:
        raise SubjectServiceError(
            code="COORDINATOR_INACTIVE",
            detail=f"User {coordinator.email} is inactive.",
            field="coordinator_id",
        )

    try:
        with transaction.atomic():
            subject = Subject(
                organization=organization,
                name=name,
                code=code,
                semester=semester,
                course=active_course,
                coordinator=coordinator,
            )
            subject.save()

            # Auto-assign coordinator membership (RF-4.1).
            SubjectMembership.objects.create(
                organization=organization,
                user=coordinator,
                subject=subject,
                role=MembershipRole.COORDINATOR,
                permission_overrides={},
                is_active=True,
            )
    except IntegrityError as exc:
        if "unique_subject_code_per_course" in str(exc).lower():
            raise SubjectServiceError(
                code="CODE_ALREADY_EXISTS",
                detail=f"Subject code '{code}' already exists in the active course.",
                field="code",
            ) from exc
        raise SubjectServiceError(code="SUBJECT_CREATION_FAILED", detail=str(exc)) from exc

    return subject


def update_subject(
    subject: Subject,
    *,
    data: dict,
    organization,
) -> dict[str, dict[str, Any]]:
    """Update a subject's fields. Handles coordinator change.

    Args:
        subject: The Subject instance to update.
        data: Dict of field names → new values (PATCH semantics).
        organization: The organization (for coordinator validation).

    Returns:
        Dict of changes for audit logging.
    """
    if not subject.course.is_active:
        raise SubjectServiceError(code="COURSE_ARCHIVED")

    changes: dict[str, dict[str, Any]] = {}

    for field in ("name", "code", "semester"):
        if field in data:
            old_value = getattr(subject, field)
            new_value = data[field]
            if old_value != new_value:
                changes[field] = {"old": old_value, "new": new_value}
                setattr(subject, field, new_value)

    # Handle coordinator change (RF-4.3).
    if "coordinator_id" in data:
        new_coord_id = data["coordinator_id"]
        if str(subject.coordinator_id) != str(new_coord_id):
            try:
                new_coordinator = User.objects.get(pk=new_coord_id, organization=organization)
            except User.DoesNotExist:
                raise SubjectServiceError(
                    code="COORDINATOR_NOT_FOUND",
                    field="coordinator_id",
                ) from None

            with transaction.atomic():
                # Demote old coordinator to TEACHER.
                old_membership = SubjectMembership.unfiltered.filter(
                    subject=subject,
                    user=subject.coordinator,
                    role=MembershipRole.COORDINATOR,
                    is_active=True,
                ).first()
                if old_membership:
                    old_membership.role = MembershipRole.TEACHER
                    old_membership.permission_overrides = {}
                    old_membership.save()

                # Promote or create new coordinator membership.
                new_membership, created = SubjectMembership.unfiltered.get_or_create(
                    subject=subject,
                    user=new_coordinator,
                    is_active=True,
                    defaults={
                        "organization": organization,
                        "role": MembershipRole.COORDINATOR,
                        "permission_overrides": {},
                    },
                )
                if not created:
                    new_membership.role = MembershipRole.COORDINATOR
                    new_membership.permission_overrides = {}
                    new_membership.save()

                changes["coordinator"] = {
                    "old": str(subject.coordinator_id),
                    "new": str(new_coord_id),
                }
                subject.coordinator = new_coordinator

    if changes:
        try:
            subject.save()
        except IntegrityError as exc:
            if "unique_subject_code_per_course" in str(exc).lower():
                raise SubjectServiceError(code="CODE_ALREADY_EXISTS", field="code") from exc
            raise

    return changes


def delete_subject(subject: Subject) -> None:
    """Delete a subject if it has no exams (RF-4.4)."""
    if not subject.course.is_active:
        raise SubjectServiceError(code="COURSE_ARCHIVED")

    if Exam.unfiltered.filter(subject=subject).exists():
        raise SubjectServiceError(
            code="HAS_EXAMS",
            detail="Cannot delete a subject with exams. Remove exams first.",
        )

    subject.delete()


# ── Group management ─────────────────────────────────────────


def create_group(*, subject: Subject, label: str) -> SubjectGroup:
    """Create a group within a subject (RF-4.5)."""
    if not subject.course.is_active:
        raise SubjectServiceError(code="COURSE_ARCHIVED")

    try:
        group = SubjectGroup.objects.create(subject=subject, label=label)
    except IntegrityError:
        raise SubjectServiceError(
            code="GROUP_LABEL_EXISTS",
            detail=f"Group '{label}' already exists in this subject.",
            field="label",
        ) from None
    return group


def delete_group(group: SubjectGroup) -> int:
    """Delete a group. Students in this group lose their group assignment (RF-4.5).

    Returns:
        Number of memberships whose group was nulled.
    """
    if not group.subject.course.is_active:
        raise SubjectServiceError(code="COURSE_ARCHIVED")

    # Null out group references on memberships (CASCADE would delete them).
    affected = SubjectMembership.unfiltered.filter(group=group).update(group=None)
    group.delete()
    return affected


# ── Membership management ────────────────────────────────────


def assign_member(
    *,
    organization,
    subject: Subject,
    user_id: str,
    role: str,
    group_id: str | None = None,
) -> SubjectMembership:
    """Assign a user to a subject with a role (RF-4.6).

    Args:
        organization: The organization instance.
        subject: The Subject to assign to.
        user_id: UUID of the user to assign.
        role: MembershipRole value (TEACHER or STUDENT).
        group_id: Optional UUID of a SubjectGroup.

    Returns:
        The created SubjectMembership.
    """
    if not subject.course.is_active:
        raise SubjectServiceError(code="COURSE_ARCHIVED")

    # Validate user.
    try:
        user = User.objects.get(pk=user_id, organization=organization)
    except User.DoesNotExist:
        raise SubjectServiceError(code="USER_NOT_FOUND", field="user_id") from None

    if not user.is_active:
        raise SubjectServiceError(code="USER_INACTIVE", field="user_id")

    # Prevent assigning as COORDINATOR through this endpoint.
    if role == MembershipRole.COORDINATOR:
        raise SubjectServiceError(
            code="INVALID_ROLE",
            detail="Use subject update to change coordinator.",
            field="role",
        )

    # Validate group if provided.
    group = None
    if group_id:
        try:
            group = SubjectGroup.objects.get(pk=group_id, subject=subject)
        except SubjectGroup.DoesNotExist:
            raise SubjectServiceError(code="GROUP_NOT_FOUND", field="group_id") from None

    # For students: check they're not already in another group in this subject.
    if role == MembershipRole.STUDENT and group:
        existing = SubjectMembership.unfiltered.filter(
            user=user, subject=subject, is_active=True
        ).first()
        if existing and existing.group and existing.group != group:
            raise SubjectServiceError(
                code="STUDENT_ALREADY_IN_GROUP",
                detail="Student is already assigned to another group in this subject.",
                field="group_id",
            )

    try:
        membership = SubjectMembership.objects.create(
            organization=organization,
            user=user,
            subject=subject,
            role=role,
            group=group,
            permission_overrides={},
            is_active=True,
        )
    except IntegrityError:
        raise SubjectServiceError(
            code="ALREADY_MEMBER",
            detail="User already has an active membership in this subject.",
        ) from None

    return membership


def unassign_member(membership: SubjectMembership) -> bool:
    """Remove a member from a subject (RF-4.7).

    If the user has exam instances, soft-delete (is_active=False).
    Otherwise, hard-delete the membership.

    Returns:
        True if soft-deleted, False if hard-deleted.
    """
    if not membership.subject.course.is_active:
        raise SubjectServiceError(code="COURSE_ARCHIVED")

    # Prevent removing the coordinator.
    if membership.role == MembershipRole.COORDINATOR:
        raise SubjectServiceError(
            code="CANNOT_REMOVE_COORDINATOR",
            detail="Change coordinator via subject update instead.",
        )

    # Soft-delete if the user has historical exam activity on this subject:
    # either as student (instance owner) or as grader (assigned a grade).
    has_history = (
        ExamInstance.unfiltered.filter(
            student=membership.user,
            exam__subject=membership.subject,
        ).exists()
        or Grade.objects.filter(
            grader=membership.user,
            instance__exam__subject=membership.subject,
        ).exists()
    )

    if has_history:
        membership.is_active = False
        membership.save(update_fields=["is_active", "updated_at"])
        return True
    membership.delete()
    return False


# ── Permission management ────────────────────────────────────


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
