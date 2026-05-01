"""
Course management business logic.

The most important operation here is the course transition (RF-3.3):
when a new course is created, the previous active course must be
archived atomically. This involves:

1. Archiving the previous course (is_active=False, status=ARCHIVED).
2. Deactivating all non-manager users (is_active=False).
3. Deactivating SubjectMemberships of the archived course.
4. Archiving notifications of the archived course.
5. Archiving all non-finalized exam instances.

Why a service instead of model signals?
    The transition is too complex for a signal:
    - It spans multiple models across multiple apps.
    - It must be transactional (all or nothing).
    - It needs explicit audit logging with context.
    - Signals hide control flow and make debugging harder.

References: RF-3.1, RF-3.3
"""

from __future__ import annotations

import logging
from typing import Any

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from apps.courses.models import AcademicCourse, CourseStatus
from apps.instances.models import ExamInstance, InstanceStatus
from apps.notifications.models import Notification, NotificationStatus
from apps.subjects.models import SubjectMembership

logger = logging.getLogger(__name__)

User = get_user_model()


class CourseServiceError(Exception):
    """Raised when a course operation violates business rules."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(detail or code)


def create_course(
    *,
    organization,
    label: str,
    start_date=None,
    end_date=None,
) -> tuple[AcademicCourse, dict[str, Any]]:
    """Create a new active course, transitioning the previous one.

    This is the single entry point for course creation. It handles:
    1. Validate label uniqueness within the organization.
    2. If a previous active course exists, run the full transition.
    3. Create the new course as ACTIVE.

    The entire operation is wrapped in a transaction so if any step
    fails, nothing changes.

    Args:
        organization: The Organization instance.
        label: Human-readable label (e.g. "2025-2026").
        start_date: Optional informational start date.
        end_date: Optional informational end date.

    Returns:
        Tuple of (new_course, transition_report).
        transition_report is a dict with counts of affected entities,
        or empty dict if no transition occurred.

    Raises:
        CourseServiceError: If the label already exists.
    """
    transition_report: dict[str, Any] = {}

    try:
        with transaction.atomic():
            # Check for existing active course in this organization.
            previous_course = (
                AcademicCourse.unfiltered.filter(organization=organization, is_active=True)
                .select_for_update()
                .first()
            )

            if previous_course is not None:
                transition_report = _execute_transition(
                    organization=organization,
                    previous_course=previous_course,
                )

            # Create the new course.
            new_course = AcademicCourse(
                organization=organization,
                label=label,
                start_date=start_date,
                end_date=end_date,
                is_active=True,
                status=CourseStatus.ACTIVE,
            )
            new_course.save()

    except IntegrityError as exc:
        error_msg = str(exc).lower()
        if "unique_course_label_per_org" in error_msg:
            raise CourseServiceError(
                code="LABEL_ALREADY_EXISTS",
                detail=f"A course with label '{label}' already exists.",
            ) from exc
        raise CourseServiceError(
            code="COURSE_CREATION_FAILED",
            detail=str(exc),
        ) from exc

    return new_course, transition_report


def update_course(
    course: AcademicCourse,
    *,
    data: dict,
) -> dict[str, dict[str, Any]]:
    """Update mutable fields of a course.

    Only allowed on active courses (archived courses are read-only).

    Args:
        course: The AcademicCourse instance to update.
        data: Dict of field names → new values (PATCH semantics).

    Returns:
        Dict of changes: {"field": {"old": ..., "new": ...}}.

    Raises:
        CourseServiceError: If the course is archived.
    """
    if not course.is_active:
        raise CourseServiceError(
            code="COURSE_ARCHIVED",
            detail="Cannot modify an archived course.",
        )

    changes: dict[str, dict[str, Any]] = {}

    for field in ("label", "start_date", "end_date"):
        if field in data:
            old_value = getattr(course, field)
            new_value = data[field]
            if old_value != new_value:
                changes[field] = {"old": str(old_value), "new": str(new_value)}
                setattr(course, field, new_value)

    if changes:
        try:
            course.save()
        except IntegrityError as exc:
            if "unique_course_label_per_org" in str(exc).lower():
                raise CourseServiceError(
                    code="LABEL_ALREADY_EXISTS",
                ) from exc
            raise

    return changes


def _execute_transition(
    *,
    organization,
    previous_course: AcademicCourse,
) -> dict[str, Any]:
    """Execute the full course transition process (RF-3.3).

    All steps run within the caller's transaction.atomic() block.

    Steps:
    1. Archive the previous course.
    2. Deactivate non-manager users.
    3. Deactivate SubjectMemberships of the archived course.
    4. Archive notifications scoped to the archived course.
    5. Archive non-finalized exam instances of the archived course.

    Args:
        organization: The organization undergoing transition.
        previous_course: The currently active course to archive.

    Returns:
        Report dict with counts of affected entities.
    """
    report: dict[str, Any] = {
        "previous_course_label": previous_course.label,
        "users_deactivated": 0,
        "memberships_deactivated": 0,
        "notifications_archived": 0,
        "instances_archived": 0,
    }

    # Step 1: Archive the previous course.
    previous_course.is_active = False
    previous_course.status = CourseStatus.ARCHIVED
    previous_course.save(update_fields=["is_active", "status", "updated_at"])

    # Step 2: Deactivate all non-manager users in the organization.
    # Managers (is_staff=True) are preserved (RF-2.6).
    # Superadmins are also preserved (they don't belong to an org).
    deactivated_count = User.objects.filter(
        organization=organization,
        is_active=True,
        is_staff=False,
    ).update(is_active=False)
    report["users_deactivated"] = deactivated_count

    # Step 3: Deactivate SubjectMemberships for the archived course.
    report["memberships_deactivated"] = _deactivate_memberships(previous_course)

    # Step 4: Archive notifications for the archived course.
    report["notifications_archived"] = _archive_notifications(previous_course)

    # Step 5: Archive non-finalized exam instances.
    report["instances_archived"] = _archive_instances(previous_course)

    logger.info(
        "Course transition completed for org %s: archived '%s', "
        "deactivated %d users, %d memberships, %d instances.",
        organization.pk,
        previous_course.label,
        deactivated_count,
        report["memberships_deactivated"],
        report["instances_archived"],
    )

    return report


def _deactivate_memberships(course: AcademicCourse) -> int:
    """Deactivate SubjectMemberships for subjects in the archived course."""
    return SubjectMembership.unfiltered.filter(
        subject__course=course,
        is_active=True,
    ).update(is_active=False)


def _archive_notifications(course: AcademicCourse) -> int:
    """Archive notifications associated with the archived course."""
    return (
        Notification.objects.filter(course=course)
        .exclude(status=NotificationStatus.ARCHIVED)
        .update(status=NotificationStatus.ARCHIVED)
    )


def _archive_instances(course: AcademicCourse) -> int:
    """Archive non-finalized exam instances from the archived course.

    All instances that are not already FINALIZED or ARCHIVED
    transition to ARCHIVED regardless of their current state.
    """
    return ExamInstance.unfiltered.filter(exam__subject__course=course).update(
        status=InstanceStatus.ARCHIVED
    )
