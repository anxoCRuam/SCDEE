"""
Integration tests for exams, models, page profiles, zones,
problems, rubrics, convocations, and the instrumented PDF pipeline.

Covers RF-6.1 through RF-6.16 with updated serializers and permissions.
"""

import io
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.audit.models.auditlog import AuditLog
from apps.courses.models.courses import AcademicCourse
from apps.exams.models.exams import (
    Exam,
    ExamConvocation,
    ExamModel,
    Problem,
    RecognitionZone,
)
from apps.organizations.models.organization import Organization
from apps.subjects.models.subjects import (
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
    course = AcademicCourse(organization=org, label="2025-2026", is_active=True)
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


def _create_student_in_subject(org, subject, group_label="G1"):
    """Create a student user, add to subject with a group."""
    user_model = get_user_model()
    student = user_model.objects.create_user(
        email=f"st-{uuid.uuid4().hex[:8]}@example.com",
        password="Pass123!",  # noqa: S106
        first_name="Student",
        last_name="One",
        organization=org,
    )
    group = SubjectGroup.objects.create(subject=subject, label=group_label)
    SubjectMembership.objects.create(
        organization=org,
        user=student,
        subject=subject,
        role=MembershipRole.STUDENT,
        group=group,
        is_active=True,
    )
    return student


def _create_exam(subject, client, name="Test Exam"):
    """Helper to create an exam via the API."""
    resp = client.post(
        f"/api/v1/subjects/{subject.pk}/exams/",
        {"name": name},
        format="json",
    )
    assert resp.status_code == status.HTTP_201_CREATED
    return resp.data["id"]


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
        # ExamFullSerializer devuelve student_permissions y models_problems, convocation
        assert "student_permissions" in resp.data
        assert "models_problems" in resp.data
        assert "convocation" in resp.data

    def test_create_exam_auto_model_a(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        exam_id = _create_exam(ctx["subject"], client, "Parcial 1")
        exam = Exam.unfiltered.get(pk=exam_id)
        assert exam.models.count() == 1
        assert exam.models.first().label == "A"

    def test_list_exams(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        _create_exam(ctx["subject"], client, "E1")
        _create_exam(ctx["subject"], client, "E2")

        resp = client.get(f"/api/v1/subjects/{ctx['subject'].pk}/exams/")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 2

    def test_list_exams_respects_role(self):
        """Teacher sees ExamTeacherSerializer fields (models_problems, convocation)."""
        ctx = _full_setup()
        # Create a teacher membership
        teacher = get_user_model().objects.create_user(
            email=f"t-{uuid.uuid4().hex[:8]}@example.com",
            password="TeachPass123!",  # noqa: S106
            first_name="Teach",
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

        _create_exam(ctx["subject"], _auth_client(ctx["manager"]), "E1")

        client = _auth_client(teacher, password="TeachPass123!")  # noqa: S106
        resp = client.get(f"/api/v1/subjects/{ctx['subject'].pk}/exams/")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 1
        exam_data = resp.data[0]
        # Teacher should have models_problems and convocation, but NO student_permissions
        assert "models_problems" in exam_data
        assert "convocation" in exam_data
        assert "student_permissions" not in exam_data

    def test_exam_detail(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        exam_id = _create_exam(ctx["subject"], client, "Detail Test")

        resp = client.get(f"/api/v1/exams/{exam_id}/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["name"] == "Detail Test"
        # Manager gets Full
        assert "student_permissions" in resp.data
        assert "models_problems" in resp.data

    def test_exam_detail_student_only_if_convoked(self):
        ctx = _full_setup()
        manager_client = _auth_client(ctx["manager"])
        exam_id = _create_exam(ctx["subject"], manager_client, "Student Exam")

        student = _create_student_in_subject(ctx["org"], ctx["subject"])
        student_client = _auth_client(student, password="Pass123!")  # noqa: S106

        # Student not convoked → 404
        resp = student_client.get(f"/api/v1/exams/{exam_id}/")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

        # Convoke student
        from apps.exams.services.exam_service import set_convocation

        set_convocation(exam=Exam.objects.get(pk=exam_id), student_ids=[str(student.pk)])

        # Now student sees basic info
        resp = student_client.get(f"/api/v1/exams/{exam_id}/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["name"] == "Student Exam"
        assert "models_problems" not in resp.data
        assert "convocation" not in resp.data

    def test_update_exam_name(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        exam_id = _create_exam(ctx["subject"], client, "Old Name")
        resp = client.patch(f"/api/v1/exams/{exam_id}/", {"name": "New Name"}, format="json")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["name"] == "New Name"

    def test_update_exam_date(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        exam_id = _create_exam(ctx["subject"], client, "Date Test")
        resp = client.patch(
            f"/api/v1/exams/{exam_id}/",
            {"date": "2025-06-15T10:00:00Z"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["date"] is not None

    def test_delete_exam_without_instances(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        exam_id = _create_exam(ctx["subject"], client, "To Delete")
        resp = client.delete(f"/api/v1/exams/{exam_id}/")
        assert resp.status_code == status.HTTP_204_NO_CONTENT

    def test_audit_log_on_create(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        _create_exam(ctx["subject"], client, "Audit Test")
        assert AuditLog.objects.filter(event_type="EXAM_CREATED").exists()


# ── Model creation (RF-6.5) ─────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestModelCreation(TestCase):
    def test_create_additional_model(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        exam_id = _create_exam(ctx["subject"], client, "Multi Model")
        resp = client.post(
            f"/api/v1/exams/{exam_id}/models/",
            {"label": "B"},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["label"] == "B"
        # ModelDetailSerializer fields
        assert "page_profiles" in resp.data
        assert "blank_pdf_ref" in resp.data

    def test_duplicate_model_label_rejected(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        exam_id = _create_exam(ctx["subject"], client, "Dup")
        resp = client.post(
            f"/api/v1/exams/{exam_id}/models/",
            {"label": "A"},  # already exists
            format="json",
        )
        assert resp.status_code == status.HTTP_409_CONFLICT


# ── PageProfile & Zones (RF-6.8) ────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestPageProfilesAndZones(TestCase):
    @staticmethod
    def _create_exam_with_model_and_blank_pdf(ctx, client, num_pages=1):
        """Crea examen, modelo, y sube un PDF con `num_pages` páginas (mock MinIO). Retorna
        (exam_id, model_pk)."""
        exam_id = _create_exam(ctx["subject"], client, "Zone Test")
        model = ExamModel.objects.get(exam_id=exam_id, label="A")

        # Generar PDF de `num_pages` páginas
        from reportlab.pdfgen import canvas as rl_canvas

        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=(595, 842))
        for i in range(num_pages):
            c.drawString(100, 100, f"Page {i + 1}")
            c.showPage()
        c.save()
        pdf_bytes = buf.getvalue()

        with patch("apps.exams.services.storage.upload_to_minio"):  # noqa: SIM117
            with patch("apps.exams.services.storage.delete_minio_object"):
                from apps.exams.services.exam_service import upload_blank_pdf

                upload_blank_pdf(model, pdf_bytes, f"{num_pages}p.pdf")
        model.refresh_from_db()
        return exam_id, str(model.pk)

    def test_create_page_profile(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        _, model_pk = self._create_exam_with_model_and_blank_pdf(ctx, client)

        resp = client.post(
            f"/api/v1/models/{model_pk}/page-profiles/",
            {"page_number": 1},
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["page_number"] == 1
        assert resp.data["page_width"] > 0

    def test_create_zone(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        _, model_pk = self._create_exam_with_model_and_blank_pdf(ctx, client)

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
        # ZoneResponseSerializer (lista de zonas)
        assert isinstance(resp.data, list)
        assert len(resp.data) == 1
        assert resp.data[0]["zone_type"] == "QR"
        assert resp.data[0]["attribute"] == "exam_qr"

    def test_qr_zone_forces_exam_qr_attribute(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        _, model_pk = self._create_exam_with_model_and_blank_pdf(ctx, client)

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
                "attribute": "something_else",
                "x": 10.0,
                "y": 10.0,
                "width": 80.0,
                "height": 80.0,
            },
            format="json",
        )
        assert resp.data[0]["attribute"] == "exam_qr"

    def test_create_zone_multipage(self):
        """Create zone on multiple pages via page_numbers parameter."""
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        _, model_pk = self._create_exam_with_model_and_blank_pdf(ctx, client, num_pages=2)

        # Create two page profiles first
        for num in (1, 2):
            client.post(
                f"/api/v1/models/{model_pk}/page-profiles/",
                {"page_number": num},
                format="json",
            )

        # Get profile_pk for page 1 (arbitrary, but required for URL)
        profiles_resp = client.get(f"/api/v1/models/{model_pk}/page-profiles/")
        page1_profile = next(p for p in profiles_resp.data if p["page_number"] == 1)

        resp = client.post(
            f"/api/v1/page-profiles/{page1_profile['id']}/zones/",
            {
                "zone_type": "OCR_TEXT",
                "attribute": "name",
                "x": 10,
                "y": 10,
                "width": 200,
                "height": 30,
                "page_numbers": [1, 2],
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert len(resp.data) == 2  # Two zones created
        page_numbers_created = {
            RecognitionZone.objects.get(pk=z["id"]).page_profile.page_number for z in resp.data
        }
        assert page_numbers_created == {1, 2}

    def test_delete_zone(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        _, model_pk = self._create_exam_with_model_and_blank_pdf(ctx, client)

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
        zone_pk = z_resp.data[0]["id"]

        resp = client.delete(f"/api/v1/zones/{zone_pk}/")
        assert resp.status_code == status.HTTP_204_NO_CONTENT

    def test_list_page_profiles(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        _, model_pk = self._create_exam_with_model_and_blank_pdf(ctx, client, num_pages=2)

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

        exam_id = _create_exam(ctx["subject"], client, "Prob Test")
        model = ExamModel.objects.get(exam_id=exam_id, label="A")

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

        exam_id = _create_exam(ctx["subject"], client, "Rub Test")
        model = ExamModel.objects.get(exam_id=exam_id)
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


# ── Convocation (RF-6.4) ────────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestConvocation(TestCase):
    def test_set_convocation(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        student = _create_student_in_subject(ctx["org"], ctx["subject"])
        exam_id = _create_exam(ctx["subject"], client, "Conv Test")

        resp = client.put(
            f"/api/v1/exams/{exam_id}/convocation/",
            {"student_ids": [str(student.pk)]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["valid"] == 1
        assert resp.data["invalid"] == []

    def test_student_without_group_rejected(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        user_model = get_user_model()
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

        exam_id = _create_exam(ctx["subject"], client, "NoGroup")
        resp = client.put(
            f"/api/v1/exams/{exam_id}/convocation/",
            {"student_ids": [str(student.pk)]},
            format="json",
        )
        assert resp.data["valid"] == 0
        assert len(resp.data["invalid"]) == 1

    def test_convocation_diff(self):
        """Ensure that setting a new list removes old students and adds new ones."""
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        student1 = _create_student_in_subject(ctx["org"], ctx["subject"], "G1")
        student2 = _create_student_in_subject(ctx["org"], ctx["subject"], "G2")
        exam_id = _create_exam(ctx["subject"], client, "Diff Test")

        # First convocation with student1
        client.put(
            f"/api/v1/exams/{exam_id}/convocation/",
            {"student_ids": [str(student1.pk)]},
            format="json",
        )
        assert ExamConvocation.objects.filter(exam_id=exam_id, student=student1).exists()

        # Second convocation with student2 only
        client.put(
            f"/api/v1/exams/{exam_id}/convocation/",
            {"student_ids": [str(student2.pk)]},
            format="json",
        )
        assert not ExamConvocation.objects.filter(exam_id=exam_id, student=student1).exists()
        assert ExamConvocation.objects.filter(exam_id=exam_id, student=student2).exists()


# ── Instrumented PDF (RF-6.15, RF-6.16) ─────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestInstrumentedPDF(TestCase):
    def test_generate_without_blank_pdf_fails(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        exam_id = _create_exam(ctx["subject"], client, "No Blank")
        model = ExamModel.objects.get(exam_id=exam_id, label="A")

        resp = client.post(f"/api/v1/models/{model.pk}/qr-pdf/")
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert "error_code" in resp.data

    def test_generate_without_qr_zones_fails(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        exam_id = _create_exam(ctx["subject"], client, "No QR")
        model = ExamModel.objects.get(exam_id=exam_id, label="A")
        # Upload blank pdf (mock)
        with patch("apps.exams.services.storage.upload_to_minio"):  # noqa: SIM117
            with patch("apps.exams.services.storage.delete_minio_object"):
                from reportlab.pdfgen import canvas as rl_canvas

                from apps.exams.services.exam_service import upload_blank_pdf

                buf = io.BytesIO()
                c = rl_canvas.Canvas(buf, pagesize=(595, 842))
                c.showPage()
                c.save()
                upload_blank_pdf(model, buf.getvalue(), "blank.pdf")

        resp = client.post(f"/api/v1/models/{model.pk}/qr-pdf/")
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert resp.data["error_code"] == "NO_QR_ZONES"

    def test_generate_instrumented_pdf_success(self):
        """Full happy path: upload blank PDF, create QR zone, generate PDF."""
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])

        exam_id = _create_exam(ctx["subject"], client, "Full PDF")
        model = ExamModel.objects.get(exam_id=exam_id, label="A")

        # Upload blank PDF (mock MinIO)
        from reportlab.pdfgen import canvas as rl_canvas

        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=(595, 842))
        c.showPage()
        c.save()
        pdf_bytes = buf.getvalue()

        with patch("apps.exams.services.storage.upload_to_minio"):  # noqa: SIM117
            with patch("apps.exams.services.storage.delete_minio_object"):
                from apps.exams.services.exam_service import upload_blank_pdf

                upload_blank_pdf(model, pdf_bytes, "blank.pdf")

        # Create page profile and QR zone
        pp_resp = client.post(
            f"/api/v1/models/{model.pk}/page-profiles/",
            {"page_number": 1},
            format="json",
        )
        profile_pk = pp_resp.data["id"]
        client.post(
            f"/api/v1/page-profiles/{profile_pk}/zones/",
            {
                "zone_type": "QR",
                "attribute": "exam_qr",
                "x": 20,
                "y": 20,
                "width": 100,
                "height": 100,
            },
            format="json",
        )

        # Now generate (mock download from minio to return the blank pdf)
        with patch("apps.exams.services.storage.download_from_minio", return_value=pdf_bytes):
            resp = client.post(f"/api/v1/models/{model.pk}/qr-pdf/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp["Content-Type"] == "application/pdf"
        assert len(resp.content) > 1000


# ── MyExamsView ─────────────────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestMyExams(TestCase):
    def test_my_exams_teacher_sees_limited(self):
        ctx = _full_setup()
        # Create teacher
        teacher = get_user_model().objects.create_user(
            email=f"t-{uuid.uuid4().hex[:8]}@example.com",
            password="TeachPass123!",  # noqa: S106
            first_name="Teach",
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

        _create_exam(ctx["subject"], _auth_client(ctx["manager"]), "Teacher Exam")
        client = _auth_client(teacher, password="TeachPass123!")  # noqa: S106
        resp = client.get("/api/v1/my-exams/")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 1
        # Teacher serializador no tiene student_permissions
        assert "student_permissions" not in resp.data[0]
        assert "models_problems" in resp.data[0]

    def test_my_exams_student_sees_basic(self):
        ctx = _full_setup()
        student = _create_student_in_subject(ctx["org"], ctx["subject"])
        manager_client = _auth_client(ctx["manager"])
        exam_id = _create_exam(ctx["subject"], manager_client, "Student Exam")

        # Convoke student
        from apps.exams.services.exam_service import set_convocation

        set_convocation(exam=Exam.objects.get(pk=exam_id), student_ids=[str(student.pk)])

        client = _auth_client(student, password="Pass123!")  # noqa: S106
        resp = client.get("/api/v1/my-exams/")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 1
        # Basic serializer: no models_problems, no convocation, no student_permissions
        exam_data = resp.data[0]
        assert "name" in exam_data
        assert "models_problems" not in exam_data
        assert "convocation" not in exam_data
        assert "student_permissions" not in exam_data


# ── Endpoint: GET /models/{id} ────────────────────────────
@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestModelDetailAPI(TestCase):
    def test_retrieve_model_detail(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        exam_id = _create_exam(ctx["subject"], client, "Model Detail Test")
        model = ExamModel.objects.get(exam_id=exam_id, label="A")

        resp = client.get(f"/api/v1/models/{model.pk}/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["id"] == str(model.pk)
        assert resp.data["label"] == "A"
        assert "page_profiles" in resp.data
        assert "blank_pdf_ref" in resp.data

    def test_model_detail_not_found(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        resp = client.get(f"/api/v1/models/{uuid.uuid4()}/")
        assert resp.status_code == status.HTTP_404_NOT_FOUND
        assert resp.data["error_code"] == "NOT_FOUND"

    def test_model_detail_forbidden_for_unauthorized(self):
        ctx = _full_setup()
        exam_id = _create_exam(ctx["subject"], _auth_client(ctx["manager"]), "Forbidden Model")
        model = ExamModel.objects.get(exam_id=exam_id)
        # Usuario sin membresía
        unauthorized = get_user_model().objects.create_user(
            email=f"unauth-{uuid.uuid4().hex[:8]}@example.com",
            password="Pass123!",  # noqa: S106
            first_name="No",
            last_name="Access",
            organization=ctx["org"],
        )
        client = _auth_client(unauthorized, password="Pass123!")  # noqa: S106
        resp = client.get(f"/api/v1/models/{model.pk}/")
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ── Endpoint: PUT blank-pdf con multipart ──────────────────
@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestBlankPDFUploadAPI(TestCase):
    def test_upload_blank_pdf_multipart(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        exam_id = _create_exam(ctx["subject"], client, "Blank Upload")
        model = ExamModel.objects.get(exam_id=exam_id)

        pdf_content = io.BytesIO()
        from reportlab.pdfgen import canvas as rl_canvas

        c = rl_canvas.Canvas(pdf_content, pagesize=(595, 842))
        c.showPage()
        c.save()
        pdf_bytes = pdf_content.getvalue()

        upload_file = SimpleUploadedFile("blank.pdf", pdf_bytes, content_type="application/pdf")

        with patch("apps.exams.services.storage.upload_to_minio"):  # noqa: SIM117
            with patch("apps.exams.services.storage.delete_minio_object"):
                resp = client.put(
                    f"/api/v1/models/{model.pk}/blank-pdf/",
                    {"file": upload_file},
                    format="multipart",
                )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["pages"] == 1
        assert len(resp.data["page_dimensions"]) == 1
        assert resp.data["size_bytes"] > 0

    def test_upload_blank_pdf_no_file(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        exam_id = _create_exam(ctx["subject"], client, "NoFile")
        model = ExamModel.objects.get(exam_id=exam_id)

        resp = client.put(f"/api/v1/models/{model.pk}/blank-pdf/", {}, format="multipart")
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data["error_code"] == "NO_FILE_PROVIDED"

    def test_upload_blank_pdf_permission_denied(self):
        ctx = _full_setup()
        exam_id = _create_exam(ctx["subject"], _auth_client(ctx["manager"]), "Perm Denied")
        model = ExamModel.objects.get(exam_id=exam_id)
        student = _create_student_in_subject(ctx["org"], ctx["subject"])
        client = _auth_client(student, password="Pass123!")  # noqa: S106
        resp = client.put(f"/api/v1/models/{model.pk}/blank-pdf/", {}, format="multipart")
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ── Validación de zonas extremas por API ────────────────────
@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestZoneValidationAPI(TestCase):
    @staticmethod
    def _create_profile_and_blank_pdf(ctx, client, num_pages=1):
        exam_id = _create_exam(ctx["subject"], client, "ZoneValid")
        model = ExamModel.objects.get(exam_id=exam_id, label="A")
        # Subir PDF
        from reportlab.pdfgen import canvas as rl_canvas

        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=(595, 842))
        for i in range(num_pages):
            c.drawString(100, 100, f"Page {i + 1}")
            c.showPage()
        c.save()
        with (
            patch("apps.exams.services.storage.upload_to_minio"),
            patch("apps.exams.services.storage.delete_minio_object"),
        ):
            from apps.exams.services.exam_service import upload_blank_pdf

            upload_blank_pdf(model, buf.getvalue(), f"{num_pages}p.pdf")
        # Crear perfil
        pp_resp = client.post(
            f"/api/v1/models/{model.pk}/page-profiles/",
            {"page_number": 1},
            format="json",
        )
        return pp_resp.data["id"]

    def test_create_zone_with_negative_coordinate(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        profile_pk = self._create_profile_and_blank_pdf(ctx, client)

        resp = client.post(
            f"/api/v1/page-profiles/{profile_pk}/zones/",
            {
                "zone_type": "OCR_TEXT",
                "attribute": "name",
                "x": -10,
                "y": 10,
                "width": 100,
                "height": 30,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data["error_code"] == "INVALID_COORDINATES"

    def test_create_zone_outside_page_boundary(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        profile_pk = self._create_profile_and_blank_pdf(ctx, client)

        # Ancho = 595, zona x=500 width=200 → 700 > 595+1
        resp = client.post(
            f"/api/v1/page-profiles/{profile_pk}/zones/",
            {
                "zone_type": "OCR_TEXT",
                "attribute": "name",
                "x": 500,
                "y": 10,
                "width": 200,
                "height": 30,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST
        assert resp.data["error_code"] == "ZONE_EXCEEDS_PAGE_WIDTH"

    def test_create_zone_invalid_type(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        profile_pk = self._create_profile_and_blank_pdf(ctx, client)

        resp = client.post(
            f"/api/v1/page-profiles/{profile_pk}/zones/",
            {
                "zone_type": "INVALID",
                "attribute": "x",
                "x": 10,
                "y": 10,
                "width": 10,
                "height": 10,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST


# ── Validación de perfil de página (sin PDF) ───────────────
@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestPageProfileValidationAPI(TestCase):
    def test_create_page_profile_without_blank_pdf(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        exam_id = _create_exam(ctx["subject"], client, "NoBlank")
        model = ExamModel.objects.get(exam_id=exam_id)

        resp = client.post(
            f"/api/v1/models/{model.pk}/page-profiles/",
            {"page_number": 1},
            format="json",
        )
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert resp.data["error_code"] == "NO_BLANK_PDF"

    def test_create_page_profile_invalid_page_number(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        exam_id = _create_exam(ctx["subject"], client, "PageRange")
        model = ExamModel.objects.get(exam_id=exam_id)

        # Subir PDF de 1 página
        from reportlab.pdfgen import canvas as rl_canvas

        buf = io.BytesIO()
        c = rl_canvas.Canvas(buf, pagesize=(595, 842))
        c.showPage()
        c.save()
        with (
            patch("apps.exams.services.storage.upload_to_minio"),
            patch("apps.exams.services.storage.delete_minio_object"),
        ):
            from apps.exams.services.exam_service import upload_blank_pdf

            upload_blank_pdf(model, buf.getvalue(), "1p.pdf")

        resp = client.post(
            f"/api/v1/models/{model.pk}/page-profiles/",
            {"page_number": 5},  # fuera de rango
            format="json",
        )
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert resp.data["error_code"] == "INVALID_PAGE_NUMBER"


# ── Copia de estructura de modelo (copy_from_id) ────────────
@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestModelCopyStructure(TestCase):
    def test_copy_model_with_zones_and_problems(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        exam_id = _create_exam(ctx["subject"], client, "Copy Test")

        # Crear modelo original "B"
        resp = client.post(f"/api/v1/exams/{exam_id}/models/", {"label": "B"}, format="json")
        orig_model = ExamModel.objects.get(pk=resp.data["id"])

        # Subir PDF
        buf = io.BytesIO()
        from reportlab.pdfgen import canvas as rl_canvas

        c = rl_canvas.Canvas(buf, pagesize=(595, 842))
        c.showPage()
        c.save()
        with (
            patch("apps.exams.services.storage.upload_to_minio"),
            patch("apps.exams.services.storage.delete_minio_object"),
        ):
            from apps.exams.services.exam_service import upload_blank_pdf

            upload_blank_pdf(orig_model, buf.getvalue(), "b.pdf")

        # Crear perfil y zona
        pp_resp = client.post(
            f"/api/v1/models/{orig_model.pk}/page-profiles/", {"page_number": 1}, format="json"
        )
        client.post(
            f"/api/v1/page-profiles/{pp_resp.data['id']}/zones/",
            {
                "zone_type": "OCR_TEXT",
                "attribute": "name",
                "x": 10,
                "y": 10,
                "width": 100,
                "height": 20,
            },
            format="json",
        )
        # Crear problema con zona y rúbrica
        zone_id = RecognitionZone.objects.filter(page_profile__exam_model=orig_model).first().pk
        problem_resp = client.post(
            f"/api/v1/models/{orig_model.pk}/problems/",
            {"name": "P1", "max_score": "5.00", "order": 1, "zone_ids": [str(zone_id)]},
            format="json",
        )
        problem_pk = problem_resp.data["id"]
        client.post(
            f"/api/v1/problems/{problem_pk}/rubric/",
            {"criteria": [{"description": "Ok", "score": "5.00"}]},
            format="json",
        )

        # Crear nuevo modelo "C" copiando de "B"
        resp_copy = client.post(
            f"/api/v1/exams/{exam_id}/models/",
            {"label": "C", "copy_from_id": str(orig_model.pk)},
            format="json",
        )
        assert resp_copy.status_code == status.HTTP_201_CREATED
        new_model = ExamModel.objects.get(pk=resp_copy.data["id"])
        assert new_model.label == "C"
        # Debe tener el mismo PDF reference
        assert new_model.blank_pdf_ref == orig_model.blank_pdf_ref
        # Debe tener un perfil de página
        assert new_model.page_profiles.count() == 1
        new_profile = new_model.page_profiles.first()
        # Debe tener una zona copiada
        assert new_profile.zones.count() == 1
        # Debe tener un problema copiado
        assert new_model.problems.count() == 1
        new_problem = new_model.problems.first()
        assert new_problem.name == "P1"
        # La zona del problema debe estar enlazada a la nueva zona
        assert new_problem.zones.count() == 1
        assert new_problem.zones.first().page_profile == new_profile
        # Rúbrica copiada
        assert new_problem.rubric_criteria.count() == 1

    def test_copy_from_non_existent_model(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        exam_id = _create_exam(ctx["subject"], client, "Bad Copy")
        resp = client.post(
            f"/api/v1/exams/{exam_id}/models/",
            {"label": "D", "copy_from_id": str(uuid.uuid4())},
            format="json",
        )
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert resp.data["error_code"] == "SOURCE_MODEL_NOT_FOUND"


# ── Convocation edge cases ──────────────────────────────────
@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestConvocationEdgeCases(TestCase):
    def test_set_empty_convocation_clears_all(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        student = _create_student_in_subject(ctx["org"], ctx["subject"])
        exam_id = _create_exam(ctx["subject"], client, "Clear Conv")
        client.put(
            f"/api/v1/exams/{exam_id}/convocation/",
            {"student_ids": [str(student.pk)]},
            format="json",
        )
        assert ExamConvocation.objects.filter(exam_id=exam_id).exists()

        resp = client.put(
            f"/api/v1/exams/{exam_id}/convocation/", {"student_ids": []}, format="json"
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["valid"] == 0
        assert not ExamConvocation.objects.filter(exam_id=exam_id).exists()

    def test_duplicate_student_ids_in_request(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        student = _create_student_in_subject(ctx["org"], ctx["subject"])
        exam_id = _create_exam(ctx["subject"], client, "Dup Students")
        resp = client.put(
            f"/api/v1/exams/{exam_id}/convocation/",
            {"student_ids": [str(student.pk), str(student.pk)]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        # El servicio maneja conjuntos, no debería duplicar
        assert ExamConvocation.objects.filter(exam_id=exam_id).count() == 1


# ── Generación de PDF instrumentado (post-delete QR) ────────
@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestInstrumentedPDFEdgeCases(TestCase):
    def test_generate_after_qr_zone_deleted_fails(self):
        ctx = _full_setup()
        client = _auth_client(ctx["manager"])
        exam_id = _create_exam(ctx["subject"], client, "DelQR")
        model = ExamModel.objects.get(exam_id=exam_id)
        # Subir PDF
        buf = io.BytesIO()
        from reportlab.pdfgen import canvas as rl_canvas

        c = rl_canvas.Canvas(buf, pagesize=(595, 842))
        c.showPage()
        c.save()
        with (
            patch("apps.exams.services.storage.upload_to_minio"),
            patch("apps.exams.services.storage.delete_minio_object"),
        ):
            from apps.exams.services.exam_service import upload_blank_pdf

            upload_blank_pdf(model, buf.getvalue(), "blank.pdf")
        # Crear perfil y zona QR
        pp_resp = client.post(
            f"/api/v1/models/{model.pk}/page-profiles/", {"page_number": 1}, format="json"
        )
        zone_resp = client.post(
            f"/api/v1/page-profiles/{pp_resp.data['id']}/zones/",
            {
                "zone_type": "QR",
                "attribute": "exam_qr",
                "x": 20,
                "y": 20,
                "width": 100,
                "height": 100,
            },
            format="json",
        )
        zone_pk = zone_resp.data[0]["id"]
        # Borrar la zona QR
        client.delete(f"/api/v1/zones/{zone_pk}/")
        # Intentar generar
        with patch("apps.exams.services.storage.download_from_minio", return_value=buf.getvalue()):
            resp = client.post(f"/api/v1/models/{model.pk}/qr-pdf/")
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert resp.data["error_code"] == "NO_QR_ZONES"


# ── Permisos en problemas y rúbricas ────────────────────────
@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestProblemAndRubricPermissions(TestCase):
    def test_create_problem_requires_can_create_rubric(self):
        ctx = _full_setup()
        # Coordinator sin permiso explícito? Por defecto coordinator tiene todos los permisos.
        # Creamos un teacher con permiso limitado (sin can_create_rubric).
        teacher = get_user_model().objects.create_user(
            email=f"t-{uuid.uuid4().hex[:8]}@example.com",
            password="TeachPass123!",  # noqa: S106
            first_name="Teach",
            last_name="User",
            organization=ctx["org"],
        )
        membership = SubjectMembership.objects.create(
            organization=ctx["org"],
            user=teacher,
            subject=ctx["subject"],
            role=MembershipRole.TEACHER,
            is_active=True,
        )
        # Quitar permiso can_create_rubric
        membership.permission_overrides = {"can_create_rubric": False}
        membership.save()

        exam_id = _create_exam(ctx["subject"], _auth_client(ctx["manager"]), "Perms")
        model = ExamModel.objects.get(exam_id=exam_id, label="A")
        client = _auth_client(teacher, password="TeachPass123!")  # noqa: S106
        resp = client.post(
            f"/api/v1/models/{model.pk}/problems/",
            {"name": "P", "max_score": "5.00"},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_set_rubric_requires_can_create_rubric(self):
        ctx = _full_setup()
        # Similar setup
        teacher = get_user_model().objects.create_user(
            email=f"t-{uuid.uuid4().hex[:8]}@example.com",
            password="TeachPass123!",  # noqa: S106
            first_name="Teach",
            last_name="User",
            organization=ctx["org"],
        )
        membership = SubjectMembership.objects.create(
            organization=ctx["org"],
            user=teacher,
            subject=ctx["subject"],
            role=MembershipRole.TEACHER,
            is_active=True,
        )
        membership.permission_overrides = {"can_create_rubric": False}
        membership.save()

        exam_id = _create_exam(ctx["subject"], _auth_client(ctx["manager"]), "RubPerm")
        model = ExamModel.objects.get(exam_id=exam_id, label="A")
        problem = Problem.objects.create(
            name="P1", max_score=Decimal("5.00"), exam_model=model, order=1
        )

        client = _auth_client(teacher, password="TeachPass123!")  # noqa: S106
        resp = client.post(
            f"/api/v1/problems/{problem.pk}/rubric/",
            {"criteria": [{"description": "A", "score": "2.00"}]},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ── MyExams edge cases ──────────────────────────────────────
@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestMyExamsEdgeCases(TestCase):
    def test_my_exams_without_active_course(self):
        # Crear usuario sin curso activo
        user_model = get_user_model()
        org = Organization.objects.create(
            name="Orphan Org", subdomain=f"orph-{uuid.uuid4().hex[:8]}"
        )
        user = user_model.objects.create_user(
            email=f"orphan-{uuid.uuid4().hex[:8]}@example.com",
            password="Pass123!",  # noqa: S106
            first_name="No",
            last_name="Course",
            organization=org,
        )
        client = _auth_client(user, password="Pass123!")  # noqa: S106
        resp = client.get("/api/v1/my-exams/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data == []

    def test_my_exams_user_without_memberships_returns_empty(self):
        ctx = _full_setup()
        # Usuario con org y curso activo pero sin membresías
        user = get_user_model().objects.create_user(
            email=f"nomem-{uuid.uuid4().hex[:8]}@example.com",
            password="Pass123!",  # noqa: S106
            first_name="No",
            last_name="Membership",
            organization=ctx["org"],
        )
        client = _auth_client(user, password="Pass123!")  # noqa: S106
        resp = client.get("/api/v1/my-exams/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data == []
