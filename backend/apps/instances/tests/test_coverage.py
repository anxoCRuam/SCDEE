"""
Integration tests for corrector coverage endpoint (RF-8.5).

Covers:
- Permission checks (can_assign_correctors required).
- Response shape matches CoverageResponseSerializer.
- Detection of uncovered problems.
- Complete coverage reporting.
"""

import uuid
from decimal import Decimal

import pytest
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.courses.models.courses import AcademicCourse
from apps.exams.models.exams import Exam, ExamModel, Problem
from apps.grading.models.grading import AssignmentRule
from apps.instances.models.instances import ExamInstance, InstanceStatus
from apps.organizations.models.organization import Organization
from apps.subjects.models.subjects import MembershipRole, Subject, SubjectMembership

pytestmark = pytest.mark.django_db
VALID_KEY = "a" * 64


def _full_setup():
    """Create org, manager, course, subject, coordinator, exam, model, problem, instance."""
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    org = Organization.objects.create(name="Test Org", subdomain=f"org-{uuid.uuid4().hex[:8]}")
    manager = user_model.objects.create_user(
        email=f"mgr-{uuid.uuid4().hex[:8]}@example.com",
        password="MgrPass123!",  # noqa: S106
        first_name="Manager",
        last_name="User",
        organization=org,
        is_staff=True,
    )
    coordinator = user_model.objects.create_user(
        email=f"coord-{uuid.uuid4().hex[:8]}@example.com",
        password="CoordPass123!",  # noqa: S106
        first_name="Coord",
        last_name="User",
        organization=org,
    )
    course = AcademicCourse.objects.create(organization=org, label="2025-2026", is_active=True)
    subject = Subject.objects.create(
        organization=org,
        name="Coverage Subject",
        code=f"CS{uuid.uuid4().hex[:4]}",
        course=course,
        coordinator=coordinator,
    )
    SubjectMembership.objects.create(
        organization=org,
        user=coordinator,
        subject=subject,
        role=MembershipRole.COORDINATOR,
        is_active=True,
    )
    exam = Exam.objects.create(organization=org, name="Coverage Exam", subject=subject)
    model_a = ExamModel.objects.create(label="A", exam=exam)
    model_b = ExamModel.objects.create(label="B", exam=exam)
    problem_1 = Problem.objects.create(
        name="P1", max_score=Decimal("10.00"), exam_model=model_a, order=1
    )
    problem_2 = Problem.objects.create(
        name="P2", max_score=Decimal("5.00"), exam_model=model_b, order=2
    )
    instance = ExamInstance.objects.create(
        organization=org,
        exam=exam,
        model=model_a,
        status=InstanceStatus.PENDING_GRADING,
        expected_pages=1,
    )
    return {
        "org": org,
        "manager": manager,
        "coordinator": coordinator,
        "subject": subject,
        "exam": exam,
        "model_a": model_a,
        "model_b": model_b,
        "problems": [problem_1, problem_2],
        "instance": instance,
    }


