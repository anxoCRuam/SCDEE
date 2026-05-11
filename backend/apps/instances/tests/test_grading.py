# apps/instances/tests/test_grading.py
import uuid
from decimal import Decimal

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.grading.models.grading import AssignmentRule
from apps.grading.services.grading import grade_problem


# Configurar la clave de encriptación para todas las pruebas
@pytest.fixture(autouse=True)
def set_encryption_key(settings):
    settings.ENCRYPTION_MASTER_KEY = "a" * 64


def _auth_client(user, password="MgrPass123!"):  # noqa: S107
    reset_auth_plugin()
    c = APIClient()
    r = c.post("/api/v1/auth/login/", {"email": user.email, "password": password}, format="json")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access_token']}")
    return c


@pytest.fixture
def grading_perm_ctx():
    from django.contrib.auth import get_user_model

    from apps.courses.models.courses import AcademicCourse
    from apps.exams.models.exams import Exam, ExamModel, Problem
    from apps.instances.models.instances import ExamInstance, InstanceStatus
    from apps.organizations.models.organization import Organization
    from apps.subjects.models.subjects import MembershipRole, Subject, SubjectMembership

    user_model = get_user_model()
    org = Organization.objects.create(name="Org", subdomain=f"gp-{uuid.uuid4().hex[:8]}")
    manager = user_model.objects.create_user(
        email=f"mgr-{uuid.uuid4().hex[:8]}@x.com",
        password="MgrPass123!",  # noqa: S106
        first_name="Manager",
        last_name="User",
        organization=org,
        is_staff=True,
    )
    corrector = user_model.objects.create_user(
        email=f"corr-{uuid.uuid4().hex[:8]}@x.com",
        password="CorrPass123!",  # noqa: S106
        first_name="Corrector",
        last_name="User",
        organization=org,
    )
    student = user_model.objects.create_user(
        email=f"stu-{uuid.uuid4().hex[:8]}@x.com",
        password="StuPass123!",  # noqa: S106
        first_name="Student",
        last_name="User",
        organization=org,
    )
    course = AcademicCourse.objects.create(organization=org, label="2026", is_active=True)
    subject = Subject.objects.create(
        organization=org, name="Math", code="M101", course=course, coordinator=manager
    )
    SubjectMembership.objects.create(
        organization=org,
        user=manager,
        subject=subject,
        role=MembershipRole.COORDINATOR,
        is_active=True,
    )
    SubjectMembership.objects.create(
        organization=org,
        user=corrector,
        subject=subject,
        role=MembershipRole.TEACHER,
        is_active=True,
    )
    exam = Exam.objects.create(organization=org, name="Exam", subject=subject)
    model = ExamModel.objects.create(label="A", exam=exam)
    problem1 = Problem.objects.create(
        name="P1", max_score=Decimal("10"), exam_model=model, order=1
    )
    problem2 = Problem.objects.create(
        name="P2", max_score=Decimal("10"), exam_model=model, order=2
    )
    instance = ExamInstance.objects.create(
        organization=org,
        exam=exam,
        model=model,
        student=student,
        status=InstanceStatus.PENDING_GRADING,
        expected_pages=1,
    )
    return {
        "org": org,
        "manager": manager,
        "corrector": corrector,
        "student": student,
        "subject": subject,
        "exam": exam,
        "model": model,
        "problem1": problem1,
        "problem2": problem2,
        "instance": instance,
    }


# ── Tests ────────────────────────────────────────────────────


def test_manager_can_grade(grading_perm_ctx):
    client = _auth_client(grading_perm_ctx["manager"])
    resp = client.put(
        f"/api/v1/instances/{grading_perm_ctx['instance'].pk}/problems/{grading_perm_ctx['problem1'].pk}/grade/",
        {"score": "5.0"},
        format="json",
    )
    assert resp.status_code == status.HTTP_200_OK


def test_assigned_corrector_can_grade(grading_perm_ctx):
    AssignmentRule.objects.create(
        exam=grading_perm_ctx["exam"],
        problems=[str(grading_perm_ctx["problem1"].pk)],
        correctors=[str(grading_perm_ctx["corrector"].pk)],
    )
    client = _auth_client(grading_perm_ctx["corrector"], password="CorrPass123!")  # noqa: S106
    resp = client.put(
        f"/api/v1/instances/{grading_perm_ctx['instance'].pk}/problems/{grading_perm_ctx['problem1'].pk}/grade/",
        {"score": "5.0"},
        format="json",
    )
    assert resp.status_code == status.HTTP_200_OK


