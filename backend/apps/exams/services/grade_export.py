"""
Grade export service.

- export_grades_csv(): Institutional CSV format (RF-14.1)
- export_grades_json(): Full grade data as JSON (RF-14.2)

References: RF-14.1, RF-14.2
"""

from __future__ import annotations

import csv
import io
import logging

from apps.instances.models import ExamInstance, InstanceStatus
from apps.subjects.models import SubjectMembership

logger = logging.getLogger(__name__)

# Default CSV columns if not configured per-org.
DEFAULT_GRADE_COLUMNS = ["nia", "last_name", "first_name", "group", "total_score"]


def export_grades_csv(exam, columns: list[str] | None = None) -> str:
    """Export grades to institutional CSV (RF-14.1).

    Only includes instances in PUBLISHED or later states.
    """
    if not columns:
        columns = list(DEFAULT_GRADE_COLUMNS)

    instances = (
        ExamInstance.unfiltered.filter(exam=exam)
        .filter(
            status__in=[
                InstanceStatus.PUBLISHED,
                InstanceStatus.IN_REVIEW,
                InstanceStatus.PENDING_REVIEW,
                InstanceStatus.FINALIZED,
                InstanceStatus.ARCHIVED,
            ]
        )
        .select_related("student", "model")
        .order_by("student__last_name", "student__first_name")
    )

    rows = []
    for instance in instances:
        row = _build_grade_row(instance, exam)
        rows.append(row)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue()


def export_grades_json(exam) -> list[dict]:
    """Export full grade data as JSON (RF-14.2).

    Includes per-problem grades for frontend statistics.
    """
    instances = (
        ExamInstance.unfiltered.filter(exam=exam)
        .filter(
            status__in=[
                InstanceStatus.PUBLISHED,
                InstanceStatus.IN_REVIEW,
                InstanceStatus.PENDING_REVIEW,
                InstanceStatus.FINALIZED,
                InstanceStatus.ARCHIVED,
            ]
        )
        .select_related("student", "model")
        .prefetch_related("grades__problem")
        .order_by("student__last_name", "student__first_name")
    )

    data = []
    for instance in instances:
        row = _build_grade_row(instance, exam)

        # Add per-problem grades.
        problem_grades = []
        for grade in instance.grades.all():
            problem_grades.append(
                {
                    "problem_id": str(grade.problem_id),
                    "problem_name": grade.problem.name,
                    "score": str(grade.score),
                    "grader_id": str(grade.grader_id),
                }
            )

        row["problem_grades"] = problem_grades
        row["model_label"] = instance.model.label if instance.model else ""
        row["status"] = instance.status
        data.append(row)

    return data


def _build_grade_row(instance, exam) -> dict:
    """Build a single row dict for grade export."""
    student = instance.student
    group_label = ""

    if student:
        # Find student's group in this subject.
        membership = (
            SubjectMembership.unfiltered.filter(user=student, subject=exam.subject, is_active=True)
            .select_related("group")
            .first()
        )
        if membership and membership.group:
            group_label = membership.group.label

    return {
        "nia": student.nia if student else "",
        "first_name": student.first_name if student else "",
        "last_name": student.last_name if student else "",
        "email": student.email if student else "",
        "group": group_label,
        "total_score": str(instance.total_score) if instance.total_score is not None else "",
        "model_label": instance.model.label if instance.model else "",
        "instance_id": str(instance.pk),
    }
