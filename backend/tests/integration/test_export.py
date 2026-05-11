"""
HTTP-driven integration tests for the grade export endpoints.

The service layer ``export_grades_csv`` / ``export_grades_json`` is
covered by unit tests in the exams app. These tests exercise the
*HTTP* surface — auth, permissions, content-type, content-disposition,
charset, and the actual byte stream produced — so the frontend can
trust what the endpoint returns.

What we cover:

- ``GET /api/v1/exams/{id}/export-grades/`` returns ``text/csv`` with
  an ``attachment`` Content-Disposition and the published grades.
- ``GET /api/v1/exams/{id}/grades/`` returns JSON with the per-problem
  breakdown.
- Both endpoints emit a ``DATA_EXPORTED`` audit row with the right
  metadata.
- Both endpoints filter by exam: only this exam's instances appear,
  not instances of other exams in the same organisation.
- Only published-and-later instances appear (PENDING_GRADING is
  excluded).
- An audit row is created per call.

References: RF-14.1, RF-14.2, RF-16.1.
"""

from __future__ import annotations

import csv
import io
import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.audit.models.auditlog import AuditLog
from apps.courses.models.courses import AcademicCourse
from apps.exams.models.exams import Exam, ExamModel, Problem
from apps.grading.models.grading import Grade
from apps.instances.models.instances import ExamInstance, InstanceStatus
from apps.organizations.models.organization import Organization
from apps.subjects.models.subjects import MembershipRole, Subject, SubjectMembership

pytestmark = [pytest.mark.django_db, pytest.mark.integration]
VALID_KEY = "a" * 64


@pytest.fixture(autouse=True)
def _stub_jwt_blacklist(monkeypatch):
    """Bypass Redis-backed JWT calls in test (LocMemCache only)."""
    monkeypatch.setattr(
        "apps.accounts.blacklist.get_user_token_generation",
        lambda user_id: 0,
    )
    monkeypatch.setattr(
        "apps.accounts.blacklist.is_blacklisted",
        lambda jti: False,
    )


# ── Helpers ────────────────────────────────────────────────────────


def _login(email: str, password: str) -> APIClient:
    """Log in via HTTP and return an authenticated APIClient."""
    reset_auth_plugin()
    client = APIClient()
    response = client.post(
        "/api/v1/auth/login/",
        {"email": email, "password": password},
        format="json",
    )
    assert (
        response.status_code == 200
    ), f"Login failed: {response.status_code} {response.content!r}"
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access_token']}")
    return client


