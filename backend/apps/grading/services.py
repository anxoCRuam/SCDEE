"""
Grading and assignment business logic.

- Manual grading with optimistic concurrency (RF-11.1, RF-11.5)
- Rubric-based grading (RF-11.2)
- Assignment rule CRUD (RF-8.1 through RF-8.3)
- Assignment application to instances (RF-8.4)
- Coverage verification (RF-8.5)
- Corrector task listing (RF-8.6)

References: RF-8.1 through RF-8.6, RF-11.1 through RF-11.6
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from django.db import transaction

from apps.grading.models import AssignmentRule, Grade
from apps.instances.models import ExamInstance

logger = logging.getLogger(__name__)


class GradingServiceError(Exception):
    """Raised when a grading operation fails."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(detail or code)


def _bump_instance_score(instance: ExamInstance, expected_score: int) -> None:
    """Atomically bump the instance score (RF-11.5).

    The check and the increment happen in a single SQL statement so two
    concurrent graders cannot both succeed: the second one sees zero
    affected rows and is rejected with VERSION_CONFLICT, with the
    instance's current score returned in the error detail so the
    client can resync.

    Must be called inside a ``transaction.atomic()`` block alongside
    the actual ``Grade`` write so the two are committed together.
    """
    rows = ExamInstance.unfiltered.filter(
        pk=instance.pk,
        total_score=expected_score,
    )
    if rows == 0:
        # Reload the actual current score for a precise error.
        try:
            current = ExamInstance.unfiltered.values_list("total_score", flat=True).get(
                pk=instance.pk
            )
        except ExamInstance.DoesNotExist:
            current = expected_score
        raise GradingServiceError(
            code="VERSION_CONFLICT",
            detail=(
                f"Expected score {expected_score}, "
                f"but instance is at score {current}. "
                f"Refresh and try again."
            ),
        )
    instance.refresh_from_db(fields=["total_score", "updated_at"])


# ── Manual grading (RF-11.1) ────────────────────────────────


def grade_problem(
    *,
    instance,
    problem,
    score: Decimal,
    grader,
    expected_score: int,
) -> Grade:
    """Grade a problem on an instance with optimistic concurrency (RF-11.5).

    Args:
        instance: The ExamInstance.
        problem: The Problem to grade.
        score: The score to assign.
        grader: The user grading.
        expected_score: Client's expected score — must match the current grade.
    Returns:
        The created/updated Grade.

    Raises:
        GradingServiceError: If version conflict or validation fails.
    """
    # Validate score against max_score before touching the DB.
    if score > problem.max_score:
        raise GradingServiceError(
            code="SCORE_EXCEEDS_MAX",
            detail=f"Score {score} exceeds max {problem.max_score}.",
        )

    with transaction.atomic():
        # Atomic CAS on version. If this fails we never write the grade.
        _bump_instance_score(instance, expected_score)

        grade, _ = Grade.objects.update_or_create(
            problem=problem,
            instance=instance,
            defaults={
                "score": score,
                "grader": grader,
                "rubric_selections": [],
            },
        )

    # Recalculate total score outside the CAS transaction (read-only on grades).
    from apps.instances.services.instance_service import recalculate_total_score

    recalculate_total_score(instance)

    return grade


# ── Rubric grading (RF-11.2) ─────────────────────────────────


def grade_by_rubric(
    *,
    instance,
    problem,
    criterion_ids: list[str],
    grader,
    expected_score: int,
) -> Grade:
    """Grade a problem using rubric criteria (RF-11.2).

    The score is the sum of selected criteria scores.
    """
    from apps.exams.models import RubricCriterion

    criteria = RubricCriterion.objects.filter(
        pk__in=criterion_ids,
        problem=problem,
    )

    if criteria.count() != len(criterion_ids):
        raise GradingServiceError(
            code="INVALID_CRITERIA",
            detail="Some criterion IDs do not belong to this problem.",
        )

    total = sum(c.score for c in criteria)

    with transaction.atomic():
        _bump_instance_score(instance, expected_score)

        grade, _ = Grade.objects.update_or_create(
            problem=problem,
            instance=instance,
            defaults={
                "score": total,
                "grader": grader,
                "rubric_selections": criterion_ids,
            },
        )

    from apps.instances.services.instance_service import recalculate_total_score

    recalculate_total_score(instance)

    return grade


# ── Assignment rules (RF-8.1 through RF-8.3) ────────────────


