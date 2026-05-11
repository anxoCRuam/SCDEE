"""
Integration tests for exam reviews (RF-12).

Covers: review creation with permissions, request submission,
listing with role-based filtering, resolution with authorization,
auto-finalization, and automated open/close tasks.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.courses.models.courses import AcademicCourse
from apps.exams.models.exams import Exam, ExamModel, Problem
from apps.grading.models.grading import AssignmentRule
from apps.instances.models.instances import ExamInstance, InstanceStatus
from apps.organizations.models.organization import Organization
from apps.reviews.models.reviews import ExamReview, ReviewRequest, ReviewStatus
from apps.subjects.models.subjects import MembershipRole, Subject, SubjectMembership

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
    teacher = user_model.objects.create_user(
        email=f"t-{uuid.uuid4().hex[:8]}@x.com",
        password="TeachPass123!",  # noqa: S106
        first_name="Teach",
        last_name="U",
        organization=org,
    )
    student = user_model.objects.create_user(
        email=f"s-{uuid.uuid4().hex[:8]}@x.com",
        password="StPass123!",  # noqa: S106
        first_name="Stu",
        last_name="U",
        organization=org,
    )
    student2 = user_model.objects.create_user(
        email=f"s2-{uuid.uuid4().hex[:8]}@x.com",
        password="StPass123!",  # noqa: S106
        first_name="Stu2",
        last_name="U",
        organization=org,
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
    # Coordinator membership
    SubjectMembership.objects.create(
        organization=org,
        user=coord,
        subject=subject,
        role=MembershipRole.COORDINATOR,
        is_active=True,
    )
    # Teacher membership (needed for assignment rules)
    SubjectMembership.objects.create(
        organization=org,
        user=teacher,
        subject=subject,
        role=MembershipRole.TEACHER,
        is_active=True,
    )
    # Student membership with group
    from apps.subjects.models.subjects import SubjectGroup

    group = SubjectGroup.objects.create(subject=subject, label="G1")
    SubjectMembership.objects.create(
        organization=org,
        user=student,
        subject=subject,
        role=MembershipRole.STUDENT,
        group=group,
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
        student=student,
        status=InstanceStatus.PUBLISHED,
        expected_pages=1,
    )

    # Assignment rule: teacher corrects problem P1
    AssignmentRule.objects.create(
        exam=exam,
        problems=[str(problem.pk)],
        correctors=[str(teacher.pk)],
    )

    return {
        "org": org,
        "manager": manager,
        "teacher": teacher,
        "student": student,
        "student2": student2,
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


# ── Review creation (RF-12.1) ───────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestReviewCreation(TestCase):
    def test_create_review(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])
        now = datetime.now(tz=UTC)

        resp = client.post(
            f"/api/v1/exams/{ctx['exam'].pk}/review/",
            {
                "start_date": (now + timedelta(hours=1)).isoformat(),
                "end_date": (now + timedelta(days=3)).isoformat(),
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert resp.data["status"] == "SCHEDULED"

    def test_duplicate_review_rejected(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])
        now = datetime.now(tz=UTC)

        client.post(
            f"/api/v1/exams/{ctx['exam'].pk}/review/",
            {
                "start_date": (now + timedelta(hours=1)).isoformat(),
                "end_date": (now + timedelta(days=3)).isoformat(),
            },
            format="json",
        )
        resp = client.post(
            f"/api/v1/exams/{ctx['exam'].pk}/review/",
            {
                "start_date": (now + timedelta(hours=2)).isoformat(),
                "end_date": (now + timedelta(days=4)).isoformat(),
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_409_CONFLICT

    def test_get_review_status(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])
        now = datetime.now(tz=UTC)

        client.post(
            f"/api/v1/exams/{ctx['exam'].pk}/review/",
            {
                "start_date": (now + timedelta(hours=1)).isoformat(),
                "end_date": (now + timedelta(days=3)).isoformat(),
            },
            format="json",
        )

        resp = client.get(f"/api/v1/exams/{ctx['exam'].pk}/review/")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["status"] == "SCHEDULED"

    def test_create_review_requires_permission(self):
        """User without can_manage_reviews cannot create a review."""
        ctx = _full_setup()
        client = _auth(ctx["teacher"], pw="TeachPass123!")
        now = datetime.now(tz=UTC)

        resp = client.post(
            f"/api/v1/exams/{ctx['exam'].pk}/review/",
            {
                "start_date": (now + timedelta(hours=1)).isoformat(),
                "end_date": (now + timedelta(days=3)).isoformat(),
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert resp.data["error_code"] == "PERMISSION_DENIED"


# ── Review request submission (RF-12.4) ──────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestReviewRequestSubmission(TestCase):
    def test_student_submits_request(self):
        ctx = _full_setup()

        # Create and open review.
        ExamReview.objects.create(
            exam=ctx["exam"],
            start_date=datetime.now(tz=UTC) - timedelta(hours=1),
            end_date=datetime.now(tz=UTC) + timedelta(days=2),
            status=ReviewStatus.OPEN,
        )
        ctx["instance"].status = InstanceStatus.IN_REVIEW
        ctx["instance"].save()

        client = _auth(ctx["student"], pw="StPass123!")
        resp = client.post(
            f"/api/v1/instances/{ctx['instance'].pk}/review-requests/",
            {
                "problems": [
                    {"problem_id": str(ctx["problem"].pk), "message": "Please review this."}
                ]
            },
            format="json",
        )
        assert resp.status_code == status.HTTP_201_CREATED
        assert len(resp.data) == 1

    def test_request_rejected_when_review_not_open(self):
        ctx = _full_setup()

        ExamReview.objects.create(
            exam=ctx["exam"],
            start_date=datetime.now(tz=UTC) + timedelta(hours=1),
            end_date=datetime.now(tz=UTC) + timedelta(days=2),
            status=ReviewStatus.SCHEDULED,
        )

        client = _auth(ctx["student"], pw="StPass123!")
        resp = client.post(
            f"/api/v1/instances/{ctx['instance'].pk}/review-requests/",
            {"problems": [{"problem_id": str(ctx["problem"].pk)}]},
            format="json",
        )
        assert resp.status_code == status.HTTP_409_CONFLICT

    def test_list_requests_as_manager(self):
        """Manager sees all requests for the instance."""
        ctx = _full_setup()

        review = ExamReview.objects.create(
            exam=ctx["exam"],
            start_date=datetime.now(tz=UTC) - timedelta(hours=1),
            end_date=datetime.now(tz=UTC) + timedelta(days=2),
            status=ReviewStatus.OPEN,
        )
        ctx["instance"].status = InstanceStatus.IN_REVIEW
        ctx["instance"].save()

        ReviewRequest.objects.create(
            review=review,
            instance=ctx["instance"],
            problem=ctx["problem"],
            student_message="Please check.",
        )

        client = _auth(ctx["manager"])
        resp = client.get(f"/api/v1/instances/{ctx['instance'].pk}/review-requests/")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 1

    def test_list_requests_as_student_owner(self):
        """Student owner sees requests of his own instance."""
        ctx = _full_setup()

        review = ExamReview.objects.create(
            exam=ctx["exam"],
            start_date=datetime.now(tz=UTC) - timedelta(hours=1),
            end_date=datetime.now(tz=UTC) + timedelta(days=2),
            status=ReviewStatus.OPEN,
        )
        ctx["instance"].status = InstanceStatus.IN_REVIEW
        ctx["instance"].save()

        ReviewRequest.objects.create(
            review=review,
            instance=ctx["instance"],
            problem=ctx["problem"],
            student_message="Check.",
        )

        client = _auth(ctx["student"], pw="StPass123!")
        resp = client.get(f"/api/v1/instances/{ctx['instance'].pk}/review-requests/")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 1

    def test_list_requests_as_assigned_teacher(self):
        """Teacher with assignment rule sees only requests for assigned problems."""
        ctx = _full_setup()

        review = ExamReview.objects.create(
            exam=ctx["exam"],
            start_date=datetime.now(tz=UTC) - timedelta(hours=1),
            end_date=datetime.now(tz=UTC) + timedelta(days=2),
            status=ReviewStatus.OPEN,
        )
        ctx["instance"].status = InstanceStatus.IN_REVIEW
        ctx["instance"].save()

        # Create a request for the assigned problem P1
        ReviewRequest.objects.create(
            review=review,
            instance=ctx["instance"],
            problem=ctx["problem"],
            student_message="Check.",
        )

        client = _auth(ctx["teacher"], pw="TeachPass123!")
        resp = client.get(f"/api/v1/instances/{ctx['instance'].pk}/review-requests/")
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 1  # teacher assigned to P1 via rule

    def test_list_requests_anonymous_or_unauthorized(self):
        """User with no relation to the instance gets an empty list."""
        ctx = _full_setup()

        review = ExamReview.objects.create(
            exam=ctx["exam"],
            start_date=datetime.now(tz=UTC) - timedelta(hours=1),
            end_date=datetime.now(tz=UTC) + timedelta(days=2),
            status=ReviewStatus.OPEN,
        )
        ctx["instance"].status = InstanceStatus.IN_REVIEW
        ctx["instance"].save()

        ReviewRequest.objects.create(
            review=review,
            instance=ctx["instance"],
            problem=ctx["problem"],
        )

        # Another student not owner of this instance
        other_student = ctx["student2"]  # not a student, but a different user
        client = _auth(other_student, pw="StPass123!")
        resp = client.get(f"/api/v1/instances/{ctx['instance'].pk}/review-requests/")
        # Current logic returns empty list for unauthorized non-owner; adjust to 403
        assert resp.status_code == status.HTTP_200_OK
        assert len(resp.data) == 0


# ── Request resolution (RF-12.7) ─────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestRequestResolution(TestCase):
    def test_resolve_request_by_manager(self):
        ctx = _full_setup()

        review = ExamReview.objects.create(
            exam=ctx["exam"],
            start_date=datetime.now(tz=UTC) - timedelta(hours=2),
            end_date=datetime.now(tz=UTC) - timedelta(hours=1),
            status=ReviewStatus.CLOSED,
        )
        ctx["instance"].status = InstanceStatus.PENDING_REVIEW
        ctx["instance"].save()

        rr = ReviewRequest.objects.create(
            review=review,
            instance=ctx["instance"],
            problem=ctx["problem"],
            student_message="Check.",
        )

        client = _auth(ctx["manager"])
        resp = client.patch(
            f"/api/v1/review-requests/{rr.pk}/resolve/",
            {"resolver_message": "Reviewed and confirmed."},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["resolved"] is True

    def test_resolve_request_by_assigned_teacher(self):
        """Teacher with assignment rule can resolve."""
        ctx = _full_setup()

        review = ExamReview.objects.create(
            exam=ctx["exam"],
            start_date=datetime.now(tz=UTC) - timedelta(hours=2),
            end_date=datetime.now(tz=UTC) - timedelta(hours=1),
            status=ReviewStatus.CLOSED,
        )
        ctx["instance"].status = InstanceStatus.PENDING_REVIEW
        ctx["instance"].save()

        rr = ReviewRequest.objects.create(
            review=review,
            instance=ctx["instance"],
            problem=ctx["problem"],
        )

        client = _auth(ctx["teacher"], pw="TeachPass123!")
        resp = client.patch(
            f"/api/v1/review-requests/{rr.pk}/resolve/",
            {"resolver_message": "Done."},
            format="json",
        )
        assert resp.status_code == status.HTTP_200_OK
        assert resp.data["resolved"] is True

    def test_resolve_request_by_unauthorized_rejected(self):
        """User without permission cannot resolve."""
        ctx = _full_setup()

        review = ExamReview.objects.create(
            exam=ctx["exam"],
            start_date=datetime.now(tz=UTC) - timedelta(hours=2),
            end_date=datetime.now(tz=UTC) - timedelta(hours=1),
            status=ReviewStatus.CLOSED,
        )
        ctx["instance"].status = InstanceStatus.PENDING_REVIEW
        ctx["instance"].save()

        rr = ReviewRequest.objects.create(
            review=review,
            instance=ctx["instance"],
            problem=ctx["problem"],
        )

        client = _auth(ctx["student"], pw="StPass123!")
        resp = client.patch(
            f"/api/v1/review-requests/{rr.pk}/resolve/",
            {"resolver_message": "Trying..."},
            format="json",
        )
        assert resp.status_code == status.HTTP_403_FORBIDDEN
        assert resp.data["error_code"] == "PERMISSION_DENIED"

    def test_resolve_last_request_finalizes_instance(self):
        ctx = _full_setup()

        review = ExamReview.objects.create(
            exam=ctx["exam"],
            start_date=datetime.now(tz=UTC) - timedelta(hours=2),
            end_date=datetime.now(tz=UTC) - timedelta(hours=1),
            status=ReviewStatus.CLOSED,
        )
        ctx["instance"].status = InstanceStatus.PENDING_REVIEW
        ctx["instance"].save()

        rr = ReviewRequest.objects.create(
            review=review,
            instance=ctx["instance"],
            problem=ctx["problem"],
        )

        from apps.reviews.services.reviews import resolve_request

        resolve_request(request=rr, resolver=ctx["manager"])

        ctx["instance"].refresh_from_db()
        assert ctx["instance"].status == InstanceStatus.FINALIZED


# ── Automated open/close (RF-12.2, RF-12.3) ─────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestAutomatedOpenClose(TestCase):
    def test_open_review_task(self):
        ctx = _full_setup()

        ExamReview.objects.create(
            exam=ctx["exam"],
            start_date=datetime.now(tz=UTC) - timedelta(minutes=5),
            end_date=datetime.now(tz=UTC) + timedelta(days=2),
            status=ReviewStatus.SCHEDULED,
        )

        from apps.reviews.tasks import process_review_openings

        result = process_review_openings()
        assert result["opened"] == 1

        ctx["instance"].refresh_from_db()
        assert ctx["instance"].status == InstanceStatus.IN_REVIEW

    def test_close_review_task_no_requests(self):
        ctx = _full_setup()

        ExamReview.objects.create(
            exam=ctx["exam"],
            start_date=datetime.now(tz=UTC) - timedelta(days=2),
            end_date=datetime.now(tz=UTC) - timedelta(minutes=5),
            status=ReviewStatus.OPEN,
        )
        ctx["instance"].status = InstanceStatus.IN_REVIEW
        ctx["instance"].save()

        from apps.reviews.tasks import process_review_closings

        result = process_review_closings()
        assert result["closed"] == 1

        ctx["instance"].refresh_from_db()
        assert ctx["instance"].status == InstanceStatus.FINALIZED

    def test_close_review_with_requests_goes_pending(self):
        ctx = _full_setup()

        review = ExamReview.objects.create(
            exam=ctx["exam"],
            start_date=datetime.now(tz=UTC) - timedelta(days=2),
            end_date=datetime.now(tz=UTC) - timedelta(minutes=5),
            status=ReviewStatus.OPEN,
        )
        ctx["instance"].status = InstanceStatus.IN_REVIEW
        ctx["instance"].save()

        ReviewRequest.objects.create(
            review=review,
            instance=ctx["instance"],
            problem=ctx["problem"],
        )

        from apps.reviews.tasks import process_review_closings

        process_review_closings()

        ctx["instance"].refresh_from_db()
        assert ctx["instance"].status == InstanceStatus.PENDING_REVIEW