def test_corrector_rejected_for_different_problem(grading_perm_ctx):
    AssignmentRule.objects.create(
        exam=grading_perm_ctx["exam"],
        problems=[str(grading_perm_ctx["problem1"].pk)],
        correctors=[str(grading_perm_ctx["corrector"].pk)],
    )
    client = _auth_client(grading_perm_ctx["corrector"], password="CorrPass123!")  # noqa: S106
    resp = client.put(
        f"/api/v1/instances/{grading_perm_ctx['instance'].pk}/problems/{grading_perm_ctx['problem2'].pk}/grade/",
        {"score": "5.0"},
        format="json",
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_unassigned_user_rejected(grading_perm_ctx):
    client = _auth_client(grading_perm_ctx["corrector"], password="CorrPass123!")  # noqa: S106
    resp = client.put(
        f"/api/v1/instances/{grading_perm_ctx['instance'].pk}/problems/{grading_perm_ctx['problem1'].pk}/grade/",
        {"score": "5.0"},
        format="json",
    )
    assert resp.status_code == status.HTTP_403_FORBIDDEN


def test_my_tasks_corrector_sees_only_assigned(grading_perm_ctx):
    AssignmentRule.objects.create(
        exam=grading_perm_ctx["exam"],
        problems=[str(grading_perm_ctx["problem1"].pk)],
        correctors=[str(grading_perm_ctx["corrector"].pk)],
    )
    client = _auth_client(grading_perm_ctx["corrector"], password="CorrPass123!")  # noqa: S106
    resp = client.get("/api/v1/my-tasks/")
    assert resp.status_code == status.HTTP_200_OK
    assert len(resp.data["grading_tasks"]) == 1
    task = resp.data["grading_tasks"][0]
    assert len(task["ungraded_problems"]) == 1
    assert task["ungraded_problems"][0] == str(grading_perm_ctx["problem1"].pk)


def test_my_tasks_unassigned_user_empty(grading_perm_ctx):
    client = _auth_client(grading_perm_ctx["corrector"], password="CorrPass123!")  # noqa: S106
    resp = client.get("/api/v1/my-tasks/")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["grading_tasks"] == []


def test_my_tasks_with_exam_id_shows_all(grading_perm_ctx):
    # Calificamos el problema1 para que ya no esté pendiente
    AssignmentRule.objects.create(
        exam=grading_perm_ctx["exam"],
        problems=[str(grading_perm_ctx["problem1"].pk), str(grading_perm_ctx["problem2"].pk)],
        correctors=[str(grading_perm_ctx["manager"].pk)],
    )
    grade_problem(
        instance=grading_perm_ctx["instance"],
        problem=grading_perm_ctx["problem1"],
        score=Decimal("5.0"),
        grader=grading_perm_ctx["manager"],
        old_score=None,
    )
    client = _auth_client(grading_perm_ctx["manager"])
    # Sin exam_id: solo problemas pendientes (quedará el problem2)
    resp_no_exam = client.get("/api/v1/my-tasks/")
    assert resp_no_exam.status_code == status.HTTP_200_OK
    assert len(resp_no_exam.data["grading_tasks"]) == 1
    task_no_exam = resp_no_exam.data["grading_tasks"][0]
    assert len(task_no_exam["problems_sent"]) == 1
    assert task_no_exam["problems_sent"][0] == str(grading_perm_ctx["problem2"].pk)

    # Con exam_id: deben aparecer todos los problemas, calificados o no
    resp_with_exam = client.get(f"/api/v1/my-tasks/?exam_id={grading_perm_ctx['exam'].pk}")
    assert resp_with_exam.status_code == status.HTTP_200_OK
    assert len(resp_with_exam.data["grading_tasks"]) == 1
    task_exam = resp_with_exam.data["grading_tasks"][0]
    assert len(task_exam["problems_sent"]) == 2  # ambos problemas visibles
    problem_ids = set(task_exam["problems_sent"])
    assert str(grading_perm_ctx["problem1"].pk) in problem_ids
    assert str(grading_perm_ctx["problem2"].pk) in problem_ids
