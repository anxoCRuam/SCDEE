"""
Unit tests for the grading service (RF-11.1 through RF-11.6, RF-8.1).

Covers in particular the optimistic-concurrency contract: two graders
trying to write at the same old_score cannot both succeed —
exactly one is rejected with ``VERSION_CONFLICT``.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.courses.models.courses import AcademicCourse
from apps.exams.models.exams import Exam, ExamModel, Problem, RubricCriterion
from apps.grading.models.grading import AssignmentRule, Grade
from apps.grading.services.grading import (
    GradingServiceError,
    can_user_grade_instance,
    create_assignment_rule,
    grade_by_rubric,
    grade_problem,
)
from apps.instances.models.instances import ExamInstance, InstanceStatus
from apps.organizations.models.organization import Organization
from apps.subjects.models.subjects import MembershipRole, Subject, SubjectMembership

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
    course = AcademicCourse(organization=org, label="2026", is_active=True)
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
    SubjectMembership.objects.create(
        organization=org,
        user=grader,
        subject=subject,
        role=MembershipRole.TEACHER,
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
    def test_happy_path_creates_grade(self, ctx):
        grade = grade_problem(
            instance=ctx["instance"],
            problem=ctx["problem"],
            score=Decimal("7.5"),
            grader=ctx["grader"],
            old_score=None,  # Primera calificación sin old_score
        )
        assert grade.score == Decimal("7.5")
        assert grade.grader_id == ctx["grader"].pk

    def test_update_with_correct_old_score(self, ctx):
        # Create first grade
        grade_problem(
            instance=ctx["instance"],
            problem=ctx["problem"],
            score=Decimal("5.0"),
            grader=ctx["grader"],
            old_score=None,
        )
        # Update with correct old_score
        grade = grade_problem(
            instance=ctx["instance"],
            problem=ctx["problem"],
            score=Decimal("8.0"),
            grader=ctx["grader"],
            old_score=Decimal("5.0"),
        )
        assert grade.score == Decimal("8.0")

    def test_version_conflict_with_wrong_old_score(self, ctx):
        # Create first grade
        grade_problem(
            instance=ctx["instance"],
            problem=ctx["problem"],
            score=Decimal("5.0"),
            grader=ctx["grader"],
            old_score=None,
        )
        # Try to update with wrong old_score → conflict
        with pytest.raises(GradingServiceError) as exc_info:
            grade_problem(
                instance=ctx["instance"],
                problem=ctx["problem"],
                score=Decimal("9.0"),
                grader=ctx["grader"],
                old_score=Decimal("4.0"),  # wrong
            )
        assert exc_info.value.code == "VERSION_CONFLICT"
        # Grade remains unchanged
        stored = Grade.objects.get(instance=ctx["instance"], problem=ctx["problem"])
        assert stored.score == Decimal("5.0")


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
            old_score=None,
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
                old_score=None,
            )
        assert exc_info.value.code == "INVALID_CRITERIA"


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
            old_score=None,
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
            old_score=None,
        )
        ctx["instance"].refresh_from_db()
        # Per recalculate_total_score: ``max(sum, 0)`` => 0.
        assert ctx["instance"].total_score == Decimal("0.00")


# ── Assignment-rule notification (RF-8.1, RF-13.7) ──────────


class TestCreateAssignmentRule:
    def test_creates_notifications_for_correctors(self, ctx):
        from apps.notifications.models.notifications import Notification, NotificationType

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


class TestCanUserGradeInstance:
    def test_manager_can_grade_any_instance(self, ctx):
        ctx["grader"].is_staff = True
        ctx["grader"].save()
        assert can_user_grade_instance(ctx["grader"], ctx["instance"], ctx["problem"]) is True

    def test_assigned_corrector_can_grade(self, ctx):
        AssignmentRule.objects.create(
            exam=ctx["exam"],
            problems=[str(ctx["problem"].pk)],
            correctors=[str(ctx["grader"].pk)],
        )
        assert can_user_grade_instance(ctx["grader"], ctx["instance"], ctx["problem"]) is True

    def test_unassigned_user_cannot_grade(self, ctx):
        assert can_user_grade_instance(ctx["grader"], ctx["instance"], ctx["problem"]) is False

    def test_corrector_cannot_grade_different_problem(self, ctx):
        # Crear un segundo problema
        other_problem = Problem.objects.create(
            name="P2", max_score=Decimal("5"), exam_model=ctx["model"], order=2
        )
        AssignmentRule.objects.create(
            exam=ctx["exam"],
            problems=[str(ctx["problem"].pk)],  # solo problema original
            correctors=[str(ctx["grader"].pk)],
        )
        assert can_user_grade_instance(ctx["grader"], ctx["instance"], other_problem) is False

    def test_rule_with_group_filter(self, ctx):
        from apps.subjects.models.subjects import SubjectGroup, SubjectMembership

        # Crear estudiante y asignarlo a la instancia
        student = get_user_model().objects.create_user(
            email=f"stu-{uuid.uuid4().hex[:8]}@x.com",
            password="Pass123!",  # noqa: S106
            first_name="Student",
            last_name="Test",
            organization=ctx["org"],
        )
        ctx["instance"].student = student
        ctx["instance"].save()

        group = SubjectGroup.objects.create(subject=ctx["subject"], label="G1")
        SubjectMembership.objects.create(
            organization=ctx["org"],
            user=student,
            subject=ctx["subject"],
            role=MembershipRole.STUDENT,
            group=group,
            is_active=True,
        )
        AssignmentRule.objects.create(
            exam=ctx["exam"],
            problems=[str(ctx["problem"].pk)],
            correctors=[str(ctx["grader"].pk)],
            groups=[str(group.pk)],
        )
        assert can_user_grade_instance(ctx["grader"], ctx["instance"], ctx["problem"]) is True
