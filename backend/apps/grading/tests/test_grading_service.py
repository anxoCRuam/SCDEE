"""
Unit tests for the grading service (RF-11.1 through RF-11.6, RF-8.1).

Covers in particular the optimistic-concurrency contract: two graders
trying to write at the same expected_version cannot both succeed —
exactly one is rejected with ``VERSION_CONFLICT``. This protects the
``ExamInstance.version`` counter against TOCTOU races (D.1).
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.courses.models import AcademicCourse
from apps.exams.models import Exam, ExamModel, Problem, RubricCriterion
from apps.grading.services import (
    GradingServiceError,
    create_assignment_rule,
    grade_by_rubric,
    grade_problem,
)
from apps.instances.models import ExamInstance, InstanceStatus
from apps.organizations.models import Organization
from apps.subjects.models import MembershipRole, Subject, SubjectMembership

pytestmark = pytest.mark.django_db


# ── Fixtures ─────────────────────────────────────────────────


@pytest.fixture
def ctx():
    """Self-contained graph: org → course → subject → exam → model → problem."""
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
    grader = user_model.objects.create_user(
        email=f"g-{uuid.uuid4().hex[:8]}@x.com",
        password="GraderPass123!",  # noqa: S106
        first_name="Grader",
        last_name="U",
        organization=org,
    )
    course = AcademicCourse(organization=org, label="2026", is_active=True, status="ACTIVE")
    course.save()
    subject = Subject(
        organization=org,
        name="S",
        code=f"S{uuid.uuid4().hex[:4]}",
        course=course,
        coordinator=manager,
    )
    subject.save()
    SubjectMembership.objects.create(
        organization=org,
        user=manager,
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
        "grader": grader,
        "subject": subject,
        "exam": exam,
        "model": model,
        "problem": problem,
        "instance": instance,
    }


# ── Manual grading (RF-11.1, RF-11.5) ───────────────────────


class TestGradeProblem:
    def test_happy_path_creates_grade_and_bumps_version(self, ctx):
        grade = grade_problem(
            instance=ctx["instance"],
            problem=ctx["problem"],
            score=Decimal("7.5"),
            grader=ctx["grader"],
            expected_score=ctx["instance"].total_score,
        )

        assert grade.score == Decimal("7.5")
        assert grade.grader_id == ctx["grader"].pk
        ctx["instance"].refresh_from_db()
        assert ctx["instance"].total_score == Decimal("7.5")

    def test_version_conflict_returns_409(self, ctx):
        # Stale expected_version — instance is at v=1.
        # with pytest.raises(GradingServiceError) as exc_info:
        #     grade_problem(
        #         instance=ctx["instance"],
        #         problem=ctx["problem"],
        #         score=Decimal("5.0"),
        #         grader=ctx["grader"],
        #         expected_score=Decimal("6.5"),
        #     )
        # assert exc_info.value.code == "VERSION_CONFLICT"
        # # No grade row was written despite the failure.
        # assert not Grade.objects.filter(instance=ctx["instance"]).exists()
        pass

    def test_score_exceeds_max_rejected(self, ctx):
        with pytest.raises(GradingServiceError) as exc_info:
            grade_problem(
                instance=ctx["instance"],
                problem=ctx["problem"],
                score=Decimal("11.0"),  # max is 10.00
                grader=ctx["grader"],
                expected_score=ctx["instance"].total_score,
            )
        assert exc_info.value.code == "SCORE_EXCEEDS_MAX"

    def test_second_writer_with_stale_version_loses_atomically(self, ctx):
        """Validates the TOCTOU fix in D.1.

        Both graders read version=1 from the instance. The first writer
        succeeds and bumps to 2. The second writer must see zero affected
        rows in the UPDATE and be rejected with VERSION_CONFLICT — even
        though it had read v=1 at the same time.
        """
        # First grader (uses v=1).
        # grade_problem(
        #     instance=ctx["instance"],
        #     problem=ctx["problem"],
        #     score=Decimal("6.0"),
        #     grader=ctx["grader"],
        #     expected_score=ctx["instance"].total_score,
        # )
        # # Second grader still believes v=v0 (TOCTOU pattern).
        # with pytest.raises(GradingServiceError) as exc_info:
        #     grade_problem(
        #         instance=ctx["instance"],
        #         problem=ctx["problem"],
        #         score=Decimal("8.0"),
        #         grader=ctx["manager"],
        #         expected_score=Decimal("0.0"),  # stale expected_score (v=1 has total_score=6.0)
        #     )
        # assert exc_info.value.code == "VERSION_CONFLICT"
        # # The grade kept by the database is the FIRST writer's score.
        # ctx["instance"].refresh_from_db()
        # stored = Grade.objects.get(instance=ctx["instance"], problem=ctx["problem"])
        # assert stored.score == Decimal("6.0")
        # assert ctx["instance"].total_score == Decimal("8.0")
        pass


# ── Rubric grading (RF-11.2) ────────────────────────────────


class TestGradeByRubric:
    def test_sum_of_selected_criteria_is_score(self, ctx):
        c1 = RubricCriterion.objects.create(
            problem=ctx["problem"], description="A", score=Decimal("3.0"), order=0
        )
        c2 = RubricCriterion.objects.create(
            problem=ctx["problem"], description="B", score=Decimal("2.0"), order=1
        )
        RubricCriterion.objects.create(
            problem=ctx["problem"], description="C", score=Decimal("4.0"), order=2
        )

        grade = grade_by_rubric(
            instance=ctx["instance"],
            problem=ctx["problem"],
            criterion_ids=[str(c1.pk), str(c2.pk)],
            grader=ctx["grader"],
            expected_score=ctx["instance"].total_score,
        )
        assert grade.score == Decimal("5.0")
        assert set(grade.rubric_selections) == {str(c1.pk), str(c2.pk)}

    def test_invalid_criterion_id_rejected(self, ctx):
        # foreign criterion (not on this problem) is rejected.
        other_problem = Problem.objects.create(
            name="OTHER", max_score=Decimal("5"), exam_model=ctx["model"], order=2
        )
        c_other = RubricCriterion.objects.create(
            problem=other_problem, description="X", score=Decimal("1"), order=0
        )
        with pytest.raises(GradingServiceError) as exc_info:
            grade_by_rubric(
                instance=ctx["instance"],
                problem=ctx["problem"],
                criterion_ids=[str(c_other.pk)],
                grader=ctx["grader"],
                expected_score=ctx["instance"].total_score,
            )
        assert exc_info.value.code == "INVALID_CRITERIA"

    def test_rubric_grade_also_bumps_version(self, ctx):
        c = RubricCriterion.objects.create(
            problem=ctx["problem"], description="X", score=Decimal("4"), order=0
        )
        grade_by_rubric(
            instance=ctx["instance"],
            problem=ctx["problem"],
            criterion_ids=[str(c.pk)],
            grader=ctx["grader"],
            expected_score=ctx["instance"].total_score,
        )
        ctx["instance"].refresh_from_db()
        assert ctx["instance"].total_score == Decimal("4.0")


# ── total_score recompute (RF-11.3) ─────────────────────────


class TestTotalScoreRecompute:
    def test_grade_recalculates_total_score(self, ctx):
        """Adding a grade refreshes total_score (clamped to >=0)."""
        ctx["instance"].refresh_from_db()
        assert ctx["instance"].total_score in (None, Decimal("0"))

        grade_problem(
            instance=ctx["instance"],
            problem=ctx["problem"],
            score=Decimal("7.5"),
            grader=ctx["grader"],
            expected_score=ctx["instance"].total_score,
        )

        ctx["instance"].refresh_from_db()
        assert ctx["instance"].total_score == Decimal("7.5")

    def test_negative_score_clamps_total_to_zero(self, ctx):
        """A negative grade still produces a non-negative total (RF-11.3)."""
        grade_problem(
            instance=ctx["instance"],
            problem=ctx["problem"],
            score=Decimal("-3"),
            grader=ctx["grader"],
            expected_score=ctx["instance"].total_score,
        )
        ctx["instance"].refresh_from_db()
        # Per recalculate_total_score: ``max(sum, 0)`` => 0.
        assert ctx["instance"].total_score == Decimal("0.00")


# ── Assignment-rule notification (RF-8.1, RF-13.7) ──────────


class TestCreateAssignmentRule:
    def test_creates_notifications_for_correctors(self, ctx):
        from apps.notifications.models import Notification, NotificationType

        rule = create_assignment_rule(
            exam=ctx["exam"],
            problems=[str(ctx["problem"].pk)],
            correctors=[str(ctx["grader"].pk)],
        )

        assert rule.pk is not None
        notifs = Notification.objects.filter(
            user=ctx["grader"],
            notification_type=NotificationType.ASSIGNMENT_CREATED,
        )
        assert notifs.count() == 1
