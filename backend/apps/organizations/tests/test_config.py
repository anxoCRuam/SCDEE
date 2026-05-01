"""
Integration tests for Phase 10: config, search (RF-14, RF-15).
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
from apps.subjects.models import MembershipRole, Subject, SubjectGroup, SubjectMembership

pytestmark = pytest.mark.django_db
VALID_KEY = "a" * 64


def _full_setup():
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    org = Organization.objects.create(name="Org", subdomain=f"o-{uuid.uuid4().hex[:8]}")
    manager = user_model.objects.create_user(
        email=f"m-{uuid.uuid4().hex[:8]}@x.com",
        password="MgrPass123!",  # noqa: S106
        first_name="Mgr",
        last_name="U",
        organization=org,
        is_staff=True,
    )
    course = AcademicCourse(organization=org, label="2025", is_active=True, status="ACTIVE")
    course.save()
    coord = user_model.objects.create_user(
        email=f"c-{uuid.uuid4().hex[:8]}@x.com",
        password="Pass123!",  # noqa: S106
        first_name="C",
        last_name="U",
        organization=org,
    )
    student = user_model.objects.create_user(
        email=f"s-{uuid.uuid4().hex[:8]}@x.com",
        password="Pass123!",  # noqa: S106
        first_name="Stu",
        last_name="Dent",
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

    exam = Exam.objects.create(organization=org, name="Final", subject=subject)
    model = ExamModel.objects.create(label="A", exam=exam)
    problem = Problem.objects.create(
        name="P1", max_score=Decimal("10.00"), exam_model=model, order=1
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


def _auth(user, pw="MgrPass123!"):
    reset_auth_plugin()
    c = APIClient()
    r = c.post("/api/v1/auth/login/", {"email": user.email, "password": pw}, format="json")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access_token']}")
    return c


# ── Config tests (RF-15.1, RF-15.2) ─────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestOrgConfig(TestCase):
    def test_get_config_creates_default(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.get("/api/v1/config/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["default_ocr_engine"] == "easyocr"
        assert resp.data["max_page_size_mb"] == 50

    def test_update_config(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.patch(
            "/api/v1/config/",
            {"max_page_size_mb": 100, "default_language": "en"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["max_page_size_mb"] == 100
        assert resp.data["default_language"] == "en"

    def test_update_persists(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        client.patch("/api/v1/config/", {"assembly_timeout_seconds": 1200}, format="json")

        resp = client.get("/api/v1/config/")
        assert resp.data["assembly_timeout_seconds"] == 1200

    def test_non_manager_rejected(self):
        ctx = _full_setup()
        client = _auth(ctx["student"], pw="Pass123!")

        resp = client.get("/api/v1/config/")
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ── Hierarchical search tests (RF-14.3) ─────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestHierarchicalSearch(TestCase):
    def test_list_courses(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.get("/api/v1/search/")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) >= 1
        assert resp.data[0]["label"] == "2025"

    def test_list_subjects_in_course(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.get(f"/api/v1/search/?course_id={ctx['course'].pk}")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) >= 1
        assert resp.data[0]["name"] == "Math"

    def test_list_exams_in_subject(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.get(f"/api/v1/search/?subject_id={ctx['subject'].pk}")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) >= 1
        assert resp.data[0]["name"] == "Final"

    def test_list_instances_in_exam(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.get(f"/api/v1/search/?exam_id={ctx['exam'].pk}")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) >= 1

    def test_search_subjects_by_name(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.get(f"/api/v1/search/?course_id={ctx['course'].pk}&search=math")
        assert len(resp.data) == 1

    def test_filter_instances_by_status(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.get(f"/api/v1/search/?exam_id={ctx['exam'].pk}&status=PUBLISHED")
        assert len(resp.data) == 1
