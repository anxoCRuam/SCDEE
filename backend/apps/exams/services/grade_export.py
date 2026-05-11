"""
Grade export service.

- export_grades(): returns (content, content_type) for CSV or JSON.
  For CSV, the column mapping is a dict {base_field: exported_header}.
  base_field can be any attribute path reachable from an ExamInstance
  (e.g. "student__nia", "student__email", "model__label", "exam__subject__name",
  "total_score").

References: RF-14.1, RF-14.2
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any

from apps.instances.models.instances import ExamInstance, InstanceStatus
from apps.subjects.models.subjects import SubjectMembership

logger = logging.getLogger(__name__)

# Default column mapping if the organization has not configured one.
DEFAULT_GRADE_COLUMNS = {
    "student__nia": "NIA",
    "student__last_name": "Last Name",
    "student__first_name": "First Name",
    "group": "Group",
    "total_score": "Final Grade",
}


def export_grades(
    exam,
    *,
    file_format: str = "csv",
    column_mapping: dict[str, str] | None = None,
) -> tuple[Any, str]:
    """Export grades for an exam.

    Args:
        exam: The Exam instance.
        file_format: "csv" or "json".
        column_mapping: For CSV, a dict mapping attribute paths to
            column headers. Defaults to ``DEFAULT_GRADE_COLUMNS``.

    Returns:
        Tuple of (content, content_type).
        - For CSV: (str, "text/csv")
        - For JSON: (list[dict], "application/json")
    """
    if file_format == "csv":
        if column_mapping is None:
            column_mapping = dict(DEFAULT_GRADE_COLUMNS)
        return _export_csv(exam, column_mapping), "text/csv"
    return _export_json(exam), "application/json"


# ── CSV export ───────────────────────────────────────────────


def _export_csv(exam, column_mapping: dict[str, str]) -> str:
    """Build a CSV string using the configured column mapping.

    The keys of column_mapping are used to extract values from each
    instance. They can contain double-underscore paths to navigate
    relationships.
    """
    instances = _get_instances_queryset(exam)

    output = io.StringIO()
    writer = csv.writer(output)

    # Header row.
    writer.writerow(column_mapping.values())

    # Data rows.
    for instance in instances:
        row = [_resolve_value(instance, field) for field in column_mapping]
        writer.writerow(row)

    return output.getvalue()


# ── JSON export ──────────────────────────────────────────────


def _export_json(exam) -> list[dict]:
    """Build a JSON-serializable list of grade objects."""
    instances = _get_instances_queryset(exam).prefetch_related("grades__problem")

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


# ── Helpers ──────────────────────────────────────────────────


def _get_instances_queryset(exam):
    """Return the base queryset of publishable instances for an exam."""
    return (
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
        .select_related(
            "student",
            "student__organization",  # por si se piden campos de organización
            "model",
            "exam",
            "exam__subject",
            "exam__subject__course",
            "exam__subject__coordinator",
        )
        .order_by("student__last_name", "student__first_name")
    )


def _build_grade_row(instance, exam) -> dict[str, str]:
    """Build a comprehensive dict with all possible fields for JSON export."""
    student = instance.student
    group_label = ""

    if student:
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
        "instance_status": instance.status,
        "exam_name": exam.name,
        "subject_name": exam.subject.name if hasattr(exam, "subject") else "",
        "course_label": exam.subject.course.label if hasattr(exam.subject, "course") else "",
        "coordinator_email": (
            exam.subject.coordinator.email if hasattr(exam.subject, "coordinator") else ""
        ),
    }


def _resolve_value(instance, field_path: str) -> str:
    """Given an instance and a dotted path, return the string representation.

    Supports special keys:
    - "group": resolves the student's group label in the exam's subject.
    - "total_score": returns the instance's total score as string.
    """
    # Special synthetic fields.
    if field_path == "group":
        student = instance.student
        if student:
            membership = (
                SubjectMembership.unfiltered.filter(
                    user=student,
                    subject=instance.exam.subject,
                    is_active=True,
                )
                .select_related("group")
                .first()
            )
            return membership.group.label if (membership and membership.group) else ""
        return ""

    if field_path == "total_score":
        return str(instance.total_score) if instance.total_score is not None else ""

    # General case: follow the relationship chain.
    parts = field_path.split("__")
    value = instance
    for part in parts:
        if value is None:
            return ""
        value = getattr(value, part, None)

    if value is None:
        return ""
    return str(value)
