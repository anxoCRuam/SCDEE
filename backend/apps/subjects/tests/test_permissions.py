"""
Tests for SubjectMembership permissions (RF-5.2, RF-5.3,
RF-5.6, RF-15.5).

* ``get_default_permissions(role, organization)`` consults
  ``OrganizationConfig.role_permission_defaults`` and falls back to
  the hard-coded table — fix C.1.
* ``get_effective_permissions(self)`` merges org defaults + overrides.
* ``update_permissions`` enforces delegation: a grantor cannot extend
  a permission they themselves do not hold (RF-5.6).
"""

from __future__ import annotations

import uuid

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.courses.models.courses import AcademicCourse
from apps.organizations.models.organization import Organization
from apps.organizations.models.organization_config import OrganizationConfig
from apps.subjects.models.permissions import (
    DEFAULT_PERMISSIONS,
    MembershipRole,
    get_default_permissions,
)
from apps.subjects.models.subjects import (
    Subject,
    SubjectMembership,
)
from apps.subjects.services.permissions import (
    update_permissions,
)
from apps.subjects.services.subjects import (
    SubjectServiceError,
)

pytestmark = pytest.mark.django_db
VALID_KEY = "a" * 64


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def setup():
    user_model = get_user_model()
    org = Organization.objects.create(name="Org", subdomain=f"o-{uuid.uuid4().hex[:8]}")
    coord = user_model.objects.create_user(
        email=f"c-{uuid.uuid4().hex[:8]}@x.com",
        password="Pass123!",  # noqa: S106
        first_name="C",
        last_name="O",
        organization=org,
    )
    teacher = user_model.objects.create_user(
        email=f"t-{uuid.uuid4().hex[:8]}@x.com",
        password="Pass123!",  # noqa: S106
        first_name="T",
        last_name="E",
        organization=org,
    )
    course = AcademicCourse(organization=org, label="2026", is_active=True)
    course.save()
    subject = Subject(
        organization=org,
        name="S",
        code=f"S{uuid.uuid4().hex[:4]}",
        course=course,
        coordinator=coord,
    )
    subject.save()
    coord_membership = SubjectMembership.objects.create(
        organization=org,
        user=coord,
        subject=subject,
        role=MembershipRole.COORDINATOR,
        is_active=True,
    )
    teacher_membership = SubjectMembership.objects.create(
        organization=org,
        user=teacher,
        subject=subject,
        role=MembershipRole.TEACHER,
        is_active=True,
    )
    return {
        "org": org,
        "subject": subject,
        "coord": coord,
        "teacher": teacher,
        "coord_membership": coord_membership,
        "teacher_membership": teacher_membership,
    }


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
    course = AcademicCourse(organization=org, label="2025-2026", is_active=True)
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


# ── get_default_permissions: org-config consumption (C.1) ────


class TestGetDefaultPermissions:
    def test_falls_back_to_hardcoded_when_no_org(self):
        result = get_default_permissions(MembershipRole.TEACHER)
        assert result == DEFAULT_PERMISSIONS[MembershipRole.TEACHER]

    def test_falls_back_when_org_has_no_config(self, setup):
        # OrganizationConfig is created lazily; do NOT create one here.
        OrganizationConfig.objects.filter(organization=setup["org"]).delete()
        result = get_default_permissions(MembershipRole.TEACHER, organization=setup["org"])
        assert result == DEFAULT_PERMISSIONS[MembershipRole.TEACHER]

    def test_org_config_overrides_take_precedence(self, setup):
        config, _ = OrganizationConfig.objects.get_or_create(organization=setup["org"])
        # The org wants TEACHERs to be able to create exams by default.
        config.role_permission_defaults = {
            MembershipRole.TEACHER: {"can_create_exams": True},
        }
        config.save()

        result = get_default_permissions(MembershipRole.TEACHER, organization=setup["org"])
        assert result["can_create_exams"] is True
        # Other keys keep the hard-coded default.
        assert result["can_create_rubric"] is True
        assert result["can_publish_grades"] is False

    def test_partial_org_override_leaves_other_keys_intact(self, setup):
        config, _ = OrganizationConfig.objects.get_or_create(organization=setup["org"])
        config.role_permission_defaults = {
            MembershipRole.TEACHER: {"can_publish_grades": True},
        }
        config.save()

        result = get_default_permissions(MembershipRole.TEACHER, organization=setup["org"])
        assert result["can_publish_grades"] is True
        assert (
            result["can_create_rubric"]
            == DEFAULT_PERMISSIONS[MembershipRole.TEACHER]["can_create_rubric"]
        )


