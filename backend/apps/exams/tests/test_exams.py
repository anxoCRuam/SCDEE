"""
Integration tests for exams, models, page profiles, zones,
problems, rubrics, and convocations (RF-6).

Happy path + key error cases.
"""

import uuid
from decimal import Decimal

import pytest
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.audit.models import AuditLog
from apps.courses.models import AcademicCourse
from apps.exams.models import (
    Exam,
    ExamModel,
    Problem,
)
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
    """Create org + manager + course + subject + coordinator. Returns dict."""
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
    course = AcademicCourse(organization=org, label="2025-2026", is_active=True, status="ACTIVE")
    course.save()

    coordinator = user_model.objects.create_user(
        email=f"coord-{uuid.uuid4().hex[:8]}@example.com",
        password="Pass123!",  # noqa: S106
        first_name="Coord",
        last_name="User",
        organization=org,
    )
    subject = Subject(
        organization=org,
        name="Test Subject",
        code=f"TS{uuid.uuid4().hex[:4]}",
        course=course,
        coordinator=coordinator,
    )
    subject.save()
    SubjectMembership.objects.create(
        organization=org,
        user=coordinator,
        subject=subject,
        role=MembershipRole.COORDINATOR,
        is_active=True,
    )

    return {
        "org": org,
        "manager": manager,
        "course": course,
        "coordinator": coordinator,
        "subject": subject,
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


# ── Exam CRUD (RF-6.1, RF-6.2, RF-6.3) ──────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestExamCRUD(TestCase):
    def test_create_exam(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        resp = client.post(
            f"/api/v1/subjects/{ctx['subject'].pk}/exams/",
            {"name": "Parcial 1"},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["name"] == "Parcial 1"
        assert resp.data["model_count"] == 1  # Auto-created model "A"

    def test_create_exam_auto_model_a(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        resp = client.post(
            f"/api/v1/subjects/{ctx['subject'].pk}/exams/",
            {"name": "Parcial 1"},
            format="json",
        )
        exam_id = resp.data["id"]
        exam = Exam.unfiltered.get(pk=exam_id)
        assert exam.models.count() == 1
        assert exam.models.first().label == "A"

    def test_list_exams(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        client.post(f"/api/v1/subjects/{ctx['subject'].pk}/exams/", {"name": "E1"}, format="json")
        client.post(f"/api/v1/subjects/{ctx['subject'].pk}/exams/", {"name": "E2"}, format="json")

        resp = client.get(f"/api/v1/subjects/{ctx['subject'].pk}/exams/")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 2

    def test_exam_detail(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        resp = client.post(
            f"/api/v1/subjects/{ctx['subject'].pk}/exams/",
            {"name": "Detail Test"},
            format="json",
        )
        exam_id = resp.data["id"]

        resp = client.get(f"/api/v1/exams/{exam_id}/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["name"] == "Detail Test"
        assert "models" in resp.data

    def test_update_exam_name(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        resp = client.post(
            f"/api/v1/subjects/{ctx['subject'].pk}/exams/",
            {"name": "Old Name"},
            format="json",
        )
        exam_id = resp.data["id"]

        resp = client.patch(f"/api/v1/exams/{exam_id}/", {"name": "New Name"}, format="json")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["name"] == "New Name"

    def test_delete_exam_without_instances(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        resp = client.post(
            f"/api/v1/subjects/{ctx['subject'].pk}/exams/",
            {"name": "To Delete"},
            format="json",
        )
        exam_id = resp.data["id"]

        resp = client.delete(f"/api/v1/exams/{exam_id}/")
        assert resp.status_code == status.HTTP_204_NO_CONTENT

    def test_audit_log_on_create(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        client.post(
            f"/api/v1/subjects/{ctx['subject'].pk}/exams/",
            {"name": "Audit Test"},
            format="json",
        )
        assert AuditLog.objects.filter(event_type="EXAM_CREATED").exists()


# ── Model creation (RF-6.5) ─────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestModelCreation(TestCase):
    def test_create_additional_model(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        resp = client.post(
            f"/api/v1/subjects/{ctx['subject'].pk}/exams/",
            {"name": "Multi Model"},
            format="json",
        )
        exam_id = resp.data["id"]

        resp = client.post(
            f"/api/v1/exams/{exam_id}/models/",
            {"label": "B"},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["label"] == "B"

    def test_duplicate_model_label_rejected(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        resp = client.post(
            f"/api/v1/subjects/{ctx['subject'].pk}/exams/",
            {"name": "Dup"},
            format="json",
        )
        exam_id = resp.data["id"]

        # "A" already exists (auto-created).
        resp = client.post(
            f"/api/v1/exams/{exam_id}/models/",
            {"label": "A"},
            format="json",
        )
        assert resp.status_code == status.HTTP_409_CONFLICT


# ── PageProfile & Zones (RF-6.8) ────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestPageProfilesAndZones(TestCase):
    def _create_exam_with_model(self, ctx, client):
        resp = client.post(
            f"/api/v1/subjects/{ctx['subject'].pk}/exams/",
            {"name": "Zone Test"},
            format="json",
        )
        exam_id = resp.data["id"]
        model = ExamModel.objects.get(exam_id=exam_id, label="A")
        return exam_id, str(model.pk)

    def test_create_page_profile(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        _, model_pk = self._create_exam_with_model(ctx, client)

        resp = client.post(
            f"/api/v1/models/{model_pk}/page-profiles/",
            {"page_number": 1},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["page_number"] == 1

    def test_create_zone(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        _, model_pk = self._create_exam_with_model(ctx, client)

        pp_resp = client.post(
            f"/api/v1/models/{model_pk}/page-profiles/",
            {"page_number": 1},
            format="json",
        )
        profile_pk = pp_resp.data["id"]

        resp = client.post(
            f"/api/v1/page-profiles/{profile_pk}/zones/",
            {
                "zone_type": "QR",
                "attribute": "exam_qr",
                "x": 10.0,
                "y": 10.0,
                "width": 100.0,
                "height": 100.0,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["zone_type"] == "QR"
        assert resp.data["attribute"] == "exam_qr"

    def test_qr_zone_forces_exam_qr_attribute(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        _, model_pk = self._create_exam_with_model(ctx, client)

        pp_resp = client.post(
            f"/api/v1/models/{model_pk}/page-profiles/",
            {"page_number": 1},
            format="json",
        )
        profile_pk = pp_resp.data["id"]

        # Even if we send a different attribute, QR zones force "exam_qr".
        resp = client.post(
            f"/api/v1/page-profiles/{profile_pk}/zones/",
            {
                "zone_type": "QR",
                "attribute": "something_else",
                "x": 10.0,
                "y": 10.0,
                "width": 80.0,
                "height": 80.0,
            },
            format="json",
        )
        assert resp.data["attribute"] == "exam_qr"

    def test_qr_zone_invalidates_instrumented_pdf(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        _, model_pk = self._create_exam_with_model(ctx, client)

        # Set instrumented_pdf_valid to True manually.
        model = ExamModel.objects.get(pk=model_pk)
        model.instrumented_pdf_valid = True
        model.save()

        pp_resp = client.post(
            f"/api/v1/models/{model_pk}/page-profiles/",
            {"page_number": 1},
            format="json",
        )
        profile_pk = pp_resp.data["id"]

        client.post(
            f"/api/v1/page-profiles/{profile_pk}/zones/",
            {
                "zone_type": "QR",
                "attribute": "exam_qr",
                "x": 10,
                "y": 10,
                "width": 80,
                "height": 80,
            },
            format="json",
        )

        model.refresh_from_db()
        assert model.instrumented_pdf_valid is False

    def test_delete_zone(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        _, model_pk = self._create_exam_with_model(ctx, client)

        pp_resp = client.post(
            f"/api/v1/models/{model_pk}/page-profiles/",
            {"page_number": 1},
            format="json",
        )
        z_resp = client.post(
            f"/api/v1/page-profiles/{pp_resp.data['id']}/zones/",
            {
                "zone_type": "OCR_TEXT",
                "attribute": "name",
                "x": 10,
                "y": 10,
                "width": 200,
                "height": 30,
            },
            format="json",
        )
        zone_pk = z_resp.data["id"]

        resp = client.delete(f"/api/v1/zones/{zone_pk}/")
        assert resp.status_code == status.HTTP_204_NO_CONTENT

    def test_list_page_profiles(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        _, model_pk = self._create_exam_with_model(ctx, client)

        client.post(f"/api/v1/models/{model_pk}/page-profiles/", {"page_number": 1}, format="json")
        client.post(f"/api/v1/models/{model_pk}/page-profiles/", {"page_number": 2}, format="json")

        resp = client.get(f"/api/v1/models/{model_pk}/page-profiles/")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 2


# ── Problems & Rubrics (RF-6.9, RF-6.11) ────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestProblemsAndRubrics(TestCase):
    def test_create_problem(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        resp = client.post(
            f"/api/v1/subjects/{ctx['subject'].pk}/exams/",
            {"name": "Prob Test"},
            format="json",
        )
        model = ExamModel.objects.get(exam_id=resp.data["id"], label="A")

        resp = client.post(
            f"/api/v1/models/{model.pk}/problems/",
            {"name": "Problema 1", "max_score": "10.00", "order": 1},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["name"] == "Problema 1"
        assert resp.data["max_score"] == "10.00"

    def test_set_rubric(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        resp = client.post(
            f"/api/v1/subjects/{ctx['subject'].pk}/exams/",
            {"name": "Rub Test"},
            format="json",
        )
        model = ExamModel.objects.get(exam_id=resp.data["id"])
        problem = Problem.objects.create(
            name="P1", max_score=Decimal("10.00"), exam_model=model, order=1
        )

        resp = client.post(
            f"/api/v1/problems/{problem.pk}/rubric/",
            {
                "criteria": [
                    {"description": "Correct algorithm", "score": "7.00"},
                    {"description": "Clean code", "score": "3.00"},
                    {"description": "Compilation error", "score": "-2.00"},
                ]
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert len(resp.data) == 3

    def test_list_problems(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        resp = client.post(
            f"/api/v1/subjects/{ctx['subject'].pk}/exams/",
            {"name": "List Prob"},
            format="json",
        )
        model = ExamModel.objects.get(exam_id=resp.data["id"])

        client.post(
            f"/api/v1/models/{model.pk}/problems/", {"name": "P1", "max_score": "5"}, format="json"
        )
        client.post(
            f"/api/v1/models/{model.pk}/problems/", {"name": "P2", "max_score": "5"}, format="json"
        )

        resp = client.get(f"/api/v1/models/{model.pk}/problems/")
        assert len(resp.data) == 2


# ── Convocation (RF-6.4) ────────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestConvocation(TestCase):
    def test_set_convocation(self):
        from django.contrib.auth import get_user_model

        user_model = get_user_model()
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        # Create students with group.
        student = user_model.objects.create_user(
            email=f"st-{uuid.uuid4().hex[:8]}@example.com",
            password="Pass123!",  # noqa: S106
            first_name="Student",
            last_name="One",
            organization=ctx["org"],
        )
        group = SubjectGroup.objects.create(subject=ctx["subject"], label="G1")
        SubjectMembership.objects.create(
            organization=ctx["org"],
            user=student,
            subject=ctx["subject"],
            role=MembershipRole.STUDENT,
            group=group,
            is_active=True,
        )

        # Create exam.
        resp = client.post(
            f"/api/v1/subjects/{ctx['subject'].pk}/exams/",
            {"name": "Conv Test"},
            format="json",
        )
        exam_id = resp.data["id"]

        # Set convocation.
        resp = client.put(
            f"/api/v1/exams/{exam_id}/convocation/",
            {"student_ids": [str(student.pk)]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["valid"] == 1

    def test_student_without_group_rejected(self):
        from django.contrib.auth import get_user_model

        user_model = get_user_model()
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        student = user_model.objects.create_user(
            email=f"st-{uuid.uuid4().hex[:8]}@example.com",
            password="Pass123!",  # noqa: S106
            first_name="No",
            last_name="Group",
            organization=ctx["org"],
        )
        SubjectMembership.objects.create(
            organization=ctx["org"],
            user=student,
            subject=ctx["subject"],
            role=MembershipRole.STUDENT,
            group=None,
            is_active=True,
        )

        resp = client.post(
            f"/api/v1/subjects/{ctx['subject'].pk}/exams/",
            {"name": "NoGroup"},
            format="json",
        )
        exam_id = resp.data["id"]

        resp = client.put(
            f"/api/v1/exams/{exam_id}/convocation/",
            {"student_ids": [str(student.pk)]},
            format="json",
        )
        assert resp.data["valid"] == 0
        assert len(resp.data["invalid"]) == 1


# ── Instrumented PDF (RF-6.15, RF-6.16) ─────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestInstrumentedPDF(TestCase):
    def test_download_invalid_pdf_returns_409(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        resp = client.post(
            f"/api/v1/subjects/{ctx['subject'].pk}/exams/",
            {"name": "PDF Test"},
            format="json",
        )
        model = ExamModel.objects.get(exam_id=resp.data["id"])

        resp = client.get(f"/api/v1/models/{model.pk}/instrumented-pdf/")
        assert resp.status_code in (status.HTTP_404_NOT_FOUND, status.HTTP_409_CONFLICT)

    def test_generate_without_qr_zones_fails(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        resp = client.post(
            f"/api/v1/subjects/{ctx['subject'].pk}/exams/",
            {"name": "No QR"},
            format="json",
        )
        model = ExamModel.objects.get(exam_id=resp.data["id"])

        resp = client.post(f"/api/v1/models/{model.pk}/generate-instrumented-pdf/")
        assert resp.status_code == status.HTTP_409_CONFLICT
