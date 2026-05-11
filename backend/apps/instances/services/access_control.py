"""
Access control logic for exam instances.

Determines what a user can see and do with an instance based on:
- User role (manager, teacher, student)
- Exam student permissions
- Review window state
- Assignment rules for teachers/correctors

References: RF-6.10, RF-7.3, RF-7.4, RF-12.x, RF-16.7
"""

from __future__ import annotations

from apps.grading.services.grading import can_user_grade_instance
from apps.instances.models.instances import InstanceStatus


def can_access_instance_data(instance, user, permission: str) -> bool:
    """Check if a user has access to instance data requiring `permission`.

    Args:
        instance: ExamInstance to check access for.
        user: The requesting user.
        permission: One of the exam's student_permissions keys:
            'can_view_pages', 'can_view_annotations', 'can_view_rubric',
            'can_view_grade_breakdown', 'can_download_pages'.

    Returns:
        True if access is allowed.
    """
    # Staff and superadmins have full access.
    if user.is_staff or getattr(user, "is_superadmin", False):
        return True

    # Student: must be the owner and instance is published or beyond.
    if instance.student_id == user.pk:
        return _student_access(instance, permission)

    # Teacher/coordinator: check assignment or can_view_all_instances.
    return _teacher_access(instance, user, permission)


def _student_access(instance, permission: str) -> bool:
    """Access control for the student who owns the instance."""
    # Basic info always visible after publication.
    if not permission:
        return instance.status in (
            InstanceStatus.PUBLISHED,
            InstanceStatus.IN_REVIEW,
            InstanceStatus.PENDING_REVIEW,
            InstanceStatus.FINALIZED,
            InstanceStatus.ARCHIVED,
        )

    # For detailed access, check review window and exam permissions.
    exam = instance.exam
    perms = exam.student_permissions or {}

    # Is the specific permission enabled?
    if not perms.get(permission, False):
        return False

    # Check review window status.
    review = getattr(exam, "review", None)
    if review is None:
        return False

    # Review is open and instance is in review state.
    if review.status == "OPEN" and instance.status == InstanceStatus.IN_REVIEW:
        return True

    # Review closed, but can_view_pages_after_review allows continued access.
    if review.status in ("CLOSED", "COMPLETED"):
        return perms.get("can_view_pages_after_review", False)

    return False


def _teacher_access(instance, user, permission: str) -> bool:
    """Access control for teachers/coordinators."""
    from apps.subjects.models.subjects import MembershipRole, SubjectMembership

    subject = instance.exam.subject
    membership = SubjectMembership.objects.filter(
        user=user,
        subject=subject,
        is_active=True,
    ).first()
    if not membership:
        return False

    # Coordinators see all.
    if membership.role == MembershipRole.COORDINATOR:
        return True

    effective = membership.get_effective_permissions()
    if effective.get("can_view_all_instances", False):
        return True

    # Otherwise, check if the user is an assigned corrector for this instance.
    return can_user_grade_instance(user, instance, None)
