"""
Integration tests for instances, state machine, grading, assignments,
and the new access-control / anti-download mechanisms.

Covers RF-7.2 through RF-7.15, RF-8.1 through RF-8.6, RF-11.1 through RF-11.6,
RF-16.6, RF-16.7.
"""

import io
import uuid
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.courses.models.courses import AcademicCourse
from apps.exams.models.exams import Exam, ExamModel, Problem, RubricCriterion
from apps.grading.models.grading import AssignmentRule, Grade
from apps.instances.models.instances import (
    ExamInstance,
    ExamPage,
    InstanceStatus,
    PageStatus,
    is_valid_transition,
)
from apps.organizations.models.organization import Organization
from apps.reviews.models.reviews import ExamReview
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

    return {
        "org": org,
        "manager": manager,
        "coord": coord,
        "subject": subject,
        "exam": exam,
        "model": model,
        "problem": problem,
    }


def _create_instance(ctx, student=None, status=InstanceStatus.ASSEMBLING, model=None):
    return ExamInstance.objects.create(
        organization=ctx["org"],
        exam=ctx["exam"],
        model=model if model else ctx["model"],
        student=student,
        status=status,
        expected_pages=2,
    )


def _auth(user, pw="MgrPass123!"):
    reset_auth_plugin()
    c = APIClient()
    r = c.post("/api/v1/auth/login/", {"email": user.email, "password": pw}, format="json")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access_token']}")
    return c


# ── State machine unit tests (RF-7.5) ───────────────────────


class TestStateMachine:
    def test_valid_forward_transitions(self):
        assert is_valid_transition("ASSEMBLING", "RECEIVED")
        assert is_valid_transition("RECEIVED", "QUEUED")
        assert is_valid_transition("QUEUED", "PENDING_GRADING")
        assert is_valid_transition("PENDING_GRADING", "GRADED")
        assert is_valid_transition("GRADED", "PUBLISHED")
        assert is_valid_transition("PUBLISHED", "IN_REVIEW")
        assert is_valid_transition("IN_REVIEW", "FINALIZED")

    def test_valid_retrocesos(self):
        assert is_valid_transition("GRADED", "PENDING_GRADING")
        assert is_valid_transition("PUBLISHED", "GRADED")
        assert is_valid_transition("FINALIZED", "PENDING_REVIEW")

    def test_invalid_transitions(self):
        assert not is_valid_transition("ASSEMBLING", "GRADED")
        assert not is_valid_transition("PUBLISHED", "ASSEMBLING")
        assert not is_valid_transition("ARCHIVED", "RECEIVED")
        assert not is_valid_transition("PENDING_GRADING", "PUBLISHED")


# ── State transition endpoint (RF-7.5) ──────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestTransitionEndpoint(TestCase):
    def test_valid_transition(self):
        ctx = _full_setup()
        inst = _create_instance(ctx, status=InstanceStatus.PENDING_GRADING)
        client = _auth(ctx["manager"])
        resp = client.patch(
            f"/api/v1/instances/{inst.pk}/transition/",
            {"target_status": "GRADED"},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["status"] == "GRADED"

    def test_invalid_transition_returns_409(self):
        ctx = _full_setup()
        inst = _create_instance(ctx)
        client = _auth(ctx["manager"])
        resp = client.patch(
            f"/api/v1/instances/{inst.pk}/transition/",
            {"target_status": "PUBLISHED"},
            format="json",
        )
        assert resp.status_code == status.HTTP_409_CONFLICT

    def test_publish_blocked_with_issues(self):
        ctx = _full_setup()
        inst = _create_instance(ctx, status=InstanceStatus.GRADED)
        inst.has_issues = True
        inst.save()
        client = _auth(ctx["manager"])
        resp = client.patch(
            f"/api/v1/instances/{inst.pk}/transition/",
            {"target_status": "PUBLISHED"},
            format="json",
        )
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert resp.data["error_code"] == "HAS_UNRESOLVED_ISSUES"


# ── Instance CRUD (RF-7.2, RF-7.3, RF-7.10) ────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestInstanceCRUD(TestCase):
    def test_list_instances(self):
        ctx = _full_setup()
        _create_instance(ctx)
        _create_instance(ctx)
        client = _auth(ctx["manager"])
        resp = client.get(f"/api/v1/exams/{ctx['exam'].pk}/instances/")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data["results"]) == 2

    def test_retrieve_instance(self):
        ctx = _full_setup()
        inst = _create_instance(ctx)
        client = _auth(ctx["manager"])
        resp = client.get(f"/api/v1/instances/{inst.pk}/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["status"] == "ASSEMBLING"

    def test_update_instance_student(self):
        ctx = _full_setup()
        student = get_user_model().objects.create_user(
            email=f"st-{uuid.uuid4().hex[:8]}@x.com",
            password="P123!",  # noqa: S106
            first_name="S",
            last_name="T",
            organization=ctx["org"],
        )
        inst = _create_instance(ctx)
        client = _auth(ctx["manager"])
        resp = client.patch(
            f"/api/v1/instances/{inst.pk}/",
            {"student_id": str(student.pk)},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["student_id"] == str(student.pk)

    def test_delete_instance(self):
        ctx = _full_setup()
        inst = _create_instance(ctx)
        client = _auth(ctx["manager"])
        resp = client.delete(f"/api/v1/instances/{inst.pk}/")
        assert resp.status_code == status.HTTP_204_NO_CONTENT


# ── Manual grading (RF-11.1) ────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestManualGrading(TestCase):
    def test_grade_problem(self):
        ctx = _full_setup()
        inst = _create_instance(ctx, status=InstanceStatus.PENDING_GRADING)
        client = _auth(ctx["manager"])
        resp = client.put(
            f"/api/v1/instances/{inst.pk}/problems/{ctx['problem'].pk}/grade/",
            {"score": "7.50", "old_score": None},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["score"] == "7.50"

    def test_version_conflict(self):
        ctx = _full_setup()
        inst = _create_instance(ctx, status=InstanceStatus.PENDING_GRADING)
        # Create an existing grade with a different score to simulate conflict
        Grade.objects.create(
            problem=ctx["problem"], instance=inst, score=Decimal("9.00"), grader=ctx["manager"]
        )
        client = _auth(ctx["manager"])
        resp = client.put(
            f"/api/v1/instances/{inst.pk}/problems/{ctx['problem'].pk}/grade/",
            {"score": "7.00", "old_score": "3.00"},  # mismatch
            format="json",
        )
        assert resp.status_code == status.HTTP_409_CONFLICT
        assert resp.data["error_code"] == "VERSION_CONFLICT"

    def test_total_score_recalculated(self):
        ctx = _full_setup()
        inst = _create_instance(ctx, status=InstanceStatus.PENDING_GRADING)
        p2 = Problem.objects.create(
            name="P2", max_score=Decimal("5.00"), exam_model=ctx["model"], order=2
        )
        client = _auth(ctx["manager"])
        client.put(
            f"/api/v1/instances/{inst.pk}/problems/{ctx['problem'].pk}/grade/",
            {"score": "8.00", "old_score": None},
            format="json",
        )
        client.put(
            f"/api/v1/instances/{inst.pk}/problems/{p2.pk}/grade/",
            {"score": "4.00", "old_score": None},
            format="json",
        )
        inst.refresh_from_db()
        assert inst.total_score == Decimal("12.00")


# ── Rubric grading (RF-11.2) ────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestRubricGrading(TestCase):
    def test_grade_by_rubric(self):
        ctx = _full_setup()
        c1 = RubricCriterion.objects.create(
            problem=ctx["problem"], description="Good", score=Decimal("7.00"), order=1
        )
        c2 = RubricCriterion.objects.create(
            problem=ctx["problem"], description="Clean", score=Decimal("3.00"), order=2
        )
        inst = _create_instance(ctx, status=InstanceStatus.PENDING_GRADING)
        client = _auth(ctx["manager"])
        resp = client.put(
            f"/api/v1/instances/{inst.pk}/problems/{ctx['problem'].pk}/rubric/",
            {"criterion_ids": [str(c1.pk), str(c2.pk)], "old_score": None},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["score"] == "10.00"


# ── Assignment rules (RF-8.1, RF-8.5) ───────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestAssignmentRules(TestCase):
    def test_create_rule(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])
        resp = client.post(
            f"/api/v1/exams/{ctx['exam'].pk}/assignment-rules/",
            {
                "problems": [str(ctx["problem"].pk)],
                "correctors": [str(ctx["coord"].pk)],
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED

    def test_list_rules(self):
        ctx = _full_setup()
        AssignmentRule.objects.create(
            exam=ctx["exam"],
            problems=[str(ctx["problem"].pk)],
            correctors=[str(ctx["coord"].pk)],
        )
        client = _auth(ctx["manager"])
        resp = client.get(f"/api/v1/exams/{ctx['exam'].pk}/assignment-rules/")
        assert len(resp.data) == 1

    def test_coverage_check(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])
        resp = client.get(f"/api/v1/exams/{ctx['exam'].pk}/coverage/")
        assert resp.data["complete"] is False

        AssignmentRule.objects.create(
            exam=ctx["exam"],
            problems=[str(ctx["problem"].pk)],
            correctors=[str(ctx["coord"].pk)],
        )
        resp = client.get(f"/api/v1/exams/{ctx['exam'].pk}/coverage/")
        assert resp.data["complete"] is True


# ── Page management (RF-7.14) ───────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestPageManagement(TestCase):
    def test_list_pages(self):
        ctx = _full_setup()
        inst = _create_instance(ctx)
        ExamPage.objects.create(instance=inst, page_number=1, storage_ref="test/p1.pdf")
        ExamPage.objects.create(instance=inst, page_number=2, storage_ref="test/p2.pdf")
        client = _auth(ctx["manager"])
        resp = client.get(f"/api/v1/instances/{inst.pk}/pages/")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 2

    def test_discard_page(self):
        ctx = _full_setup()
        inst = _create_instance(ctx)
        page = ExamPage.objects.create(instance=inst, page_number=1, storage_ref="test/p1.pdf")
        client = _auth(ctx["manager"])
        resp = client.delete(f"/api/v1/instances/{inst.pk}/pages/{page.pk}/")
        assert resp.status_code == status.HTTP_204_NO_CONTENT
        page.refresh_from_db()
        assert page.status == PageStatus.DISCARDED

    def test_reorder_pages(self):
        ctx = _full_setup()
        inst = _create_instance(ctx)
        p1 = ExamPage.objects.create(instance=inst, page_number=1, storage_ref="t/1.pdf")
        p2 = ExamPage.objects.create(instance=inst, page_number=2, storage_ref="t/2.pdf")
        client = _auth(ctx["manager"])
        resp = client.patch(
            f"/api/v1/instances/{inst.pk}/pages/reorder/",
            {"page_ids": [str(p2.pk), str(p1.pk)]},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        p1.refresh_from_db()
        p2.refresh_from_db()
        assert p2.page_number == 1
        assert p1.page_number == 2


# ── Bulk publish (RF-7.9) ───────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestBulkPublish(TestCase):
    def test_publish_graded_instances(self):
        ctx = _full_setup()
        inst1 = _create_instance(ctx, status=InstanceStatus.GRADED)
        inst1.has_issues = False
        inst1.save()
        inst2 = _create_instance(ctx, status=InstanceStatus.GRADED)
        inst2.has_issues = True
        inst2.save()
        client = _auth(ctx["manager"])
        resp = client.post(f"/api/v1/exams/{ctx['exam'].pk}/publish/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["published"] == 1
        assert resp.data["skipped"] == 1
        inst1.refresh_from_db()
        assert inst1.status == InstanceStatus.PUBLISHED


# ══════════════════════════════════════════════════════════════
# NEW TESTS: Access control & anti-download mechanisms
# ══════════════════════════════════════════════════════════════


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestStudentAccessControl(TestCase):
    """Tests that students only see their own instances and obey review window permissions."""

    def setUp(self):
        self.ctx = _full_setup()
        self.student = get_user_model().objects.create_user(
            email=f"stu-{uuid.uuid4().hex[:8]}@x.com",
            password="StudPass123!",  # noqa: S106
            first_name="Student",
            last_name="One",
            organization=self.ctx["org"],
        )
        # Add student to subject with group
        group = SubjectGroup.objects.create(subject=self.ctx["subject"], label="G1")
        SubjectMembership.objects.create(
            organization=self.ctx["org"],
            user=self.student,
            subject=self.ctx["subject"],
            role=MembershipRole.STUDENT,
            group=group,
            is_active=True,
        )
        self.student_client = _auth(self.student, pw="StudPass123!")
        self.manager_client = _auth(self.ctx["manager"])

    def test_student_list_only_own_instance(self):
        other_student = get_user_model().objects.create_user(
            email=f"other-{uuid.uuid4().hex[:8]}@x.com",
            password="Pass123!",  # noqa: S106
            first_name="Other",
            last_name="Student",
            organization=self.ctx["org"],
        )
        inst_own = _create_instance(
            self.ctx, student=self.student, status=InstanceStatus.PUBLISHED
        )
        _create_instance(self.ctx, student=other_student)

        resp = self.student_client.get(f"/api/v1/exams/{self.ctx['exam'].pk}/instances/")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 1
        assert resp.data[0]["id"] == str(inst_own.pk)

    def test_student_detail_basic_info(self):
        inst = _create_instance(self.ctx, student=self.student, status=InstanceStatus.PUBLISHED)
        resp = self.student_client.get(f"/api/v1/instances/{inst.pk}/")
        assert resp.status_code == status.HTTP_200_OK
        assert "total_score" in resp.data
        # Without review window, no detailed data
        assert "pages" not in resp.data
        assert "annotations" not in resp.data

    def test_student_detail_during_review_with_permissions(self):
        inst = _create_instance(self.ctx, student=self.student, status=InstanceStatus.IN_REVIEW)
        # Create review window in OPEN state
        from datetime import UTC, datetime, timedelta

        ExamReview.objects.create(
            exam=self.ctx["exam"],
            start_date=datetime.now(UTC) - timedelta(hours=1),
            end_date=datetime.now(UTC) + timedelta(hours=1),
            status="OPEN",
        )
        resp = self.student_client.get(f"/api/v1/instances/{inst.pk}/")
        assert resp.status_code == status.HTTP_200_OK
        # With can_view_pages=True (default permissions), pages should appear
        assert "pages" in resp.data
        assert "rubric" in resp.data

    def test_student_detail_after_review_can_view_after(self):
        inst = _create_instance(self.ctx, student=self.student, status=InstanceStatus.FINALIZED)
        # Review closed, but can_view_pages_after_review=True
        from datetime import UTC, datetime, timedelta

        ExamReview.objects.create(
            exam=self.ctx["exam"],
            start_date=datetime.now(UTC) - timedelta(days=2),
            end_date=datetime.now(UTC) - timedelta(days=1),
            status="CLOSED",
        )
        self.ctx["exam"].student_permissions["can_view_pages_after_review"] = True
        self.ctx["exam"].save()
        resp = self.student_client.get(f"/api/v1/instances/{inst.pk}/")
        assert resp.status_code == status.HTTP_200_OK
        assert "pages" in resp.data

    def test_student_cannot_see_other_instance(self):
        other_student = get_user_model().objects.create_user(
            email=f"other-{uuid.uuid4().hex[:8]}@x.com",
            password="Pass123!",  # noqa: S106
            first_name="Other",
            last_name="Student",
            organization=self.ctx["org"],
        )
        inst_other = _create_instance(self.ctx, student=other_student)
        resp = self.student_client.get(f"/api/v1/instances/{inst_other.pk}/")
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    def test_student_list_only_own_and_with_basic_serializer(self):
        """Student listing returns only their instance with basic fields."""
        other_student = get_user_model().objects.create_user(
            email=f"other-{uuid.uuid4().hex[:8]}@x.com",
            password="Pass123!",  # noqa: S106
            first_name="Other",
            last_name="Student",
            organization=self.ctx["org"],
        )
        inst_own = _create_instance(
            self.ctx, student=self.student, status=InstanceStatus.PUBLISHED
        )
        _create_instance(self.ctx, student=other_student, status=InstanceStatus.PUBLISHED)

        resp = self.student_client.get(f"/api/v1/exams/{self.ctx['exam'].pk}/instances/")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 1
        assert resp.data[0]["id"] == str(inst_own.pk)
        # Verifica que no incluye campos de InstanceResponseSerializer (como "model_id")
        assert "model_id" not in resp.data[0]  # StudentInstanceSerializer no tiene model_id
        assert "status" in resp.data[0]
        assert "total_score" in resp.data[0]


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestTeacherAccessControl(TestCase):
    """Tests that teachers without full permissions only see assigned instances."""

    def setUp(self):
        self.ctx = _full_setup()
        self.teacher = get_user_model().objects.create_user(
            email=f"teacher-{uuid.uuid4().hex[:8]}@x.com",
            password="TeachPass123!",  # noqa: S106
            first_name="Teach",
            last_name="User",
            organization=self.ctx["org"],
        )
        # Teacher membership without can_view_all_instances
        SubjectMembership.objects.create(
            organization=self.ctx["org"],
            user=self.teacher,
            subject=self.ctx["subject"],
            role=MembershipRole.TEACHER,
            is_active=True,
        )
        self.teacher_client = _auth(self.teacher, pw="TeachPass123!")

    def test_teacher_sees_only_assigned_instances(self):
        # Create instances, only one assigned via rule
        inst_assigned = _create_instance(self.ctx, status=InstanceStatus.PENDING_GRADING)
        other_model = ExamModel.objects.create(label="B", exam=self.ctx["exam"])
        _create_instance(self.ctx, status=InstanceStatus.PENDING_GRADING, model=other_model)
        membership = SubjectMembership.objects.get(user=self.teacher, subject=self.ctx["subject"])
        membership.permission_overrides = {"can_view_all_instances": False}
        membership.save()

        # Create a rule that assigns corrector to inst_assigned's model/problem
        AssignmentRule.objects.create(
            exam=self.ctx["exam"],
            models_filter=[str(self.ctx["model"].pk)],
            problems=[str(self.ctx["problem"].pk)],
            correctors=[str(self.teacher.pk)],
        )

        resp = self.teacher_client.get(f"/api/v1/exams/{self.ctx['exam'].pk}/instances/")
        assert resp.status_code == status.HTTP_200_OK
        # Should only see the instance that matches the rule
        assert len(resp.data["results"]) == 1
        assert resp.data["results"][0]["id"] == str(inst_assigned.pk)

    def test_teacher_with_can_view_all_instances_sees_all(self):
        # Grant permission
        membership = SubjectMembership.objects.get(user=self.teacher, subject=self.ctx["subject"])
        membership.permission_overrides = {"can_view_all_instances": True}
        membership.save()

        _create_instance(self.ctx)
        _create_instance(self.ctx)
        resp = self.teacher_client.get(f"/api/v1/exams/{self.ctx['exam'].pk}/instances/")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data["results"]) == 2

    def test_teacher_list_only_assigned(self):
        inst_assigned = _create_instance(self.ctx, status=InstanceStatus.PENDING_GRADING)
        other_model = ExamModel.objects.create(label="B", exam=self.ctx["exam"])
        _create_instance(self.ctx, status=InstanceStatus.PENDING_GRADING, model=other_model)

        membership = SubjectMembership.objects.get(user=self.teacher, subject=self.ctx["subject"])
        membership.permission_overrides = {"can_view_all_instances": False}
        membership.save()

        AssignmentRule.objects.create(
            exam=self.ctx["exam"],
            models_filter=[str(self.ctx["model"].pk)],
            problems=[str(self.ctx["problem"].pk)],
            correctors=[str(self.teacher.pk)],
        )

        resp = self.teacher_client.get(f"/api/v1/exams/{self.ctx['exam'].pk}/instances/")
        assert resp.status_code == status.HTTP_200_OK
        results = resp.data["results"]
        assert len(results) == 1
        assert results[0]["id"] == str(inst_assigned.pk)
        assert "model_id" in results[0]


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestDownloadWithEncryption(TestCase):
    """Tests PDF download with watermark and optional encryption."""

    def setUp(self):
        self.ctx = _full_setup()
        self.student = get_user_model().objects.create_user(
            email=f"stu-{uuid.uuid4().hex[:8]}@x.com",
            password="StudPass123!",  # noqa: S106
            first_name="Student",
            last_name="One",
            organization=self.ctx["org"],
        )
        group = SubjectGroup.objects.create(subject=self.ctx["subject"], label="G1")
        SubjectMembership.objects.create(
            organization=self.ctx["org"],
            user=self.student,
            subject=self.ctx["subject"],
            role=MembershipRole.STUDENT,
            group=group,
            is_active=True,
        )
        # Create a published instance with a page (needs MinIO mock)
        self.inst = _create_instance(
            self.ctx, student=self.student, status=InstanceStatus.PUBLISHED
        )
        ExamPage.objects.create(
            instance=self.inst,
            page_number=1,
            storage_ref="test/page1.png",
            status=PageStatus.RECOGNIZED,
        )

    @patch("apps.exams.services.storage.download_from_minio")
    def test_download_as_student_encrypted(self, mock_download):
        """Student without can_download_pages gets encrypted PDF."""
        # Prepare mock image bytes (a tiny valid PNG)
        from PIL import Image

        img = Image.new("RGB", (10, 10), color="red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        mock_download.return_value = buf.getvalue()

        # Ensure can_download_pages is False (default)
        self.ctx["exam"].student_permissions["can_download_pages"] = False
        self.ctx["exam"].save()

        # Need review window to allow page access
        from datetime import UTC, datetime, timedelta

        ExamReview.objects.create(
            exam=self.ctx["exam"],
            start_date=datetime.now(UTC) - timedelta(hours=1),
            end_date=datetime.now(UTC) + timedelta(hours=1),
            status="OPEN",
        )
        self.inst.status = InstanceStatus.IN_REVIEW
        self.inst.save()

        client = _auth(self.student, pw="StudPass123!")
        resp = client.get(f"/api/v1/instances/{self.inst.pk}/download/")
        # Should succeed but return encrypted content
        assert resp.status_code == status.HTTP_200_OK
        assert resp["Content-Type"] == "application/octet-stream"
        assert resp["Content-Disposition"].startswith("inline")
        # The response body should not start with '%PDF'
        assert not resp.content.startswith(b"%PDF")
        # Anti-cache headers should be present
        assert "no-store" in resp["Cache-Control"]

    @patch("apps.exams.services.storage.download_from_minio")
    def test_download_as_student_with_download_permission(self, mock_download):
        """Student with can_download_pages=True gets plain PDF."""
        from PIL import Image

        img = Image.new("RGB", (10, 10), color="red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        mock_download.return_value = buf.getvalue()

        self.ctx["exam"].student_permissions["can_download_pages"] = True
        self.ctx["exam"].save()

        from datetime import UTC, datetime, timedelta

        ExamReview.objects.create(
            exam=self.ctx["exam"],
            start_date=datetime.now(UTC) - timedelta(hours=1),
            end_date=datetime.now(UTC) + timedelta(hours=1),
            status="OPEN",
        )
        self.inst.status = InstanceStatus.IN_REVIEW
        self.inst.save()

        client = _auth(self.student, pw="StudPass123!")
        resp = client.get(f"/api/v1/instances/{self.inst.pk}/download/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp["Content-Type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF")

    @patch("apps.exams.services.storage.download_from_minio")
    def test_download_as_manager_unencrypted(self, mock_download):
        """Managers always get unencrypted PDF."""
        from PIL import Image

        img = Image.new("RGB", (10, 10), color="red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        mock_download.return_value = buf.getvalue()

        client = _auth(self.ctx["manager"])
        resp = client.get(f"/api/v1/instances/{self.inst.pk}/download/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp["Content-Type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF")

    def test_download_without_review_access_denied(self):
        """Student cannot download if review window is not active and
        no can_view_pages_after_review."""
        self.ctx["exam"].student_permissions["can_view_pages"] = True
        self.ctx["exam"].save()
        self.inst.status = InstanceStatus.PUBLISHED
        self.inst.save()

        client = _auth(self.student, pw="StudPass123!")
        resp = client.get(f"/api/v1/instances/{self.inst.pk}/download/")
        assert resp.status_code == status.HTTP_403_FORBIDDEN

    @patch("apps.exams.services.storage.download_from_minio")
    def test_download_after_review_with_can_view_after(self, mock_download):
        """Student can download after review if can_view_pages_after_review=True."""
        from PIL import Image

        img = Image.new("RGB", (10, 10), color="red")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        mock_download.return_value = buf.getvalue()

        self.ctx["exam"].student_permissions.update(
            {
                "can_view_pages": True,
                "can_view_pages_after_review": True,
            }
        )
        self.ctx["exam"].save()

        from datetime import UTC, datetime, timedelta

        ExamReview.objects.create(
            exam=self.ctx["exam"],
            start_date=datetime.now(UTC) - timedelta(days=2),
            end_date=datetime.now(UTC) - timedelta(days=1),
            status="CLOSED",
        )
        self.inst.status = InstanceStatus.FINALIZED
        self.inst.save()

        client = _auth(self.student, pw="StudPass123!")
        resp = client.get(f"/api/v1/instances/{self.inst.pk}/download/")
        assert resp.status_code == status.HTTP_200_OK
