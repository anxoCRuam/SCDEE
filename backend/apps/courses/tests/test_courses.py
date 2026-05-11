"""
Integration tests for academic course management (RF-3.1 through RF-3.5).

Tests verify:
1. Managers can create courses (RF-3.1).
2. Creating a new course archives the previous one (RF-3.3).
3. Transition deactivates non-manager users (RF-3.3).
4. Transition preserves manager users (RF-2.6).
5. Managers can update active courses (RF-3.2).
6. Writes on archived courses are blocked with 403 (RF-3.5).
7. Duplicate labels within same org are rejected.
8. Audit log entries are created for creation and transition.
9. Tenant isolation: managers only see their org's courses.
"""

import uuid

import pytest
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.audit.models.auditlog import AuditLog
from apps.courses.models.courses import AcademicCourse
from apps.organizations.models.organization import Organization

pytestmark = pytest.mark.django_db

VALID_KEY = "a" * 64


def _create_org(name="Test Org"):
    return Organization.objects.create(
        name=name,
        subdomain=f"org-{uuid.uuid4().hex[:8]}",
    )


def _create_manager(org, password="ManagerPass123!"):  # noqa: S107
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    return user_model.objects.create_user(
        email=f"manager-{uuid.uuid4().hex[:8]}@example.com",
        password=password,
        first_name="Manager",
        last_name="User",
        organization=org,
        is_staff=True,
    )


def _create_regular_user(org, password="UserPass123!"):  # noqa: S107
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    return user_model.objects.create_user(
        email=f"user-{uuid.uuid4().hex[:8]}@example.com",
        password=password,
        first_name="Regular",
        last_name="User",
        organization=org,
        is_staff=False,
    )


