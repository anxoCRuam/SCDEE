"""
Rubric-based grading: arithmetic correctness tests.

Per RF-11.2, ``grade_by_rubric`` sums the scores of every selected
criterion (positive AND negative — negative criteria express
penalties). Per RF-11.3, the resulting *instance* total cannot drop
below zero — penalties stop biting once the score reaches 0.

This module pins both rules with focused tests, covering edge cases
that the existing ``test_grading_service.py`` does not specifically
isolate:

- A single positive criterion grades the problem to its score.
- Multiple positive criteria sum cleanly.
- Mixed positive + negative criteria yield the algebraic sum (which
  may be negative for the *problem*).
- A problem grade can be negative — it is the *instance total* that
  is clamped at 0, not individual problem grades.
- Picking criteria from another problem is rejected with
  ``INVALID_CRITERIA``.

References: RF-11.2, RF-11.3.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model

from apps.courses.models.courses import AcademicCourse
from apps.exams.models.exams import Exam, ExamModel, Problem, RubricCriterion
from apps.grading.services.grading import GradingServiceError, grade_by_rubric
from apps.instances.models.instances import ExamInstance, InstanceStatus
from apps.organizations.models.organization import Organization
from apps.subjects.models.subjects import MembershipRole, Subject, SubjectMembership

pytestmark = pytest.mark.django_db


# ── Helpers ────────────────────────────────────────────────────────


def _setup_problem_with_rubric(criteria_scores: list[Decimal]) -> dict:
    """Build a complete graph plus a Problem with the given criteria.

    ``criteria_scores`` lists the score for each criterion in order
    (e.g. ``[2.0, 3.0, -1.0]`` creates three criteria worth +2, +3
    and -1 respectively).
    """
    user_model = get_user_model()
    suffix = uuid.uuid4().hex[:8]

    org = Organization.objects.create(name=f"Org-{suffix}", subdomain=f"o{suffix}")
    grader = user_model.objects.create_user(
        email=f"g-{suffix}@x.com",
        password="P@ss123!",  # noqa: S106
        first_name="Grace",
        last_name="Grader",
        organization=org,
        is_staff=True,
    )
    course = AcademicCourse.objects.create(organization=org, label="2026", is_active=True)
    subject = Subject.objects.create(
        organization=org,
        name="S",
        code=f"S{suffix}",
        course=course,
        coordinator=grader,
    )
    SubjectMembership.objects.create(
        organization=org,
        user=grader,
        subject=subject,
        role=MembershipRole.COORDINATOR,
        is_active=True,
    )
    exam = Exam.objects.create(organization=org, name="E", subject=subject)
    model = ExamModel.objects.create(label="A", exam=exam)
    problem = Problem.objects.create(
        name="P1", max_score=Decimal("10.00"), exam_model=model, order=1
    )
    student = user_model.objects.create_user(
        email=f"s-{suffix}@x.com",
        password="P@ss123!",  # noqa: S106
        first_name="Sam",
        last_name="Student",
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
        status=InstanceStatus.PENDING_GRADING,
        expected_pages=1,
    )

    criteria = [
        RubricCriterion.objects.create(
            problem=problem,
            description=f"Criterion {i + 1}",
            score=score,
            order=i + 1,
        )
        for i, score in enumerate(criteria_scores)
    ]

    return {
        "org": org,
        "grader": grader,
        "exam": exam,
        "problem": problem,
        "instance": instance,
        "criteria": criteria,
    }


# ── Tests ──────────────────────────────────────────────────────────


def test_rubric_with_single_positive_criterion():
    """One criterion ticked → the problem grade equals its score."""
    setup = _setup_problem_with_rubric([Decimal("4.50")])

    grade = grade_by_rubric(
        instance=setup["instance"],
        problem=setup["problem"],
        criterion_ids=[str(setup["criteria"][0].pk)],
        grader=setup["grader"],
        old_score=None,
    )

    assert grade.score == Decimal("4.50")


def test_rubric_with_multiple_positive_criteria_sums_cleanly():
    """Sum of selected positive criteria = problem grade."""
    setup = _setup_problem_with_rubric([Decimal("2.00"), Decimal("3.00"), Decimal("1.50")])
    selected = [str(c.pk) for c in setup["criteria"]]  # all three

    grade = grade_by_rubric(
        instance=setup["instance"],
        problem=setup["problem"],
        criterion_ids=selected,
        grader=setup["grader"],
        old_score=None,
    )

    assert grade.score == Decimal("6.50")


def test_rubric_mixes_positive_and_negative_criteria_correctly():
    """Negative criteria express penalties and lower the algebraic sum."""
    setup = _setup_problem_with_rubric([Decimal("5.00"), Decimal("3.00"), Decimal("-1.50")])
    selected = [str(c.pk) for c in setup["criteria"]]

    grade = grade_by_rubric(
        instance=setup["instance"],
        problem=setup["problem"],
        criterion_ids=selected,
        grader=setup["grader"],
        old_score=None,
    )

    # 5.00 + 3.00 − 1.50
    assert grade.score == Decimal("6.50")


def test_rubric_problem_grade_can_go_below_zero():
    """A single problem may end up with a negative grade — it is the
    instance total that gets clamped at 0 (RF-11.3), not the problem
    score itself.
    """
    setup = _setup_problem_with_rubric([Decimal("1.00"), Decimal("-3.00")])
    selected = [str(c.pk) for c in setup["criteria"]]

    grade = grade_by_rubric(
        instance=setup["instance"],
        problem=setup["problem"],
        criterion_ids=selected,
        grader=setup["grader"],
        old_score=None,
    )

    # 1 − 3 = −2; the problem grade reflects that.
    assert grade.score == Decimal("-2.00")

    # The instance total, however, is recomputed and clamped at 0
    # (this is the RF-11.3 invariant: a student never has a
    # *negative* total grade).
    setup["instance"].refresh_from_db()
    assert setup["instance"].total_score == Decimal(
        "0.00"
    ), f"Instance total should clamp at 0; got {setup['instance'].total_score}."


def test_rubric_picking_criterion_from_other_problem_is_rejected():
    """Criterion IDs must belong to *this* problem.

    Without this check, an attacker could pick high-value criteria
    from another problem to inflate a grade. Or, more mundanely, a
    bug in the frontend could send the wrong IDs and silently
    miscompute the score.
    """
    setup = _setup_problem_with_rubric([Decimal("5.00")])

    # Build a second problem with its own criterion in the same exam.
    other_problem = Problem.objects.create(
        name="P2",
        max_score=Decimal("10.00"),
        exam_model=setup["problem"].exam_model,
        order=2,
    )
    foreign_criterion = RubricCriterion.objects.create(
        problem=other_problem,
        description="Belongs to P2",
        score=Decimal("9.99"),
        order=1,
    )

    with pytest.raises(GradingServiceError) as exc_info:
        grade_by_rubric(
            instance=setup["instance"],
            problem=setup["problem"],  # P1
            criterion_ids=[str(foreign_criterion.pk)],  # but criterion is on P2
            grader=setup["grader"],
            old_score=None,
        )

    assert exc_info.value.code == "INVALID_CRITERIA"


def test_rubric_with_empty_criterion_list_yields_zero():
    """Selecting no criteria gives the problem a score of 0.

    This is the deliberate "wrong on every count" case — sums the
    empty set, which is 0 in arithmetic and so should be in the code.
    """
    setup = _setup_problem_with_rubric([Decimal("5.00")])

    grade = grade_by_rubric(
        instance=setup["instance"],
        problem=setup["problem"],
        criterion_ids=[],
        grader=setup["grader"],
        old_score=None,
    )

    assert grade.score == Decimal("0.00")
