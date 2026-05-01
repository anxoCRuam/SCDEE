"""
Integration tests for annotations (RF-10).

Covers: creation (text, stylus, audio stub), listing with filters,
update (author-only), deletion, OCR grade extraction, and audit.
"""

import uuid
from decimal import Decimal

import pytest
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.annotations.models import Annotation
from apps.audit.models import AuditLog
from apps.courses.models import AcademicCourse
from apps.exams.models import Exam, ExamModel, Problem
from apps.instances.models import ExamInstance, InstanceStatus
from apps.organizations.models import Organization
from apps.subjects.models import MembershipRole, Subject, SubjectMembership

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
    subject = Subject(
        organization=org,
        name="S",
        code=f"S{uuid.uuid4().hex[:4]}",
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
    exam = Exam.objects.create(organization=org, name="Exam", subject=subject)
    model = ExamModel.objects.create(label="A", exam=exam)
    problem = Problem.objects.create(
        name="P1", max_score=Decimal("10.00"), exam_model=model, order=1
    )
    instance = ExamInstance.objects.create(
        organization=org,
        exam=exam,
        model=model,
        status=InstanceStatus.PENDING_GRADING,
        expected_pages=1,
    )
    return {
        "org": org,
        "manager": manager,
        "coord": coord,
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


# ── Creation tests (RF-10.1) ────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestAnnotationCreation(TestCase):
    def test_create_text_annotation(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.post(
            f"/api/v1/instances/{ctx['instance'].pk}/annotations/",
            {
                "annotation_type": "TEXT",
                "payload": "Good work on this problem!",
                "page_number": 1,
                "x": 50.0,
                "y": 30.0,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["annotation_type"] == "TEXT"
        assert resp.data["payload"] == "Good work on this problem!"

    def test_create_stylus_annotation(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        strokes = '[{"x": 10, "y": 10}, {"x": 50, "y": 50}]'
        resp = client.post(
            f"/api/v1/instances/{ctx['instance'].pk}/annotations/",
            {
                "annotation_type": "STYLUS",
                "payload": strokes,
                "page_number": 2,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["annotation_type"] == "STYLUS"

    def test_create_annotation_with_problem(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.post(
            f"/api/v1/instances/{ctx['instance'].pk}/annotations/",
            {
                "annotation_type": "TEXT",
                "payload": "Check your algebra",
                "problem_id": str(ctx["problem"].pk),
                "page_number": 1,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["problem_id"] == str(ctx["problem"].pk)

    def test_empty_text_payload_rejected(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.post(
            f"/api/v1/instances/{ctx['instance'].pk}/annotations/",
            {"annotation_type": "TEXT", "payload": ""},
            format="json",
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST

    def test_audit_log_created(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        client.post(
            f"/api/v1/instances/{ctx['instance'].pk}/annotations/",
            {"annotation_type": "TEXT", "payload": "Audit test"},
            format="json",
        )
        assert AuditLog.objects.filter(event_type="ANNOTATION_CREATED").exists()


# ── Listing tests (RF-10.4) ─────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestAnnotationListing(TestCase):
    def test_list_annotations(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        # Create two annotations.
        client.post(
            f"/api/v1/instances/{ctx['instance'].pk}/annotations/",
            {"annotation_type": "TEXT", "payload": "Note 1", "page_number": 1},
            format="json",
        )
        client.post(
            f"/api/v1/instances/{ctx['instance'].pk}/annotations/",
            {"annotation_type": "TEXT", "payload": "Note 2", "page_number": 2},
            format="json",
        )

        resp = client.get(f"/api/v1/instances/{ctx['instance'].pk}/annotations/")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 2

    def test_filter_by_page_number(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        client.post(
            f"/api/v1/instances/{ctx['instance'].pk}/annotations/",
            {"annotation_type": "TEXT", "payload": "Page 1", "page_number": 1},
            format="json",
        )
        client.post(
            f"/api/v1/instances/{ctx['instance'].pk}/annotations/",
            {"annotation_type": "TEXT", "payload": "Page 2", "page_number": 2},
            format="json",
        )

        resp = client.get(f"/api/v1/instances/{ctx['instance'].pk}/annotations/?page_number=1")
        assert len(resp.data) == 1
        assert resp.data[0]["page_number"] == 1

    def test_filter_by_type(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        client.post(
            f"/api/v1/instances/{ctx['instance'].pk}/annotations/",
            {"annotation_type": "TEXT", "payload": "Text note"},
            format="json",
        )
        client.post(
            f"/api/v1/instances/{ctx['instance'].pk}/annotations/",
            {"annotation_type": "STYLUS", "payload": "[]"},
            format="json",
        )

        resp = client.get(
            f"/api/v1/instances/{ctx['instance'].pk}/annotations/?annotation_type=TEXT"
        )
        assert len(resp.data) == 1


# ── Update tests (RF-10.2) ──────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestAnnotationUpdate(TestCase):
    def test_author_can_update(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.post(
            f"/api/v1/instances/{ctx['instance'].pk}/annotations/",
            {"annotation_type": "TEXT", "payload": "Old text"},
            format="json",
        )
        annotation_id = resp.data["id"]

        resp = client.put(
            f"/api/v1/annotations/{annotation_id}/",
            {"payload": "New text"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["payload"] == "New text"

    def test_non_author_cannot_update(self):
        ctx = _full_setup()
        # Create annotation as manager.
        mgr_client = _auth(ctx["manager"])
        resp = mgr_client.post(
            f"/api/v1/instances/{ctx['instance'].pk}/annotations/",
            {"annotation_type": "TEXT", "payload": "Manager note"},
            format="json",
        )
        annotation_id = resp.data["id"]

        # Try to update as coordinator (different user).
        coord_client = _auth(ctx["coord"], pw="Pass123!")
        resp = coord_client.put(
            f"/api/v1/annotations/{annotation_id}/",
            {"payload": "Coordinator edit"},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN


# ── Deletion tests (RF-10.3) ────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestAnnotationDeletion(TestCase):
    def test_author_can_delete(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.post(
            f"/api/v1/instances/{ctx['instance'].pk}/annotations/",
            {"annotation_type": "TEXT", "payload": "To delete"},
            format="json",
        )
        annotation_id = resp.data["id"]

        resp = client.delete(f"/api/v1/annotations/{annotation_id}/")
        assert resp.status_code == status.HTTP_204_NO_CONTENT

        assert not Annotation.objects.filter(pk=annotation_id).exists()

    def test_manager_can_delete_others_annotation(self):
        ctx = _full_setup()

        # Create annotation as coordinator.
        coord_client = _auth(ctx["coord"], pw="Pass123!")
        resp = coord_client.post(
            f"/api/v1/instances/{ctx['instance'].pk}/annotations/",
            {"annotation_type": "TEXT", "payload": "Coord note"},
            format="json",
        )
        annotation_id = resp.data["id"]

        # Manager can delete it.
        mgr_client = _auth(ctx["manager"])
        resp = mgr_client.delete(f"/api/v1/annotations/{annotation_id}/")
        assert resp.status_code == status.HTTP_204_NO_CONTENT


# ── Grade annotation tests (RF-10.5, RF-9.12) ───────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestGradeAnnotation(TestCase):
    def test_text_grade_annotation_auto_applies(self):
        """A text annotation marked as grade with a number auto-applies the grade."""
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.post(
            f"/api/v1/instances/{ctx['instance'].pk}/annotations/",
            {
                "annotation_type": "TEXT",
                "payload": "7.5",
                "problem_id": str(ctx["problem"].pk),
                "is_grade_annotation": True,
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED

        # Check that the grade was applied.
        from apps.grading.models import Grade

        grade = Grade.objects.filter(instance=ctx["instance"], problem=ctx["problem"]).first()
        assert grade is not None
        assert grade.score == Decimal("7.5")

    def test_ocr_grade_endpoint(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.post(
            f"/api/v1/instances/{ctx['instance'].pk}/annotations/",
            {
                "annotation_type": "TEXT",
                "payload": "8.0",
                "problem_id": str(ctx["problem"].pk),
            },
            format="json",
        )
        annotation_id = resp.data["id"]

        resp = client.post(f"/api/v1/annotations/{annotation_id}/ocr-grade/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["recognized_value"] is not None
