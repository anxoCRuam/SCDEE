"""
Integration tests for search (RF-14).
"""

import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.courses.models.courses import AcademicCourse
from apps.exams.models.exams import Exam, ExamModel, Problem
from apps.grading.models.grading import Grade
from apps.instances.models.instances import ExamInstance, InstanceStatus
from apps.organizations.models.organization import Organization
from apps.subjects.models.subjects import MembershipRole, Subject, SubjectGroup, SubjectMembership

pytestmark = pytest.mark.django_db
VALID_KEY = "a" * 64


def _full_setup():
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
    course = AcademicCourse(organization=org, label="2025", is_active=True)
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


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestHierarchicalSearch(TestCase):
    def test_list_courses(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.get("/api/v1/search/")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert len(data["results"]) >= 1
        assert data["results"][0]["label"] == "2025"

    def test_list_subjects_in_course(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.get(f"/api/v1/search/?course_id={ctx['course'].pk}")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert len(data["results"]) >= 1
        assert data["results"][0]["name"] == "Math"

    def test_list_exams_in_subject(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.get(f"/api/v1/search/?subject_id={ctx['subject'].pk}")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert len(data["results"]) >= 1
        assert data["results"][0]["name"] == "Final"

    def test_list_instances_in_exam(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.get(f"/api/v1/search/?exam_id={ctx['exam'].pk}")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert len(data["results"]) >= 1

    def test_search_subjects_by_name(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.get(f"/api/v1/search/?course_id={ctx['course'].pk}&search=math")
        data = resp.json()
        assert len(data["results"]) == 1

    def test_filter_instances_by_status(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.get(f"/api/v1/search/?exam_id={ctx['exam'].pk}&status=PUBLISHED")
        data = resp.json()
        assert len(data["results"]) == 1

    def test_pagination(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])
        # Crear varios cursos para probar paginación
        for i in range(5):
            AcademicCourse.objects.create(organization=ctx["org"], label=f"C{i}", is_active=True)
        resp = client.get("/api/v1/search/?page=1&page_size=2")
        assert resp.status_code == status.HTTP_200_OK
        data = resp.json()
        assert data["count"] == 6  # El del setup + 5 nuevos
        assert len(data["results"]) == 2
        assert "next" in data

    def test_search_subjects_by_code(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])
        resp = client.get(
            f"/api/v1/search/?course_id={ctx['course'].pk}&search={ctx['subject'].code}"
        )
        assert resp.status_code == 200
        assert len(resp.data["results"]) == 1
        assert resp.data["results"][0]["code"] == ctx["subject"].code

    def test_search_instances_by_student_email(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])
        resp = client.get(
            f"/api/v1/search/?exam_id={ctx['exam'].pk}&search={ctx['student'].email}"
        )
        assert resp.status_code == 200
        assert len(resp.data["results"]) == 1
        assert resp.data["results"][0]["student_email"] == ctx["student"].email

    def test_instances_filter_by_status_and_issues(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])
        # crear una segunda instancia con estado diferente
        instance2 = ExamInstance.objects.create(
            organization=ctx["org"],
            exam=ctx["exam"],
            model=ctx["model"],
            student=None,
            status="PENDING",
            has_issues=True,
        )
        resp = client.get(
            f"/api/v1/search/?exam_id={ctx['exam'].pk}&status=PENDING&has_issues=true"
        )
        assert resp.status_code == 200
        results = resp.data["results"]
        assert len(results) == 1
        assert results[0]["id"] == str(instance2.pk)

    def test_search_denied_for_student(self):
        ctx = _full_setup()
        client = _auth(ctx["student"], pw="Pass123!")
        resp = client.get("/api/v1/search/")
        assert resp.status_code == status.HTTP_403_FORBIDDEN
