"""
Integration tests for Phase 10: export, config, search (RF-14, RF-15).

Moved from apps/organizations/tests/ to apps/exams/tests/
because the core logic revolves around exam data.
"""

import uuid
from decimal import Decimal

import pytest
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.courses.models import AcademicCourse
from apps.exams.models import Exam, ExamModel, Problem
from apps.grading.models import Grade
from apps.instances.models import ExamInstance, InstanceStatus
from apps.organizations.models import Organization
from apps.subjects.models import (
    MembershipRole,
    Subject,
    SubjectGroup,
    SubjectMembership,
)

pytestmark = pytest.mark.django_db
VALID_KEY = "a" * 64


# ── Helpers ──────────────────────────────────────────────────


def _full_setup():
    """Create a complete environment: org, manager, coordinator,
    student with group, exam, model, problem, instance and grade."""
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    org = Organization.objects.create(name="Export Org", subdomain=f"exp-{uuid.uuid4().hex[:8]}")
    manager = user_model.objects.create_user(
        email=f"mgr-{uuid.uuid4().hex[:8]}@example.com",
        password="MgrPass123!",  # noqa: S106
        first_name="Manager",
        last_name="Export",
        organization=org,
        is_staff=True,
    )
    course = AcademicCourse(organization=org, label="2025-2026", is_active=True, status="ACTIVE")
    course.save()

    coord = user_model.objects.create_user(
        email=f"coord-{uuid.uuid4().hex[:8]}@example.com",
        password="Pass123!",  # noqa: S106
        first_name="Coord",
        last_name="Export",
        organization=org,
    )
    student = user_model.objects.create_user(
        email=f"stu-{uuid.uuid4().hex[:8]}@example.com",
        password="Pass123!",  # noqa: S106
        first_name="Student",
        last_name="Export",
        organization=org,
        nia="NIA001",
    )

    subject = Subject(
        organization=org,
        name="Math",
        code=f"M{uuid.uuid4().hex[:4]}",
        course=course,
        coordinator=coord,
    )
    subject.save()

    SubjectMembership.objects.create(
        organization=org,
        user=coord,
        subject=subject,
        role=MembershipRole.COORDINATOR,
        is_active=True,
    )
    group = SubjectGroup.objects.create(subject=subject, label="G1")
    SubjectMembership.objects.create(
        organization=org,
        user=student,
        subject=subject,
        role=MembershipRole.STUDENT,
        group=group,
        is_active=True,
    )

    exam = Exam.objects.create(organization=org, name="Final Exam", subject=subject)
    model = ExamModel.objects.create(label="A", exam=exam)
    problem = Problem.objects.create(
        name="Problem 1", max_score=Decimal("10.00"), exam_model=model, order=1
    )

    instance = ExamInstance.objects.create(
        organization=org,
        exam=exam,
        model=model,
        student=student,
        status=InstanceStatus.PUBLISHED,
        total_score=Decimal("8.50"),
    )
    Grade.objects.create(problem=problem, instance=instance, score=Decimal("8.50"), grader=coord)

    return {
        "org": org,
        "manager": manager,
        "coord": coord,
        "student": student,
        "course": course,
        "subject": subject,
        "exam": exam,
        "model": model,
        "problem": problem,
        "instance": instance,
    }


def _auth_client(user, password="MgrPass123!"):  # noqa: S107
    """Create an APIClient authenticated as the given user."""
    reset_auth_plugin()
    client = APIClient()
    resp = client.post(
        "/api/v1/auth/login/",
        {"email": user.email, "password": password},
        format="json",
    )
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {resp.data['access_token']}")
    return client


# ── Grade export tests (RF-14.1, RF-14.2) ───────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestGradeExport(TestCase):
    def test_export_csv(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        resp = client.get(f"/api/v1/exams/{ctx['exam'].pk}/export-grades/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp["Content-Type"] == "text/csv"

        content = resp.content.decode("utf-8")
        assert "nia" in content
        assert "8.50" in content or "8.5" in content

    def test_export_json(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        resp = client.get(f"/api/v1/exams/{ctx['exam'].pk}/grades/")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 1
        assert resp.data[0]["nia"] == "NIA001"
        assert "problem_grades" in resp.data[0]
        assert len(resp.data[0]["problem_grades"]) == 1


# ── Export permission tests ─────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestGradeExportPermissions(TestCase):
    # def test_export_csv_as_student_returns_403(self):
    #     ctx = _full_setup()
    #     client = _auth_client(ctx["student"], password="Pass123!")
    #     resp = client.get(f"/api/v1/exams/{ctx['exam'].pk}/export-grades/")
    #     assert resp.status_code == status.HTTP_403_FORBIDDEN

    # def test_export_json_as_student_returns_403(self):
    #     ctx = _full_setup()
    #     client = _auth_client(ctx["student"], password="Pass123!")
    #     resp = client.get(f"/api/v1/exams/{ctx['exam'].pk}/grades/")
    #     assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_export_csv_as_coordinator_returns_200(self):
        ctx = _full_setup()
        client = _auth_client(ctx["coord"], password="Pass123!")  # noqa: S106
        resp = client.get(f"/api/v1/exams/{ctx['exam'].pk}/export-grades/")
        assert resp.status_code == status.HTTP_200_OK

    def test_export_json_as_coordinator_returns_200(self):
        ctx = _full_setup()
        client = _auth_client(ctx["coord"], password="Pass123!")  # noqa: S106
        resp = client.get(f"/api/v1/exams/{ctx['exam'].pk}/grades/")
        assert resp.status_code == status.HTTP_200_OK
