"""
Integration tests for academic course management (RF-3.1 through RF-3.5).

Tests verify:
1. Managers can create courses (RF-3.1).
2. Creating a new course archives the previous one (RF-3.3).
3. Transition deactivates non-manager users (RF-3.3).
4. Transition preserves manager users (RF-2.6).
5. Managers can list courses with active/archived differentiation (RF-3.4).
6. Managers can update active courses (RF-3.2).
7. Writes on archived courses are blocked with 403 (RF-3.5).
8. Duplicate labels within same org are rejected.
9. Audit log entries are created for creation and transition.
10. Tenant isolation: managers only see their org's courses.
"""

import uuid

import pytest
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.audit.models import AuditLog
from apps.courses.models import AcademicCourse, CourseStatus
from apps.organizations.models import Organization

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
        assert response.data["status"] == "ACTIVE"

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
        assert old_course.status == CourseStatus.ARCHIVED

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


# ── Course listing tests (RF-3.4) ───────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestCourseListing(TestCase):
    """GET /api/v1/courses/ (RF-3.4)."""

    def test_list_all_courses(self):
        """Returns all courses (active + archived)."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        client.post("/api/v1/courses/", {"label": "2023-2024"}, format="json")
        client.post("/api/v1/courses/", {"label": "2024-2025"}, format="json")
        client.post("/api/v1/courses/", {"label": "2025-2026"}, format="json")

        response = client.get("/api/v1/courses/")

        assert response.status_code == status.HTTP_200_OK
        assert len(response.data) == 3

    def test_active_course_listed_first(self):
        """Active course appears before archived ones."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        client.post("/api/v1/courses/", {"label": "2023-2024"}, format="json")
        client.post("/api/v1/courses/", {"label": "2024-2025"}, format="json")

        response = client.get("/api/v1/courses/")

        assert response.data[0]["is_active"] is True
        assert response.data[0]["label"] == "2024-2025"

    def test_filter_by_active_status(self):
        """?is_active=true returns only the active course."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        client.post("/api/v1/courses/", {"label": "2023-2024"}, format="json")
        client.post("/api/v1/courses/", {"label": "2024-2025"}, format="json")

        response = client.get("/api/v1/courses/?is_active=true")

        assert len(response.data) == 1
        assert response.data[0]["is_active"] is True

    def test_filter_archived(self):
        """?is_active=false returns only archived courses."""
        org = _create_org()
        manager = _create_manager(org)
        client = _get_auth_client(manager)

        client.post("/api/v1/courses/", {"label": "2023-2024"}, format="json")
        client.post("/api/v1/courses/", {"label": "2024-2025"}, format="json")

        response = client.get("/api/v1/courses/?is_active=false")

        assert len(response.data) == 1
        assert response.data[0]["is_active"] is False

    def test_tenant_isolation(self):
        """Manager only sees courses from their organization."""
        org1 = _create_org("Org 1")
        org2 = _create_org("Org 2")
        manager1 = _create_manager(org1)
        manager2 = _create_manager(org2)

        # Create courses in both orgs.
        client1 = _get_auth_client(manager1)
        client1.post("/api/v1/courses/", {"label": "Org1-2025"}, format="json")

        client2 = _get_auth_client(manager2)
        client2.post("/api/v1/courses/", {"label": "Org2-2025"}, format="json")

        # Manager1 should only see Org1's course.
        reset_auth_plugin()
        client1 = _get_auth_client(manager1)
        response = client1.get("/api/v1/courses/")

        assert len(response.data) == 1
        assert response.data[0]["label"] == "Org1-2025"


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
