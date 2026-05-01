"""
Subject import/export business logic.

Import (RF-4.2):
    JSON-only format with nested structure:
    [
      {
        "name": "Matemáticas I",
        "code": "MAT1",
        "semester": "1er cuatrimestre",
        "coordinator_email": "coord@example.com",
        "groups": ["G1", "G2"],
        "teachers": ["prof@example.com"],
        "students": [
          {"email": "alumno@example.com", "group": "G1"}
        ]
      }
    ]

Export (RF-4.10):
    Generates JSON or CSV with subject data, groups, and member counts.

References: RF-4.2, RF-4.10
"""

from __future__ import annotations

import csv
import io
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from django.contrib.auth import get_user_model
from django.db import transaction

from apps.courses.models import AcademicCourse
from apps.subjects.models import (
    MembershipRole,
    Subject,
    SubjectGroup,
    SubjectMembership,
)

logger = logging.getLogger(__name__)
User = get_user_model()


@dataclass
class SubjectImportResult:
    """Report of a bulk subject import."""

    created: int = 0
    errors: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_errors(self) -> int:
        return len(self.errors)

    def to_dict(self) -> dict:
        return {
            "created": self.created,
            "total_errors": self.total_errors,
            "errors": self.errors,
        }


def import_subjects(
    organization,
    data: list[dict],
) -> SubjectImportResult:
    """Import subjects from a parsed JSON array.

    Each subject is processed independently — errors on one row
    don't abort the others.
    """
    result = SubjectImportResult()

    active_course = AcademicCourse.unfiltered.filter(
        organization=organization, is_active=True
    ).first()
    if active_course is None:
        result.errors.append({"row": 0, "error": "NO_ACTIVE_COURSE"})
        return result

    for row_num, row in enumerate(data, start=1):
        try:
            _process_subject_import_row(organization, active_course, row, row_num, result)
        except Exception as exc:
            logger.warning("Subject import row %d failed: %s", row_num, exc)
            result.errors.append(
                {
                    "row": row_num,
                    "error": "UNEXPECTED_ERROR",
                    "detail": str(exc),
                }
            )

    return result


def _process_subject_import_row(
    organization,
    active_course,
    row: dict,
    row_num: int,
    result: SubjectImportResult,
) -> None:
    """Process a single subject import row."""
    name = row.get("name", "").strip()
    code = row.get("code", "").strip()
    semester = row.get("semester", "").strip()
    coordinator_email = row.get("coordinator_email", "").strip()
    groups = row.get("groups", [])
    teachers = row.get("teachers", [])
    students = row.get("students", [])

    # Validate required fields.
    if not name or not code or not coordinator_email:
        result.errors.append(
            {
                "row": row_num,
                "error": "VALIDATION_ERROR",
                "detail": "name, code, and coordinator_email are required.",
            }
        )
        return

    # Find coordinator.
    try:
        coordinator = User.objects.get(email=coordinator_email, organization=organization)
    except User.DoesNotExist:
        result.errors.append(
            {
                "row": row_num,
                "error": "COORDINATOR_NOT_FOUND",
                "detail": f"User {coordinator_email} not found.",
            }
        )
        return

    try:
        with transaction.atomic():
            # Create subject.
            subject = Subject(
                organization=organization,
                name=name,
                code=code,
                semester=semester,
                course=active_course,
                coordinator=coordinator,
            )
            subject.save()

            # Create coordinator membership.
            SubjectMembership.objects.create(
                organization=organization,
                user=coordinator,
                subject=subject,
                role=MembershipRole.COORDINATOR,
                is_active=True,
            )

            # Create groups.
            group_map: dict[str, SubjectGroup] = {}
            for group_label in groups:
                label = str(group_label).strip()
                if label:
                    g = SubjectGroup.objects.create(subject=subject, label=label)
                    group_map[label] = g

            # Assign teachers.
            for teacher_email in teachers:
                email = str(teacher_email).strip()
                if not email:
                    continue
                try:
                    teacher = User.objects.get(email=email, organization=organization)
                    SubjectMembership.objects.create(
                        organization=organization,
                        user=teacher,
                        subject=subject,
                        role=MembershipRole.TEACHER,
                        is_active=True,
                    )
                except User.DoesNotExist:
                    result.errors.append(
                        {
                            "row": row_num,
                            "error": "TEACHER_NOT_FOUND",
                            "detail": f"Teacher {email} not found (subject still created).",
                        }
                    )

            # Assign students.
            for student_data in students:
                email = str(student_data.get("email", "")).strip()
                group_label = str(student_data.get("group", "")).strip()
                if not email:
                    continue

                group = group_map.get(group_label)
                try:
                    student = User.objects.get(email=email, organization=organization)
                    SubjectMembership.objects.create(
                        organization=organization,
                        user=student,
                        subject=subject,
                        role=MembershipRole.STUDENT,
                        group=group,
                        is_active=True,
                    )
                except User.DoesNotExist:
                    result.errors.append(
                        {
                            "row": row_num,
                            "error": "STUDENT_NOT_FOUND",
                            "detail": f"Student {email} not found (subject still created).",
                        }
                    )

        result.created += 1

    except Exception as exc:
        if "unique_subject_code_per_course" in str(exc).lower():
            result.errors.append(
                {
                    "row": row_num,
                    "error": "CODE_ALREADY_EXISTS",
                    "detail": f"Subject code '{code}' already exists.",
                }
            )
        else:
            result.errors.append(
                {
                    "row": row_num,
                    "error": "CREATION_FAILED",
                    "detail": str(exc),
                }
            )


def export_subjects(queryset, file_format: str = "json") -> str:
    """Export subjects with groups and member counts (RF-4.10)."""
    data = []
    for subject in queryset.prefetch_related("groups", "memberships"):
        groups = list(subject.groups.values_list("label", flat=True))
        member_counts = {
            "coordinators": subject.memberships.filter(
                role=MembershipRole.COORDINATOR, is_active=True
            ).count(),
            "teachers": subject.memberships.filter(
                role=MembershipRole.TEACHER, is_active=True
            ).count(),
            "students": subject.memberships.filter(
                role=MembershipRole.STUDENT, is_active=True
            ).count(),
        }
        data.append(
            {
                "name": subject.name,
                "code": subject.code,
                "semester": subject.semester,
                "coordinator_email": subject.coordinator.email,
                "groups": groups,
                "member_counts": member_counts,
            }
        )

    if file_format == "json":
        return json.dumps(data, ensure_ascii=False, indent=2)

    # CSV: flatten structure.
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "name",
            "code",
            "semester",
            "coordinator_email",
            "groups",
            "coordinators",
            "teachers",
            "students",
        ],
    )
    writer.writeheader()
    for item in data:
        writer.writerow(
            {
                "name": item["name"],
                "code": item["code"],
                "semester": item["semester"],
                "coordinator_email": item["coordinator_email"],
                "groups": ";".join(item["groups"]),
                "coordinators": item["member_counts"]["coordinators"],
                "teachers": item["member_counts"]["teachers"],
                "students": item["member_counts"]["students"],
            }
        )
    return output.getvalue()