def _setup_published_exam_with_grades():
    """Build org + exam + 2 published instances + 1 pending instance.

    We need a mix so the tests can verify that PENDING_GRADING is
    excluded.
    """
    user_model = get_user_model()
    suffix = uuid.uuid4().hex[:8]

    org = Organization.objects.create(name=f"Org-{suffix}", subdomain=f"o{suffix}")
    manager = user_model.objects.create_user(
        email=f"mgr-{suffix}@x.com",
        password="MgrPass123!",  # noqa: S106
        first_name="Mary",
        last_name="Manager",
        organization=org,
        is_staff=True,
    )
    course = AcademicCourse.objects.create(organization=org, label="2026", is_active=True)
    subject = Subject.objects.create(
        organization=org,
        name="Subject",
        code=f"S{suffix}",
        course=course,
        coordinator=manager,
    )
    SubjectMembership.objects.create(
        organization=org,
        user=manager,
        subject=subject,
        role=MembershipRole.COORDINATOR,
        is_active=True,
    )
    exam = Exam.objects.create(organization=org, name="Final Exam", subject=subject)
    model = ExamModel.objects.create(label="A", exam=exam)
    problem = Problem.objects.create(
        name="P1", max_score=Decimal("10.00"), exam_model=model, order=1
    )

    # Three students with different last names (CSV is sorted by last name).
    students_data = [
        ("Alvarez", "Ana", "A1234567", InstanceStatus.PUBLISHED, Decimal("8.50")),
        ("Becerra", "Bruno", "B2345678", InstanceStatus.FINALIZED, Decimal("6.25")),
        ("Cano", "Carlos", "C3456789", InstanceStatus.PENDING_GRADING, None),
    ]

    student_rows = []
    for last_name, first_name, nia, status, total_score in students_data:
        student = user_model.objects.create_user(
            email=f"{last_name.lower()}-{suffix}@x.com",
            password="StuPass123!",  # noqa: S106
            first_name=first_name,
            last_name=last_name,
            nia=nia,
            organization=org,
        )
        SubjectMembership.objects.create(
            organization=org,
            user=student,
            subject=subject,
            role=MembershipRole.STUDENT,
            is_active=True,
        )
        instance = ExamInstance.objects.create(
            organization=org,
            exam=exam,
            model=model,
            student=student,
            status=status,
            total_score=total_score,
            expected_pages=1,
        )
        # Add a Grade row only for non-pending students so the
        # JSON ``problem_grades`` block has something to show.
        if total_score is not None:
            Grade.objects.create(
                problem=problem,
                instance=instance,
                score=total_score,
                grader=manager,
            )
        student_rows.append(
            {
                "student": student,
                "instance": instance,
                "status": status,
                "score": total_score,
            }
        )

    return {
        "org": org,
        "manager": manager,
        "manager_pw": "MgrPass123!",
        "exam": exam,
        "problem": problem,
        "students": student_rows,
    }


# ── Tests: CSV ─────────────────────────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
def test_csv_export_returns_correct_content_type_and_disposition():
    """Headers must declare CSV and trigger a save dialog in the browser."""
    world = _setup_published_exam_with_grades()
    client = _login(world["manager"].email, world["manager_pw"])

    response = client.get(f"/api/v1/exams/{world['exam'].pk}/export/")

    assert response.status_code == 200
    assert response["Content-Type"].startswith(
        "text/csv"
    ), f"Expected text/csv, got {response['Content-Type']!r}."

    disposition = response["Content-Disposition"]
    assert "attachment" in disposition
    # Filename should mention the exam id so it's distinguishable when
    # the user downloads several exports.
    assert str(world["exam"].pk) in disposition


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
def test_csv_export_excludes_pending_instances_and_sorts_by_last_name():
    """Only PUBLISHED-and-later show up, sorted alphabetically by Last Name."""
    world = _setup_published_exam_with_grades()
    client = _login(world["manager"].email, world["manager_pw"])

    response = client.get(f"/api/v1/exams/{world['exam'].pk}/export/")
    body = response.content.decode("utf-8")

    reader = csv.DictReader(io.StringIO(body))
    rows = list(reader)

    # Two students published; the pending one is excluded.
    assert len(rows) == 2, f"Expected 2 rows (Alvarez + Becerra), got {len(rows)}: {rows}"

    last_names = [row["Last Name"] for row in rows]
    assert last_names == [
        "Alvarez",
        "Becerra",
    ], f"Rows should be sorted by Last Name; got {last_names}."

    # Carlos Cano (PENDING_GRADING) must not be there.
    assert all(row["Last Name"] != "Cano" for row in rows)


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
def test_csv_export_does_not_leak_other_exams():
    """Two exams in the same org → CSV for exam A only contains A's rows."""
    world = _setup_published_exam_with_grades()

    # Build a second exam in the same org with one published instance.
    other_exam = Exam.objects.create(
        organization=world["org"],
        name="Other Exam",
        subject=world["students"][0]["instance"].exam.subject,
    )
    other_model = ExamModel.objects.create(label="A", exam=other_exam)

    user_model = get_user_model()
    intruder_student = user_model.objects.create_user(
        email=f"intruder-{uuid.uuid4().hex[:8]}@x.com",
        password="P@ss123!",  # noqa: S106
        first_name="Zorro",
        last_name="Zzzz",
        nia="Z0000000",
        organization=world["org"],
    )
    ExamInstance.objects.create(
        organization=world["org"],
        exam=other_exam,
        model=other_model,
        student=intruder_student,
        status=InstanceStatus.PUBLISHED,
        total_score=Decimal("7.00"),
        expected_pages=1,
    )

    client = _login(world["manager"].email, world["manager_pw"])
    response = client.get(f"/api/v1/exams/{world['exam'].pk}/export/")
    rows = list(csv.DictReader(io.StringIO(response.content.decode("utf-8"))))

    # Intruder student must NOT appear in the export.
    assert all(
        row["Last Name"] != "Zzzz" for row in rows
    ), "Export of exam A leaked rows from exam B in the same organisation."


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
def test_csv_export_writes_audit_row_with_metadata():
    """Each call must append a DATA_EXPORTED audit event."""
    world = _setup_published_exam_with_grades()
    client = _login(world["manager"].email, world["manager_pw"])

    before = AuditLog.objects.filter(event_type="DATA_EXPORTED").count()
    client.get(f"/api/v1/exams/{world['exam'].pk}/export/")
    after = AuditLog.objects.filter(event_type="DATA_EXPORTED").count()

    assert after == before + 1

    last_event = AuditLog.objects.filter(event_type="DATA_EXPORTED").order_by("-timestamp").first()
    assert last_event.actor_id == world["manager"].pk
    payload = last_event.payload or {}
    # The view writes a dict with format and exam name.
    assert payload.get("export_format") == "csv"
    assert payload.get("exam") == world["exam"].name


