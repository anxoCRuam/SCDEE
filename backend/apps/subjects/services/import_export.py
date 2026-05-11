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
from django.db import IntegrityError, transaction

from apps.courses.models.courses import AcademicCourse
from apps.subjects.models.subjects import (
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
    name = row.get("name", "").strip()
    code = row.get("code", "").strip()
    semester = row.get("semester", "").strip()
    coordinator_email = row.get("coordinator_email", "").strip()
    groups = row.get("groups", [])
    teachers = row.get("teachers", [])
    students = row.get("students", [])

    if not name or not code or not coordinator_email:
        result.errors.append(
            {
                "row": row_num,
                "error": "VALIDATION_ERROR",
                "detail": "name, code, and coordinator_email are required.",
            }
        )
        return

    # Buscar coordinador
    try:
        coordinator = User.unfiltered.get(email=coordinator_email, organization=organization)
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
            # Obtener o crear el subject (tolerante a código duplicado)
            subject, created = Subject.unfiltered.get_or_create(
                code=code,
                course=active_course,
                organization=organization,
                defaults={
                    "name": name,
                    "semester": semester,
                    "coordinator": coordinator,
                },
            )
            if not created:
                # Si ya existía, actualizar nombre y semestre si vienen
                subject.name = name
                subject.semester = semester
                subject.coordinator = coordinator
                subject.save()

            # Coordinador siempre queda con membresía activa
            SubjectMembership.unfiltered.get_or_create(
                organization=organization,
                user=coordinator,
                subject=subject,
                role=MembershipRole.COORDINATOR,
                defaults={"is_active": True},
            )
            # Asegurar que esté activa
            SubjectMembership.unfiltered.filter(
                organization=organization,
                user=coordinator,
                subject=subject,
                role=MembershipRole.COORDINATOR,
            ).update(is_active=True)

            # Grupos: crear los que falten
            group_map = {}
            for group_label in groups:
                label = str(group_label).strip()
                if label:
                    g, _ = SubjectGroup.objects.get_or_create(subject=subject, label=label)
                    group_map[label] = g

            # Profesores
            for teacher_email in teachers:
                email = str(teacher_email).strip()
                if not email:
                    continue
                try:
                    teacher = User.unfiltered.get(email=email, organization=organization)
                    SubjectMembership.unfiltered.get_or_create(
                        organization=organization,
                        user=teacher,
                        subject=subject,
                        role=MembershipRole.TEACHER,
                        defaults={"is_active": True},
                    )
                except User.DoesNotExist:
                    result.errors.append(
                        {
                            "row": row_num,
                            "error": "TEACHER_NOT_FOUND",
                            "detail": f"Teacher {email} not found.",
                        }
                    )

            # Estudiantes
            for student_data in students:
                email = str(student_data.get("email", "")).strip()
                group_label = str(student_data.get("group", "")).strip()
                if not email:
                    continue
                group = group_map.get(group_label)
                try:
                    student = User.unfiltered.get(email=email, organization=organization)
                    membership, _ = SubjectMembership.unfiltered.get_or_create(
                        organization=organization,
                        user=student,
                        subject=subject,
                        role=MembershipRole.STUDENT,
                        defaults={"is_active": True, "group": group},
                    )
                    if not _ and group is not None:
                        membership.group = group
                        membership.save(update_fields=["group"])
                except User.DoesNotExist:
                    result.errors.append(
                        {
                            "row": row_num,
                            "error": "STUDENT_NOT_FOUND",
                            "detail": f"Student {email} not found.",
                        }
                    )

        # Se procesó con éxito (sea creado o ya existente)
        result.created += 1

    except IntegrityError as exc:
        # Esta excepción puede saltar si hay problemas de unicidad en membresías,
        # pero con get_or_create no debería ocurrir. La mantenemos como red de seguridad.
        result.errors.append(
            {
                "row": row_num,
                "error": "CREATION_FAILED",
                "detail": str(exc),
            }
        )


def export_subjects(queryset, file_format: str = "json") -> str:
    """Exporta subjects en el mismo formato que la importación."""
    data = []
    for subject in queryset.prefetch_related("groups", "memberships__user"):
        # Grupos como lista de labels
        groups = list(subject.groups.values_list("label", flat=True))
        # Profesores y estudiantes desde las membresías activas
        teachers = []
        students = []
        for m in subject.memberships.filter(is_active=True):
            if m.role == MembershipRole.TEACHER:
                teachers.append(m.user.email)
            elif m.role == MembershipRole.STUDENT:
                students.append(
                    {
                        "email": m.user.email,
                        "group": m.group.label if m.group else "",
                    }
                )
        # Coordinador
        coordinator_email = subject.coordinator.email if subject.coordinator else ""

        data.append(
            {
                "name": subject.name,
                "code": subject.code,
                "semester": subject.semester,
                "coordinator_email": coordinator_email,
                "groups": groups,
                "teachers": teachers,
                "students": students,
            }
        )

    if file_format == "json":
        return json.dumps(data, ensure_ascii=False, indent=2)

    # CSV
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "name",
            "code",
            "semester",
            "coordinator_email",
            "groups",
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
                "teachers": ";".join(item["teachers"]),
                "students": ";".join(
                    f"{s['email']}:{s['group']}" for s in item["students"] if s["group"]
                ),
            }
        )
    return output.getvalue()


def parse_csv_to_subject_list(csv_content: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(csv_content))
    subjects = []
    for row in reader:
        # Convertir grupos: cadena 'G1;G2' a lista
        groups = [g.strip() for g in row.get("groups", "").split(";") if g.strip()]
        # Convertir profesores: cadena 'email1;email2' a lista
        teachers = [t.strip() for t in row.get("teachers", "").split(";") if t.strip()]
        # Convertir estudiantes: cadena 'email1:group1;email2:group2' a lista de dicts
        students_raw = row.get("students", "")
        students = []
        if students_raw:
            for part in students_raw.split(";"):
                if ":" in part:
                    email, group = part.split(":", 1)
                    students.append({"email": email.strip(), "group": group.strip()})
                else:
                    students.append({"email": part.strip(), "group": ""})

        subjects.append(
            {
                "name": row["name"].strip(),
                "code": row["code"].strip(),
                "semester": row.get("semester", "").strip(),
                "coordinator_email": row["coordinator_email"].strip(),
                "groups": groups,
                "teachers": teachers,
                "students": students,
            }
        )
    return subjects
