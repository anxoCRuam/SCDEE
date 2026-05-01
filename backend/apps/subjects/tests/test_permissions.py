"""
Service-level tests for SubjectMembership permissions (RF-5.2, RF-5.3,
RF-5.6, RF-15.5).

The endpoint-level test_subjects.py covers the basic happy paths via
HTTP. This file focuses on the service contract:

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

from apps.courses.models import AcademicCourse
from apps.organizations.models import Organization, OrganizationConfig
from apps.subjects.models import (
    DEFAULT_PERMISSIONS,
    MembershipRole,
    Subject,
    SubjectMembership,
    get_default_permissions,
)
from apps.subjects.services.subject_service import (
    SubjectServiceError,
    update_permissions,
)

pytestmark = pytest.mark.django_db


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
    course = AcademicCourse(organization=org, label="2026", is_active=True, status="ACTIVE")
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
        assert result["can_grade"] is True
        assert result["can_publish_grades"] is False

    def test_partial_org_override_leaves_other_keys_intact(self, setup):
        config, _ = OrganizationConfig.objects.get_or_create(organization=setup["org"])
        config.role_permission_defaults = {
            MembershipRole.TEACHER: {"can_publish_grades": True},
        }
        config.save()

        result = get_default_permissions(MembershipRole.TEACHER, organization=setup["org"])
        assert result["can_publish_grades"] is True
        assert result["can_grade"] == DEFAULT_PERMISSIONS[MembershipRole.TEACHER]["can_grade"]


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
            "can_grade": True,
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
                overrides={"can_grade": True},
                grantor_permissions={"can_grade": True},
            )
        assert exc_info.value.code == "STUDENT_PERMS_FIXED"