def _auth_client(user, password="MgrPass123!"):  # noqa: S107
    reset_auth_plugin()
    client = APIClient()
    resp = client.post(
        "/api/v1/auth/login/",
        {"email": user.email, "password": password},
        format="json",
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access_token']}")
    return client


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestCoverageEndpoint(TestCase):
    """Tests for GET /exams/{id}/coverage/."""

    def test_permission_denied_without_can_assign_correctors(self):
        """A user without can_assign_correctors gets 403."""
        ctx = _full_setup()
        # coordinator has membership but not the specific permission? By default coordinator
        # has can_assign_correctors=True,
        # so we need a user with no permission. Create a teacher or modify permissions.
        from django.contrib.auth import get_user_model

        user_model = get_user_model()
        teacher = user_model.objects.create_user(
            email=f"t-{uuid.uuid4().hex[:8]}@example.com",
            password="TeachPass123!",  # noqa: S106
            first_name="Teacher",
            last_name="User",
            organization=ctx["org"],
        )
        SubjectMembership.objects.create(
            organization=ctx["org"],
            user=teacher,
            subject=ctx["subject"],
            role=MembershipRole.TEACHER,
            is_active=True,
        )
        client = _auth_client(teacher, password="TeachPass123!")  # noqa: S106
        resp = client.get(f"/api/v1/exams/{ctx['exam'].pk}/coverage/")
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_coverage_with_no_rules_shows_all_uncovered(self):
        """Without any assignment rules, all problems are uncovered."""
        ctx = _full_setup()
        _auth_client(
            ctx["manager"]
        )  # manager is staff, bypasses permission? Actually IsStaff bypasses SubjectPermission?
        # In our permission system, is_staff bypasses. So use coordinator who has the permission.
        # coordinator has can_assign_correctors by default. So:
        coord_client = _auth_client(ctx["coordinator"], password="CoordPass123!")
        resp = coord_client.get(f"/api/v1/exams/{ctx['exam'].pk}/coverage/")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.data
        assert data["complete"] is False
        assert data["total_problems"] == 2
        assert data["covered_problems"] == 0
        assert len(data["uncovered_problems"]) == 2
        # Check shape
        for up in data["uncovered_problems"]:
            assert "problem_id" in up
            assert "problem_name" in up
            assert "model" in up
        assert data["total_instances"] == 1  # instance created
        assert data["rules_count"] == 0

    def test_coverage_with_rule_covers_some_problems(self):
        """Adding a rule covering one problem marks it as covered."""
        ctx = _full_setup()
        # Create rule covering only problem_1
        AssignmentRule.objects.create(
            exam=ctx["exam"],
            problems=[str(ctx["problems"][0].pk)],
            correctors=[str(ctx["coordinator"].pk)],
        )
        coord_client = _auth_client(ctx["coordinator"], password="CoordPass123!")
        resp = coord_client.get(f"/api/v1/exams/{ctx['exam'].pk}/coverage/")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.data
        assert data["complete"] is False
        assert data["covered_problems"] == 1
        assert len(data["uncovered_problems"]) == 1
        # uncovered should be problem_2
        uncovered = data["uncovered_problems"][0]
        assert uncovered["problem_name"] == ctx["problems"][1].name

    def test_coverage_all_covered(self):
        """When rules cover all problems, complete is True."""
        ctx = _full_setup()
        AssignmentRule.objects.create(
            exam=ctx["exam"],
            problems=[str(p.pk) for p in ctx["problems"]],
            correctors=[str(ctx["coordinator"].pk)],
        )
        coord_client = _auth_client(ctx["coordinator"], password="CoordPass123!")
        resp = coord_client.get(f"/api/v1/exams/{ctx['exam'].pk}/coverage/")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.data
        assert data["complete"] is True
        assert data["covered_problems"] == 2
        assert data["uncovered_problems"] == []

    def test_instances_count_excludes_archived(self):
        """total_instances excludes archived instances."""
        ctx = _full_setup()
        # archive the existing instance
        ctx["instance"].status = InstanceStatus.ARCHIVED
        ctx["instance"].save()
        coord_client = _auth_client(ctx["coordinator"], password="CoordPass123!")
        resp = coord_client.get(f"/api/v1/exams/{ctx['exam'].pk}/coverage/")
        assert resp.data["total_instances"] == 0

    def test_manager_bypasses_permission_check(self):
        """Managers (is_staff) should be able to access coverage without explicit
        membership permission."""
        ctx = _full_setup()
        # manager is staff, but may not have membership. However,
        # SubjectPermission checks is_staff first and returns True.
        client = _auth_client(ctx["manager"])
        resp = client.get(f"/api/v1/exams/{ctx['exam'].pk}/coverage/")
        assert resp.status_code == status.HTTP_200_OK