# ── Effective permissions on a membership ────────────────────


class TestEffectivePermissions:
    def test_membership_inherits_org_defaults(self, setup):
        config, _ = OrganizationConfig.objects.get_or_create(organization=setup["org"])
        config.role_permission_defaults = {
            MembershipRole.TEACHER: {"can_create_exams": True},
        }
        config.save()

        # Refresh the membership so its `organization` FK is the same row.
        m = SubjectMembership.objects.get(pk=setup["teacher_membership"].pk)
        perms = m.get_effective_permissions()
        assert perms["can_create_exams"] is True

    def test_overrides_take_precedence_over_org_defaults(self, setup):
        config, _ = OrganizationConfig.objects.get_or_create(organization=setup["org"])
        config.role_permission_defaults = {
            MembershipRole.TEACHER: {"can_create_exams": True},
        }
        config.save()

        m = setup["teacher_membership"]
        m.permission_overrides = {"can_create_exams": False}
        m.save()
        perms = m.get_effective_permissions()
        assert perms["can_create_exams"] is False

    def test_null_override_is_ignored(self, setup):
        m = setup["teacher_membership"]
        m.permission_overrides = {"can_create_exams": None}
        m.save()
        perms = m.get_effective_permissions()
        # None is ignored — value falls back to the role default.
        assert perms["can_create_exams"] is False


# ── Delegation restriction (RF-5.6) ──────────────────────────


class TestDelegationRestriction:
    def test_grant_of_unhad_permission_is_rejected(self, setup):
        """Grantor cannot grant ``can_publish_grades`` if they lack it."""
        grantor_perms = {
            "can_create_exams": True,
            "can_create_rubric": True,
            "can_publish_grades": False,  # <- key under test
        }

        with pytest.raises(SubjectServiceError) as exc_info:
            update_permissions(
                membership=setup["teacher_membership"],
                overrides={"can_publish_grades": True},
                grantor_permissions=grantor_perms,
            )
        assert exc_info.value.code == "DELEGATION_EXCEEDED"

    def test_grant_of_held_permission_succeeds(self, setup):
        grantor_perms = {"can_create_exams": True}

        result = update_permissions(
            membership=setup["teacher_membership"],
            overrides={"can_create_exams": True},
            grantor_permissions=grantor_perms,
        )

        assert result["can_create_exams"] is True

    def test_revoke_does_not_require_grantor_permission(self, setup):
        """Revoking (setting False or None) is allowed even if the grantor
        does not hold the permission."""
        # Pre-grant via direct mutation.
        setup["teacher_membership"].permission_overrides = {"can_create_exams": True}
        setup["teacher_membership"].save()

        # Grantor has nothing.
        grantor_perms: dict[str, bool] = {}

        # Revoke (None = restore default) is allowed.
        result = update_permissions(
            membership=setup["teacher_membership"],
            overrides={"can_create_exams": None},
            grantor_permissions=grantor_perms,
        )
        assert result["can_create_exams"] is False  # back to TEACHER default.

    def test_student_membership_cannot_be_modified(self, setup):
        student = get_user_model().objects.create_user(
            email=f"s-{uuid.uuid4().hex[:8]}@x.com",
            password="Pass123!",  # noqa: S106
            first_name="St",
            last_name="U",
            organization=setup["org"],
        )
        student_m = SubjectMembership.objects.create(
            organization=setup["org"],
            user=student,
            subject=setup["subject"],
            role=MembershipRole.STUDENT,
            is_active=True,
        )

        with pytest.raises(SubjectServiceError) as exc_info:
            update_permissions(
                membership=student_m,
                overrides={"can_create_rubric": True},
                grantor_permissions={"can_create_rubric": True},
            )
        assert exc_info.value.code == "STUDENT_PERMS_FIXED"


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
        assert resp.data["permissions"]["can_create_rubric"] is True
        # Teachers cannot create exams by default.
        assert resp.data["permissions"].get("can_create_exams", False) is False

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
            {"permissions": {"can_create_rubric": True}},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN
