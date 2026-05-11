"""
Integration tests for Phase 10: export, config, search (RF-14, RF-15).
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
from apps.subjects.models.permissions import MembershipRole
from apps.subjects.models.subjects import Subject, SubjectGroup, SubjectMembership

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


# ── Grade export tests (RF-14.1, RF-14.2) ───────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestGradeExport(TestCase):
    def test_export_csv(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.get(f"/api/v1/exams/{ctx['exam'].pk}/export/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp["Content-Type"] == "text/csv"

        content = resp.content.decode("utf-8")
        assert "NIA" in content
        assert "8.50" in content or "8.5" in content

    def test_export_json(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.get(f"/api/v1/exams/{ctx['exam'].pk}/export/?export_format=json")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 1
        assert resp.data[0]["nia"] == "NIA001"
        assert "problem_grades" in resp.data[0]
        assert len(resp.data[0]["problem_grades"]) == 1

    def test_export_csv_forbidden_for_student(self):
        ctx = _full_setup()
        client = _auth(ctx["student"], pw="Pass123!")
        resp = client.get(f"/api/v1/exams/{ctx['exam'].pk}/export/")
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_export_json_forbidden_for_teacher_without_export_perm(self):
        # Crear un teacher sin can_export_grades
        ctx = _full_setup()
        user_model = get_user_model()
        teacher = user_model.objects.create_user(
            email=f"t-{uuid.uuid4().hex[:8]}@x.com",
            password="TeachPass123!",  # noqa: S106
            first_name="T",
            last_name="U",
            organization=ctx["org"],
        )
        # Asignar membresía como TEACHER (que por defecto no tiene can_export_grades)
        SubjectMembership.objects.create(
            organization=ctx["org"],
            user=teacher,
            subject=ctx["subject"],
            role=MembershipRole.TEACHER,
            is_active=True,
        )
        client = _auth(teacher, pw="TeachPass123!")
        resp = client.get(f"/api/v1/exams/{ctx['exam'].pk}/export/")
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_export_csv_staff_user_allowed(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])
        resp = client.get(f"/api/v1/exams/{ctx['exam'].pk}/export/")
        assert resp.status_code == status.HTTP_200_OK

    def test_export_csv_custom(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])
        from django.core.cache import cache

        from apps.organizations.services.organization_config import get_org_config

        config = get_org_config(ctx["org"])
        config.grade_export_columns = {
            "student__nia": "NIA",
            "student__last_name": "Apellidos",
            "group": "Grupo",
            "total_score": "Nota Final",
        }
        config.save()
        # Invalida la caché manualmente (el servicio solo lo hace en update_org_config)
        cache.delete(f"org_config:{ctx['org'].pk}")

        resp = client.get(f"/api/v1/exams/{ctx['exam'].pk}/export/?export_format=csv")
        assert resp["Content-Type"] == "text/csv"
        content = resp.content.decode("utf-8")
        assert "NIA" in content
        assert "Apellidos" in content
        assert "Dent" in content
        assert "Grupo" in content
        assert "Nota Final" in content