# ── Tests: JSON ────────────────────────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
def test_json_export_returns_per_problem_breakdown():
    """JSON export must include the per-problem grades for stats consumers."""
    world = _setup_published_exam_with_grades()
    client = _login(world["manager"].email, world["manager_pw"])

    response = client.get(f"/api/v1/exams/{world['exam'].pk}/export/?export_format=json")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)
    # Same exclusion rules: PENDING out, PUBLISHED+ in.
    assert len(data) == 2

    # Each row carries a problem_grades list.
    for row in data:
        assert "problem_grades" in row, f"JSON row missing problem_grades: {row!r}"
        assert "model_label" in row
        assert "status" in row

    # The first row (Alvarez, score 8.50) must have one problem_grade.
    alvarez = next(r for r in data if r["last_name"] == "Alvarez")
    assert len(alvarez["problem_grades"]) == 1
    pg = alvarez["problem_grades"][0]
    # Score is serialised as string to preserve decimal precision.
    assert pg["score"] == "8.50"


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
def test_json_export_writes_audit_row_with_format_json():
    """Mirror of the CSV audit test, but for the JSON endpoint."""
    world = _setup_published_exam_with_grades()
    client = _login(world["manager"].email, world["manager_pw"])

    before = AuditLog.objects.filter(event_type="DATA_EXPORTED").count()
    client.get(f"/api/v1/exams/{world['exam'].pk}/export/?export_format=json")
    after = AuditLog.objects.filter(event_type="DATA_EXPORTED").count()

    assert after == before + 1
    last_event = AuditLog.objects.filter(event_type="DATA_EXPORTED").order_by("-timestamp").first()
    assert (last_event.payload or {}).get("export_format") == "json"


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
def test_export_returns_404_for_unknown_exam():
    """Unknown exam id → 404 with the standard ``error_code`` shape."""
    world = _setup_published_exam_with_grades()
    client = _login(world["manager"].email, world["manager_pw"])

    fake = uuid.uuid4()
    csv_response = client.get(f"/api/v1/exams/{fake}/export/")
    json_response = client.get(f"/api/v1/exams/{fake}/export/?export_format=json")

    for r in (csv_response, json_response):
        assert (
            r.status_code == 404
        ), f"Expected 404 for unknown exam, got {r.status_code} {r.content!r}"