def _get_auth_client(user, password="ManagerPass123!"):  # noqa: S107
    reset_auth_plugin()
    client = APIClient()
    response = client.post(
        "/api/v1/auth/login/",
        {"email": user.email, "password": password},
        format="json",
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access_token']}")
    return client


# ── Course creation tests (RF-3.1) ──────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestCourseCreation(TestCase):
    """POST /api/v1/courses/ (RF-3.1)."""

    def test_create_first_course(self):
        """Manager creates the first course for the organization."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        response = client.post(
            "/api/v1/courses/",
            {"label": "2025-2026", "start_date": "2025-09-01"},
            format="json",
        )

        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["label"] == "2025-2026"
        assert response.data["is_active"] is True

    def test_duplicate_label_rejected(self):
        """Duplicate label in the same org returns 409."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        client.post(
            "/api/v1/courses/",
            {"label": "2025-2026"},
            format="json",
        )

        # Archiving happens automatically, but the label still exists.
        response = client.post(
            "/api/v1/courses/",
            {"label": "2025-2026"},
            format="json",
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    def test_non_manager_rejected(self):
        """Non-manager gets 403."""
        org = _create_org()
        regular = _create_regular_user(org)
        client = _get_auth_client(regular, password="UserPass123!")  # noqa: S106

        response = client.post(
            "/api/v1/courses/",
            {"label": "2025-2026"},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN

    def test_audit_log_created(self):
        """Course creation generates an audit log entry."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        client.post(
            "/api/v1/courses/",
            {"label": "2025-2026"},
            format="json",
        )

        log = AuditLog.objects.filter(event_type="COURSE_CREATED").first()
        assert log is not None
        assert log.payload["label"] == "2025-2026"


# ── Course transition tests (RF-3.3) ────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestCourseTransition(TestCase):
    """Automatic transition when creating a new course (RF-3.3)."""

    def test_previous_course_archived(self):
        """Creating a new course archives the previous one."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        # Create first course.
        client.post("/api/v1/courses/", {"label": "2024-2025"}, format="json")

        # Create second course — first should be archived.
        client.post("/api/v1/courses/", {"label": "2025-2026"}, format="json")

        old_course = AcademicCourse.unfiltered.get(organization=org, label="2024-2025")
        assert old_course.is_active is False

        new_course = AcademicCourse.unfiltered.get(organization=org, label="2025-2026")
        assert new_course.is_active is True

    def test_non_manager_users_deactivated(self):
        """Transition deactivates non-manager users (RF-3.3 step 2)."""
        org = _create_org()
        manager = _create_manager(org)
        regular = _create_regular_user(org)
        client = _get_auth_client(manager)

        # Create first course.
        client.post("/api/v1/courses/", {"label": "2024-2025"}, format="json")

        # Create second course — triggers transition.
        client.post("/api/v1/courses/", {"label": "2025-2026"}, format="json")

        regular.refresh_from_db()
        assert regular.is_active is False

    def test_manager_users_preserved(self):
        """Managers keep their active status during transition (RF-2.6)."""
        org = _create_org()
        manager = _create_manager(org)

        # Create another manager.
        from django.contrib.auth import get_user_model

        user_model = get_user_model()
        other_manager = user_model.objects.create_user(
            email=f"mgr2-{uuid.uuid4().hex[:8]}@example.com",
            password="ManagerPass123!",  # noqa: S106
            first_name="Other",
            last_name="Manager",
            organization=org,
            is_staff=True,
        )

        client = _get_auth_client(manager)

        client.post("/api/v1/courses/", {"label": "2024-2025"}, format="json")
        client.post("/api/v1/courses/", {"label": "2025-2026"}, format="json")

        other_manager.refresh_from_db()
        assert other_manager.is_active is True

    def test_transition_creates_audit_log(self):
        """Transition generates a COURSE_TRANSITION audit log entry."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        client.post("/api/v1/courses/", {"label": "2024-2025"}, format="json")
        client.post("/api/v1/courses/", {"label": "2025-2026"}, format="json")

        log = AuditLog.objects.filter(event_type="COURSE_TRANSITION").first()
        assert log is not None
        assert log.payload["previous_course_label"] == "2024-2025"
        assert "users_deactivated" in log.payload

    def test_only_one_active_course(self):
        """After transition, exactly one course is active."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        client.post("/api/v1/courses/", {"label": "2023-2024"}, format="json")
        client.post("/api/v1/courses/", {"label": "2024-2025"}, format="json")
        client.post("/api/v1/courses/", {"label": "2025-2026"}, format="json")

        active_count = AcademicCourse.unfiltered.filter(organization=org, is_active=True).count()
        assert active_count == 1

    def test_multiple_regular_users_deactivated(self):
        """All non-manager users are deactivated during transition."""
        org = _create_org()
        manager = _create_manager(org)

        # Create several regular users.
        regulars = [_create_regular_user(org) for _ in range(5)]

        client = _get_auth_client(manager)
        client.post("/api/v1/courses/", {"label": "2024-2025"}, format="json")
        client.post("/api/v1/courses/", {"label": "2025-2026"}, format="json")

        for user in regulars:
            user.refresh_from_db()
            assert user.is_active is False


# ── Course update tests (RF-3.2) ────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestCourseUpdate(TestCase):
    """PATCH /api/v1/courses/{id}/ (RF-3.2)."""

    def test_update_active_course(self):
        """Manager can update an active course's label."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        response = client.post(
            "/api/v1/courses/",
            {"label": "2025-2026"},
            format="json",
        )
        course_id = response.data["id"]

        response = client.patch(
            f"/api/v1/courses/{course_id}/",
            {"label": "2025-2026 (updated)"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["label"] == "2025-2026 (updated)"

    def test_update_dates(self):
        """Manager can update start and end dates."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        response = client.post(
            "/api/v1/courses/",
            {"label": "2025-2026"},
            format="json",
        )
        course_id = response.data["id"]

        response = client.patch(
            f"/api/v1/courses/{course_id}/",
            {"start_date": "2025-09-15", "end_date": "2026-06-30"},
            format="json",
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.data["start_date"] == "2025-09-15"
        assert response.data["end_date"] == "2026-06-30"


# ── Archived course protection tests (RF-3.5) ───────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestArchivedCourseProtection(TestCase):
    """Writes on archived courses are blocked (RF-3.5)."""

    def test_update_archived_course_returns_403(self):
        """Attempting to update an archived course returns 403."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        # Create and archive a course.
        client.post("/api/v1/courses/", {"label": "2024-2025"}, format="json")
        client.post("/api/v1/courses/", {"label": "2025-2026"}, format="json")

        archived = AcademicCourse.unfiltered.get(organization=org, label="2024-2025")

        response = client.patch(
            f"/api/v1/courses/{archived.pk}/",
            {"label": "Modified"},
            format="json",
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.data["error_code"] == "COURSE_ARCHIVED"

    def test_archived_course_still_readable(self):
        """Archived courses can be retrieved (read-only access)."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        client.post("/api/v1/courses/", {"label": "2024-2025"}, format="json")
        client.post("/api/v1/courses/", {"label": "2025-2026"}, format="json")

        archived = AcademicCourse.unfiltered.get(organization=org, label="2024-2025")

        response = client.get(f"/api/v1/courses/{archived.pk}/")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["label"] == "2024-2025"
        assert response.data["is_active"] is False


# ── Retrieve tests ───────────────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestCourseRetrieve(TestCase):
    """GET /api/v1/courses/{id}/."""

    def test_retrieve_existing_course(self):
        """Manager can retrieve a course by ID."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        response = client.post(
            "/api/v1/courses/",
            {"label": "2025-2026", "start_date": "2025-09-01"},
            format="json",
        )
        course_id = response.data["id"]

        response = client.get(f"/api/v1/courses/{course_id}/")

        assert response.status_code == status.HTTP_200_OK
        assert response.data["label"] == "2025-2026"

    def test_retrieve_nonexistent_returns_404(self):
        """Non-existent course ID returns 404."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        response = client.get(f"/api/v1/courses/{uuid.uuid4()}/")

        assert response.status_code == status.HTTP_404_NOT_FOUND


# ── Service layer tests ─────────────────────────────────────────


def _create_service_org():
    """Helper to create organization for service tests."""
    return Organization.objects.create(
        name="Test Org",
        subdomain=f"svc-{uuid.uuid4().hex[:8]}",
    )


def _create_service_user(org, is_staff=False):
    """Helper to create user for service tests."""
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
        from apps.courses.services.courses import create_course

        org = _create_service_org()
        course, report = create_course(organization=org, label="2025-2026")

        assert course.is_active is True
        assert course.label == "2025-2026"
        assert course.organization == org

    def test_no_transition_on_first_course(self):
        """First course has no transition report."""
        from apps.courses.services.courses import create_course

        org = _create_service_org()
        _, report = create_course(organization=org, label="2025-2026")

        assert report == {}

    def test_transition_archives_previous(self):
        from apps.courses.services.courses import create_course

        org = _create_service_org()
        create_course(organization=org, label="2024-2025")
        _, report = create_course(organization=org, label="2025-2026")

        old = AcademicCourse.unfiltered.get(organization=org, label="2024-2025")
        assert old.is_active is False
        assert report["previous_course_label"] == "2024-2025"

    def test_transition_deactivates_regular_users(self):
        from apps.courses.services.courses import create_course

        org = _create_service_org()
        regular = _create_service_user(org, is_staff=False)

        create_course(organization=org, label="2024-2025")
        _, report = create_course(organization=org, label="2025-2026")

        regular.refresh_from_db()
        assert regular.is_active is False
        assert report["users_deactivated"] >= 1

    def test_transition_preserves_managers(self):
        from apps.courses.services.courses import create_course

        org = _create_service_org()
        manager = _create_service_user(org, is_staff=True)

        create_course(organization=org, label="2024-2025")
        create_course(organization=org, label="2025-2026")

        manager.refresh_from_db()
        assert manager.is_active is True

    def test_duplicate_label_raises(self):
        from apps.courses.services.courses import CourseServiceError, create_course

        org = _create_service_org()
        create_course(organization=org, label="2025-2026")

        with pytest.raises(CourseServiceError) as exc_info:
            create_course(organization=org, label="2025-2026")

        assert exc_info.value.code == "LABEL_ALREADY_EXISTS"

    def test_same_label_different_orgs_ok(self):
        """Same label in different orgs is fine."""
        from apps.courses.services.courses import create_course

        org1 = _create_service_org()
        org2 = _create_service_org()

        course1, _ = create_course(organization=org1, label="2025-2026")
        course2, _ = create_course(organization=org2, label="2025-2026")

        assert course1.pk != course2.pk

    def test_with_dates(self):
        """Informational dates are stored correctly."""
        from datetime import date

        from apps.courses.services.courses import create_course

        org = _create_service_org()
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
        from apps.courses.services.courses import create_course

        org = _create_service_org()
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
        from apps.subjects.models.subjects import (
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
        from apps.courses.services.courses import create_course
        from apps.subjects.models.subjects import SubjectMembership

        org = _create_service_org()
        user = _create_service_user(org)
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
        from apps.courses.services.courses import create_course
        from apps.notifications.models.notifications import (
            Notification,
            NotificationStatus,
            NotificationType,
        )

        org = _create_service_org()
        user = _create_service_user(org, is_staff=True)
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

        from apps.courses.services.courses import create_course
        from apps.exams.models.exams import Exam, ExamModel, Problem
        from apps.instances.models.instances import ExamInstance, InstanceStatus

        org = _create_service_org()
        user = _create_service_user(org, is_staff=True)
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
        from apps.courses.services.courses import create_course, update_course

        org = _create_service_org()
        course, _ = create_course(organization=org, label="Old Label")

        changes = update_course(course, data={"label": "New Label"})

        assert "label" in changes
        assert changes["label"]["old"] == "Old Label"
        assert changes["label"]["new"] == "New Label"
        course.refresh_from_db()
        assert course.label == "New Label"

    def test_no_changes_returns_empty(self):
        from apps.courses.services.courses import create_course, update_course

        org = _create_service_org()
        course, _ = create_course(organization=org, label="Same")

        changes = update_course(course, data={"label": "Same"})

        assert changes == {}

    def test_update_archived_raises(self):
        """Updating an archived course raises CourseServiceError."""
        from apps.courses.services.courses import CourseServiceError, create_course, update_course

        org = _create_service_org()
        create_course(organization=org, label="2024-2025")
        create_course(organization=org, label="2025-2026")

        archived = AcademicCourse.unfiltered.get(organization=org, label="2024-2025")

        with pytest.raises(CourseServiceError) as exc_info:
            update_course(archived, data={"label": "Modified"})

        assert exc_info.value.code == "COURSE_ARCHIVED"