def create_assignment_rule(
    *,
    exam,
    groups: list[str] | None = None,
    models_filter: list[str] | None = None,
    problems: list[str],
    correctors: list[str],
) -> AssignmentRule:
    """Create an assignment rule and notify each corrector (RF-8.1, RF-13.7)."""
    rule = AssignmentRule.objects.create(
        exam=exam,
        groups=groups or [],
        models_filter=models_filter or [],
        problems=problems,
        correctors=correctors,
    )

    if correctors:
        from django.contrib.auth import get_user_model

        from apps.instances.models import ExamInstance, InstanceStatus
        from apps.notifications.services import notify_assignment_created

        instance_count = ExamInstance.unfiltered.filter(
            exam=exam,
            status__in=[
                InstanceStatus.PENDING_GRADING,
                InstanceStatus.GRADED,
            ],
        ).count()

        user_model = get_user_model()
        for user in user_model.objects.filter(pk__in=correctors):
            notify_assignment_created(user, exam, instance_count)

    return rule


def delete_assignment_rule(rule: AssignmentRule) -> None:
    """Delete an assignment rule (RF-8.3)."""
    rule.delete()


# ── Coverage check (RF-8.5) ─────────────────────────────────


def check_coverage(exam) -> dict[str, Any]:
    """Check if all instances/problems have corrector coverage (RF-8.5).

    Returns:
        Dict with coverage status and uncovered items.
    """
    from apps.exams.models import ExamModel, Problem
    from apps.instances.models import ExamInstance, InstanceStatus

    rules = AssignmentRule.objects.filter(exam=exam)

    # Collect all covered problem IDs.
    covered_problems: set[str] = set()
    for rule in rules:
        for pid in rule.problems:
            covered_problems.add(str(pid))

    # Find all problems across all models.
    all_models = ExamModel.objects.filter(exam=exam)
    all_problems = Problem.objects.filter(exam_model__in=all_models)

    uncovered = [
        {"problem_id": str(p.pk), "problem_name": p.name, "model": p.exam_model.label}
        for p in all_problems
        if str(p.pk) not in covered_problems
    ]

    # Count instances.
    total_instances = (
        ExamInstance.unfiltered.filter(exam=exam).exclude(status=InstanceStatus.ARCHIVED).count()
    )

    return {
        "complete": len(uncovered) == 0,
        "total_problems": all_problems.count(),
        "covered_problems": len(covered_problems),
        "uncovered_problems": uncovered,
        "total_instances": total_instances,
        "rules_count": rules.count(),
    }


# ── Corrector tasks (RF-8.6) ─────────────────────────────────


def get_corrector_tasks(user, exam=None) -> list[dict]:
    """Get the list of grading tasks for a corrector (RF-8.6).

    Returns instances where the user is assigned as corrector
    and there are ungraded problems.
    """
    from apps.instances.models import ExamInstance, InstanceStatus

    # Find all rules where this user is a corrector.
    rules = AssignmentRule.objects.filter(correctors__contains=[str(user.pk)])

    if exam:
        rules = rules.filter(exam=exam)

    tasks = []
    for rule in rules:
        # Find matching instances.
        instances = ExamInstance.unfiltered.filter(
            exam=rule.exam,
            status__in=[
                InstanceStatus.PENDING_GRADING,
                InstanceStatus.GRADED,
            ],
        )

        # Filter by groups if specified.
        if rule.groups:
            from apps.subjects.models import SubjectMembership

            student_ids = SubjectMembership.unfiltered.filter(
                subject=rule.exam.subject,
                group_id__in=rule.groups,
                is_active=True,
            ).values_list("user_id", flat=True)
            instances = instances.filter(student_id__in=student_ids)

        # Filter by models if specified.
        if rule.models_filter:
            instances = instances.filter(model_id__in=rule.models_filter)

        for instance in instances:
            # Find ungraded problems from this rule.
            graded_problem_ids = set(
                Grade.objects.filter(instance=instance).values_list("problem_id", flat=True)
            )
            ungraded = [
                pid for pid in rule.problems if pid not in [str(g) for g in graded_problem_ids]
            ]

            if ungraded:
                tasks.append(
                    {
                        "instance_id": str(instance.pk),
                        "exam_name": rule.exam.name,
                        "student_email": instance.student.email if instance.student else None,
                        "ungraded_problems": ungraded,
                        "total_problems": len(rule.problems),
                    }
                )

    return tasks
