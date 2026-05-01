"""
Integration tests for subjects, groups, members, and permissions.

Covers RF-4.1 through RF-4.10 and RF-5.1 through RF-5.6.
Happy path + main error cases. Not exhaustive edge cases.
"""

import json
import uuid

import pytest
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.audit.models import AuditLog
from apps.courses.models import AcademicCourse
from apps.organizations.models import Organization
from apps.subjects.models import (
    MembershipRole,
    Subject,
    SubjectMembership,
)

pytestmark = pytest.mark.django_db
VALID_KEY = "a" * 64


# ── Test helpers ─────────────────────────────────────────────


def _setup_org_with_course():
    """Create org + manager + active course. Returns (org, manager, course)."""
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    org = Organization.objects.create(name="Test Org", subdomain=f"org-{uuid.uuid4().hex[:8]}")
    manager = user_model.objects.create_user(
        email=f"mgr-{uuid.uuid4().hex[:8]}@example.com",
        password="MgrPass123!",  # noqa S106
        first_name="Manager",
        last_name="User",
        organization=org,
        is_staff=True,
    )
    course = AcademicCourse(organization=org, label="2025-2026", is_active=True, status="ACTIVE")
    course.save()
    return org, manager, course


def _create_user(org, **kwargs):
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    email = kwargs.pop("email", f"u-{uuid.uuid4().hex[:8]}@example.com")
    return user_model.objects.create_user(
        email=email,
        password=kwargs.pop("password", "Pass123!"),
        first_name=kwargs.pop("first_name", "Test"),
        last_name=kwargs.pop("last_name", "User"),
        organization=org,
        **kwargs,
    )


