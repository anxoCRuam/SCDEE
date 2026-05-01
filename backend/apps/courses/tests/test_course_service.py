"""
Unit tests for course service business logic.

Tests the service layer directly without HTTP:
1. Course creation with automatic transition.
2. User deactivation during transition.
3. Manager preservation during transition.
4. Label uniqueness enforcement.
5. Update validation on archived courses.
"""

import uuid

import pytest
from django.test import TestCase, override_settings

from apps.courses.models import AcademicCourse, CourseStatus
from apps.courses.services import (
    CourseServiceError,
    create_course,
    update_course,
)
from apps.organizations.models import Organization

pytestmark = pytest.mark.django_db

VALID_KEY = "a" * 64


def _create_org():
    return Organization.objects.create(
        name="Test Org",
        subdomain=f"svc-{uuid.uuid4().hex[:8]}",
    )


def _create_user(org, is_staff=False):
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    return user_model.objects.create_user(
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        password="Pass123!",  # noqa: S106
        first_name="Test",
        last_name="User",
        organization=org,
        is_staff=is_staff,
    )


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestCreateCourse(TestCase):
    """Test create_course() service function."""

    def test_creates_active_course(self):
        org = _create_org()
        course, report = create_course(organization=org, label="2025-2026")

        assert course.is_active is True
        assert course.status == CourseStatus.ACTIVE
        assert course.label == "2025-2026"
        assert course.organization == org

    def test_no_transition_on_first_course(self):
        """First course has no transition report."""
        org = _create_org()
        _, report = create_course(organization=org, label="2025-2026")

        assert report == {}

    def test_transition_archives_previous(self):
        org = _create_org()
        create_course(organization=org, label="2024-2025")
        _, report = create_course(organization=org, label="2025-2026")

        old = AcademicCourse.unfiltered.get(organization=org, label="2024-2025")
        assert old.is_active is False
        assert old.status == CourseStatus.ARCHIVED
        assert report["previous_course_label"] == "2024-2025"

    def test_transition_deactivates_regular_users(self):
        org = _create_org()
        regular = _create_user(org, is_staff=False)

        create_course(organization=org, label="2024-2025")
        _, report = create_course(organization=org, label="2025-2026")

        regular.refresh_from_db()
        assert regular.is_active is False
        assert report["users_deactivated"] >= 1

    def test_transition_preserves_managers(self):
        org = _create_org()
        manager = _create_user(org, is_staff=True)

        create_course(organization=org, label="2024-2025")
        create_course(organization=org, label="2025-2026")

        manager.refresh_from_db()
        assert manager.is_active is True

    def test_duplicate_label_raises(self):
        org = _create_org()
        create_course(organization=org, label="2025-2026")

        with pytest.raises(CourseServiceError) as exc_info:
            create_course(organization=org, label="2025-2026")

        assert exc_info.value.code == "LABEL_ALREADY_EXISTS"

    def test_same_label_different_orgs_ok(self):
        """Same label in different orgs is fine."""
        org1 = _create_org()
        org2 = _create_org()

        course1, _ = create_course(organization=org1, label="2025-2026")
        course2, _ = create_course(organization=org2, label="2025-2026")

        assert course1.pk != course2.pk

    def test_with_dates(self):
        """Informational dates are stored correctly."""
        from datetime import date

        org = _create_org()
        course, _ = create_course(
            organization=org,
            label="2025-2026",
            start_date=date(2025, 9, 1),
            end_date=date(2026, 6, 30),
        )

        assert course.start_date == date(2025, 9, 1)
        assert course.end_date == date(2026, 6, 30)

    def test_three_transitions(self):
        """Multiple sequential transitions work correctly."""
        org = _create_org()
        create_course(organization=org, label="2022-2023")
        create_course(organization=org, label="2023-2024")
        create_course(organization=org, label="2024-2025")

        active = AcademicCourse.unfiltered.filter(organization=org, is_active=True)
        archived = AcademicCourse.unfiltered.filter(organization=org, is_active=False)

        assert active.count() == 1
        assert active.first().label == "2024-2025"
        assert archived.count() == 2


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestTransitionCascade(TestCase):
    """The transition must archive memberships, notifications and
    non-finalized exam instances of the previous course (RF-3.3)."""

    def _make_subject_with_membership(self, org, course, user):
        from apps.subjects.models import (
            MembershipRole,
            Subject,
            SubjectMembership,
        )

        subject = Subject(
            organization=org,
            name="S",
            code=f"S{uuid.uuid4().hex[:4]}",
            course=course,
            coordinator=user,
        )
        subject.save()
        membership = SubjectMembership.objects.create(
            organization=org,
            user=user,
            subject=subject,
            role=MembershipRole.COORDINATOR,
            is_active=True,
        )
        return subject, membership

    def test_memberships_deactivated_on_transition(self):
        from apps.subjects.models import SubjectMembership

        org = _create_org()
        user = _create_user(org)
        first, _ = create_course(organization=org, label="2024-2025")
        _, membership = self._make_subject_with_membership(org, first, user)
        assert membership.is_active is True

        _, report = create_course(organization=org, label="2025-2026")

        membership.refresh_from_db()
        assert membership.is_active is False
        assert report["memberships_deactivated"] >= 1
        # The unfiltered manager must still see the row (soft delete only).
        assert SubjectMembership.unfiltered.filter(pk=membership.pk).exists()

    def test_notifications_archived_on_transition(self):
        from apps.notifications.models import (
            Notification,
            NotificationStatus,
            NotificationType,
        )

        org = _create_org()
        user = _create_user(org, is_staff=True)
        first, _ = create_course(organization=org, label="2024-2025")
        # Unread notification scoped to the course about to be archived.
        notif = Notification.objects.create(
            user=user,
            notification_type=NotificationType.GENERAL,
            title="t",
            message="m",
            status=NotificationStatus.UNREAD,
            course=first,
        )

        _, report = create_course(organization=org, label="2025-2026")

        notif.refresh_from_db()
        assert notif.status == NotificationStatus.ARCHIVED
        assert report["notifications_archived"] >= 1

    def test_non_finalized_instances_archived(self):
        from decimal import Decimal

        from apps.exams.models import Exam, ExamModel, Problem
        from apps.instances.models import ExamInstance, InstanceStatus

        org = _create_org()
        user = _create_user(org, is_staff=True)
        first, _ = create_course(organization=org, label="2024-2025")
        subject, _ = self._make_subject_with_membership(org, first, user)

        exam = Exam.objects.create(organization=org, name="E", subject=subject)
        model = ExamModel.objects.create(label="A", exam=exam)
        Problem.objects.create(name="P", max_score=Decimal("5"), exam_model=model, order=0)
        # Two instances: one in flight, one already finalized.
        in_flight = ExamInstance.objects.create(
            organization=org,
            exam=exam,
            model=model,
            status=InstanceStatus.GRADED,
            expected_pages=1,
        )
        finalized = ExamInstance.objects.create(
            organization=org,
            exam=exam,
            model=model,
            status=InstanceStatus.FINALIZED,
            expected_pages=1,
        )

        _, report = create_course(organization=org, label="2025-2026")

        in_flight.refresh_from_db()
        finalized.refresh_from_db()
        assert in_flight.status == InstanceStatus.ARCHIVED
        assert finalized.status == InstanceStatus.ARCHIVED
        assert report["instances_archived"] >= 1


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestUpdateCourse(TestCase):
    """Test update_course() service function."""

    def test_update_label(self):
        org = _create_org()
        course, _ = create_course(organization=org, label="Old Label")

        changes = update_course(course, data={"label": "New Label"})

        assert "label" in changes
        assert changes["label"]["old"] == "Old Label"
        assert changes["label"]["new"] == "New Label"
        course.refresh_from_db()
        assert course.label == "New Label"

    def test_no_changes_returns_empty(self):
        org = _create_org()
        course, _ = create_course(organization=org, label="Same")

        changes = update_course(course, data={"label": "Same"})

        assert changes == {}

    def test_update_archived_raises(self):
        """Updating an archived course raises CourseServiceError."""
        org = _create_org()
        create_course(organization=org, label="2024-2025")
        create_course(organization=org, label="2025-2026")

        archived = AcademicCourse.unfiltered.get(organization=org, label="2024-2025")

        with pytest.raises(CourseServiceError) as exc_info:
            update_course(archived, data={"label": "Modified"})

        assert exc_info.value.code == "COURSE_ARCHIVED"