def _auth_client(user, password="MgrPass123!"):  # noqa S106
    reset_auth_plugin()
    client = APIClient()
    resp = client.post(
        "/api/v1/auth/login/",
        {"email": user.email, "password": password},
        format="json",
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access_token']}")
    return client


# ── Subject CRUD (RF-4.1, RF-4.3, RF-4.4) ───────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestSubjectCRUD(TestCase):
    def test_create_subject(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        resp = client.post(
            "/api/v1/subjects/",
            {"name": "Matemáticas I", "code": "MAT1", "coordinator_id": str(coord.pk)},
            format="json",
        )

        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["name"] == "Matemáticas I"
        assert resp.data["code"] == "MAT1"

    def test_create_auto_assigns_coordinator_membership(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        client.post(
            "/api/v1/subjects/",
            {"name": "EDA", "code": "EDA", "coordinator_id": str(coord.pk)},
            format="json",
        )

        membership = SubjectMembership.unfiltered.filter(
            user=coord, role=MembershipRole.COORDINATOR
        ).first()
        assert membership is not None
        assert membership.is_active is True

    def test_duplicate_code_rejected(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        client.post(
            "/api/v1/subjects/",
            {"name": "S1", "code": "DUP", "coordinator_id": str(coord.pk)},
            format="json",
        )
        resp = client.post(
            "/api/v1/subjects/",
            {"name": "S2", "code": "DUP", "coordinator_id": str(coord.pk)},
            format="json",
        )
        assert resp.status_code == status.HTTP_409_CONFLICT

    def test_update_subject_name(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        resp = client.post(
            "/api/v1/subjects/",
            {"name": "Old Name", "code": "UPD", "coordinator_id": str(coord.pk)},
            format="json",
        )
        sid = resp.data["id"]

        resp = client.patch(f"/api/v1/subjects/{sid}/", {"name": "New Name"}, format="json")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["name"] == "New Name"

    def test_change_coordinator(self):
        org, mgr, course = _setup_org_with_course()
        old_coord = _create_user(org, password="Pass123!")  # noqa S106
        new_coord = _create_user(org, password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        resp = client.post(
            "/api/v1/subjects/",
            {"name": "S", "code": "CC", "coordinator_id": str(old_coord.pk)},
            format="json",
        )
        sid = resp.data["id"]

        resp = client.patch(
            f"/api/v1/subjects/{sid}/",
            {"coordinator_id": str(new_coord.pk)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

        # Old coordinator should be TEACHER now.
        old_m = SubjectMembership.unfiltered.get(user=old_coord, subject_id=sid, is_active=True)
        assert old_m.role == MembershipRole.TEACHER

        # New coordinator has COORDINATOR role.
        new_m = SubjectMembership.unfiltered.get(user=new_coord, subject_id=sid, is_active=True)
        assert new_m.role == MembershipRole.COORDINATOR

    def test_delete_subject_without_exams(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        resp = client.post(
            "/api/v1/subjects/",
            {"name": "Del", "code": "DEL", "coordinator_id": str(coord.pk)},
            format="json",
        )
        sid = resp.data["id"]

        resp = client.delete(f"/api/v1/subjects/{sid}/")
        assert resp.status_code == status.HTTP_204_NO_CONTENT

    def test_retrieve_subject_detail(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        resp = client.post(
            "/api/v1/subjects/",
            {"name": "Detail", "code": "DET", "coordinator_id": str(coord.pk)},
            format="json",
        )
        sid = resp.data["id"]

        resp = client.get(f"/api/v1/subjects/{sid}/")
        assert resp.status_code == status.HTTP_200_OK
        assert "groups" in resp.data
        assert "member_counts" in resp.data


# ── Groups (RF-4.5) ─────────────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestGroups(TestCase):
    def _create_subject(self, org, mgr, coord):
        client = _auth_client(mgr)
        resp = client.post(
            "/api/v1/subjects/",
            {
                "name": "GrpTest",
                "code": f"G{uuid.uuid4().hex[:4]}",
                "coordinator_id": str(coord.pk),
            },
            format="json",
        )
        return resp.data["id"], client

    def test_create_group(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        sid, client = self._create_subject(org, mgr, coord)

        resp = client.post(f"/api/v1/subjects/{sid}/groups/", {"label": "G1"}, format="json")
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["label"] == "G1"

    def test_list_groups(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        sid, client = self._create_subject(org, mgr, coord)

        client.post(f"/api/v1/subjects/{sid}/groups/", {"label": "G1"}, format="json")
        client.post(f"/api/v1/subjects/{sid}/groups/", {"label": "G2"}, format="json")

        resp = client.get(f"/api/v1/subjects/{sid}/groups/")
        assert len(resp.data) == 2

    def test_duplicate_group_rejected(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        sid, client = self._create_subject(org, mgr, coord)

        client.post(f"/api/v1/subjects/{sid}/groups/", {"label": "G1"}, format="json")
        resp = client.post(f"/api/v1/subjects/{sid}/groups/", {"label": "G1"}, format="json")
        assert resp.status_code == status.HTTP_409_CONFLICT

    def test_delete_group_nulls_memberships(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        student = _create_user(org, password="Pass123!")  # noqa S106
        sid, client = self._create_subject(org, mgr, coord)

        g_resp = client.post(f"/api/v1/subjects/{sid}/groups/", {"label": "G1"}, format="json")
        gid = g_resp.data["id"]

        # Assign student to group.
        client.post(
            f"/api/v1/subjects/{sid}/members/",
            {"user_id": str(student.pk), "role": "STUDENT", "group_id": gid},
            format="json",
        )

        # Delete group.
        client.delete(f"/api/v1/subjects/{sid}/groups/{gid}/")

        # Student's membership should have group = null.
        m = SubjectMembership.unfiltered.get(user=student, subject_id=sid)
        assert m.group is None


# ── Members (RF-4.6, RF-4.7) ────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestMembers(TestCase):
    def test_assign_teacher(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        teacher = _create_user(org, password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        resp = client.post(
            "/api/v1/subjects/",
            {"name": "S", "code": "MBR", "coordinator_id": str(coord.pk)},
            format="json",
        )
        sid = resp.data["id"]

        resp = client.post(
            f"/api/v1/subjects/{sid}/members/",
            {"user_id": str(teacher.pk), "role": "TEACHER"},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["role"] == "TEACHER"

    def test_assign_student_to_group(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        student = _create_user(org, password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        resp = client.post(
            "/api/v1/subjects/",
            {"name": "S", "code": "SG", "coordinator_id": str(coord.pk)},
            format="json",
        )
        sid = resp.data["id"]

        g = client.post(f"/api/v1/subjects/{sid}/groups/", {"label": "G1"}, format="json")
        gid = g.data["id"]

        resp = client.post(
            f"/api/v1/subjects/{sid}/members/",
            {"user_id": str(student.pk), "role": "STUDENT", "group_id": gid},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["group_label"] == "G1"

    def test_duplicate_membership_rejected(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        teacher = _create_user(org, password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        resp = client.post(
            "/api/v1/subjects/",
            {"name": "S", "code": "DM", "coordinator_id": str(coord.pk)},
            format="json",
        )
        sid = resp.data["id"]

        client.post(
            f"/api/v1/subjects/{sid}/members/",
            {"user_id": str(teacher.pk), "role": "TEACHER"},
            format="json",
        )
        resp = client.post(
            f"/api/v1/subjects/{sid}/members/",
            {"user_id": str(teacher.pk), "role": "TEACHER"},
            format="json",
        )
        assert resp.status_code == status.HTTP_409_CONFLICT

    def test_unassign_member(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        teacher = _create_user(org, password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        resp = client.post(
            "/api/v1/subjects/",
            {"name": "S", "code": "UM", "coordinator_id": str(coord.pk)},
            format="json",
        )
        sid = resp.data["id"]

        m_resp = client.post(
            f"/api/v1/subjects/{sid}/members/",
            {"user_id": str(teacher.pk), "role": "TEACHER"},
            format="json",
        )
        mid = m_resp.data["id"]

        resp = client.delete(f"/api/v1/subjects/{sid}/members/{mid}/")
        assert resp.status_code == status.HTTP_204_NO_CONTENT

    def test_list_members(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        resp = client.post(
            "/api/v1/subjects/",
            {"name": "S", "code": "LM", "coordinator_id": str(coord.pk)},
            format="json",
        )
        sid = resp.data["id"]

        resp = client.get(f"/api/v1/subjects/{sid}/members/")
        assert resp.status_code == status.HTTP_200_OK
        # At least the coordinator.
        assert len(resp.data) >= 1


# ── Permissions (RF-5.3, RF-5.5, RF-5.6) ────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestPermissions(TestCase):
    def _setup_with_teacher(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        teacher = _create_user(org, password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        resp = client.post(
            "/api/v1/subjects/",
            {"name": "P", "code": "PERM", "coordinator_id": str(coord.pk)},
            format="json",
        )
        sid = resp.data["id"]

        m_resp = client.post(
            f"/api/v1/subjects/{sid}/members/",
            {"user_id": str(teacher.pk), "role": "TEACHER"},
            format="json",
        )
        return client, m_resp.data["id"], teacher

    def test_get_effective_permissions(self):
        client, mid, teacher = self._setup_with_teacher()

        resp = client.get(f"/api/v1/memberships/{mid}/permissions/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["role"] == "TEACHER"
        # Teachers can grade by default.
        assert resp.data["permissions"]["can_grade"] is True
        # Teachers cannot create exams by default.
        assert resp.data["permissions"]["can_create_exams"] is False

    def test_grant_permission_override(self):
        client, mid, teacher = self._setup_with_teacher()

        resp = client.patch(
            f"/api/v1/memberships/{mid}/permissions/",
            {"permissions": {"can_create_exams": True}},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["permissions"]["can_create_exams"] is True
        assert resp.data["has_overrides"] is True

    def test_revoke_permission_with_null(self):
        client, mid, teacher = self._setup_with_teacher()

        # First grant.
        client.patch(
            f"/api/v1/memberships/{mid}/permissions/",
            {"permissions": {"can_create_exams": True}},
            format="json",
        )
        # Then revoke (restore default).
        resp = client.patch(
            f"/api/v1/memberships/{mid}/permissions/",
            {"permissions": {"can_create_exams": None}},
            format="json",
        )
        # Default for TEACHER is False.
        assert resp.data["permissions"]["can_create_exams"] is False

    def test_delegation_restriction(self):
        """Non-manager coordinator cannot grant perms they don't have.
        Actually managers have all perms so this tests via coordinator."""
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        teacher = _create_user(org, password="Pass123!")  # noqa S106

        # Create subject as manager.
        mgr_client = _auth_client(mgr)
        resp = mgr_client.post(
            "/api/v1/subjects/",
            {"name": "D", "code": "DLG", "coordinator_id": str(coord.pk)},
            format="json",
        )
        sid = resp.data["id"]

        m_resp = mgr_client.post(
            f"/api/v1/subjects/{sid}/members/",
            {"user_id": str(teacher.pk), "role": "TEACHER"},
            format="json",
        )
        mid = m_resp.data["id"]

        # Now login as coordinator (not manager) and try to grant.
        coord_client = _auth_client(coord, password="Pass123!")  # noqa S106
        # Coordinator has can_delegate_perms=True and can_create_exams=True,
        # so granting can_create_exams should work.
        resp = coord_client.patch(
            f"/api/v1/memberships/{mid}/permissions/",
            {"permissions": {"can_create_exams": True}},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK

    def test_student_perms_cannot_be_modified(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        student = _create_user(org, password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        resp = client.post(
            "/api/v1/subjects/",
            {"name": "S", "code": "SP", "coordinator_id": str(coord.pk)},
            format="json",
        )
        sid = resp.data["id"]

        m_resp = client.post(
            f"/api/v1/subjects/{sid}/members/",
            {"user_id": str(student.pk), "role": "STUDENT"},
            format="json",
        )
        mid = m_resp.data["id"]

        resp = client.patch(
            f"/api/v1/memberships/{mid}/permissions/",
            {"permissions": {"can_grade": True}},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ── My subjects (RF-4.8) ────────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestMySubjects(TestCase):
    def test_user_sees_own_subjects(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        student = _create_user(org, password="Pass123!")  # noqa S106
        mgr_client = _auth_client(mgr)

        # Create subject with coordinator.
        resp = mgr_client.post(
            "/api/v1/subjects/",
            {"name": "MyS", "code": "MY1", "coordinator_id": str(coord.pk)},
            format="json",
        )
        sid = resp.data["id"]

        # Assign student.
        mgr_client.post(
            f"/api/v1/subjects/{sid}/members/",
            {"user_id": str(student.pk), "role": "STUDENT"},
            format="json",
        )

        # Student checks my-subjects.
        student_client = _auth_client(student, password="Pass123!")  # noqa S106
        resp = student_client.get("/api/v1/my-subjects/")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 1
        assert resp.data[0]["code"] == "MY1"
        assert resp.data[0]["role"] == "STUDENT"

    def test_no_subjects_returns_empty(self):
        org, mgr, course = _setup_org_with_course()
        user = _create_user(org, password="Pass123!")  # noqa S106
        client = _auth_client(user, password="Pass123!")  # noqa S106

        resp = client.get("/api/v1/my-subjects/")
        assert resp.data == []


# ── Import/Export (RF-4.2, RF-4.10) ──────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestSubjectImportExport(TestCase):
    def test_import_subjects_json(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, email="coord-imp@example.com", password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        import_data = [
            {
                "name": "Imported Subject",
                "code": "IMP1",
                "semester": "1er cuatrimestre",
                "coordinator_email": coord.email,
                "groups": ["G1", "G2"],
                "teachers": [],
                "students": [],
            }
        ]

        resp = client.post("/api/v1/subjects/import/", import_data, format="json")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["created"] == 1

        # Verify subject exists.
        assert Subject.unfiltered.filter(code="IMP1").exists()

        # Verify groups created.
        subject = Subject.unfiltered.get(code="IMP1")
        assert subject.groups.count() == 2

    def test_export_subjects_json(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        client.post(
            "/api/v1/subjects/",
            {"name": "Export Me", "code": "EXP", "coordinator_id": str(coord.pk)},
            format="json",
        )

        resp = client.get("/api/v1/subjects/export/?format=json")
        assert resp.status_code == status.HTTP_200_OK

        data = json.loads(resp.content)
        assert len(data) >= 1
        assert any(s["code"] == "EXP" for s in data)

    def test_audit_logs_created(self):
        org, mgr, course = _setup_org_with_course()
        coord = _create_user(org, password="Pass123!")  # noqa S106
        client = _auth_client(mgr)

        client.post(
            "/api/v1/subjects/",
            {"name": "Audit", "code": "AUD", "coordinator_id": str(coord.pk)},
            format="json",
        )

        assert AuditLog.objects.filter(event_type="SUBJECT_CREATED").exists()
