"""
Exam management business logic.

Handles CRUD for all exam-related entities:
- Exam creation with auto-generated first model (RF-6.1)
- Model creation with optional structure copy (RF-6.5)
- Blank PDF upload with page extraction (RF-6.7, RF-6.14)
- PageProfile and zone management (RF-6.8)
- Problem and rubric management (RF-6.9, RF-6.11)
- Convocation management (RF-6.4)

References: RF-6.1 through RF-6.14
"""

from __future__ import annotations

import io
import logging
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction

from apps.exams.models import (
    DEFAULT_STUDENT_PERMISSIONS,
    Exam,
    ExamConvocation,
    ExamModel,
    PageProfile,
    Problem,
    RecognitionZone,
    RubricCriterion,
    ZoneType,
)
from apps.instances.models import ExamInstance

logger = logging.getLogger(__name__)
User = get_user_model()


class ExamServiceError(Exception):
    """Raised when an exam operation violates business rules."""

    def __init__(self, code: str, detail: str = "", field: str = "") -> None:
        self.code = code
        self.field = field
        super().__init__(detail or code)


# ── Exam CRUD ────────────────────────────────────────────────


def create_exam(
    *,
    organization,
    subject,
    name: str,
) -> Exam:
    """Create an exam with an auto-generated first model "A" (RF-6.1)."""
    if not subject.course.is_active:
        raise ExamServiceError(code="COURSE_ARCHIVED")

    with transaction.atomic():
        exam = Exam.objects.create(
            organization=organization,
            name=name,
            subject=subject,
            student_permissions=dict(DEFAULT_STUDENT_PERMISSIONS),
        )
        # Auto-create first model "A".
        ExamModel.objects.create(label="A", exam=exam)

    return exam


def update_exam(exam: Exam, *, data: dict) -> dict[str, Any]:
    """Update exam fields (RF-6.2). Returns changes dict."""
    if not exam.subject.course.is_active:
        raise ExamServiceError(code="COURSE_ARCHIVED")

    changes: dict[str, Any] = {}

    if "name" in data and data["name"] != exam.name:
        changes["name"] = {"old": exam.name, "new": data["name"]}
        exam.name = data["name"]

    if "student_permissions" in data:
        old_perms = dict(exam.student_permissions)
        new_perms = data["student_permissions"]
        if old_perms != new_perms:
            changes["student_permissions"] = {"old": old_perms, "new": new_perms}
            exam.student_permissions = new_perms

    if changes:
        exam.save()

    return changes


def delete_exam(exam: Exam) -> None:
    """Delete exam if no instances exist (RF-6.3).

    MinIO objects (blank PDFs, instrumented PDFs) are scheduled for
    deletion via ``transaction.on_commit`` to keep DB and storage in
    sync: the cleanup only runs if the row delete actually commits.
    """
    from apps.core.tasks import cleanup_minio_orphans

    if not exam.subject.course.is_active:
        raise ExamServiceError(code="COURSE_ARCHIVED")

    if ExamInstance.unfiltered.filter(exam=exam).exists():
        raise ExamServiceError(code="HAS_INSTANCES")

    refs = _collect_exam_storage_refs(exam)

    with transaction.atomic():
        exam.delete()
        if refs:
            transaction.on_commit(lambda: cleanup_minio_orphans.delay(refs))


def _collect_exam_storage_refs(exam: Exam) -> list[str]:
    """Gather all MinIO references owned by the exam's models."""
    refs: list[str] = []
    for model in exam.models.all():
        if model.blank_pdf_ref:
            refs.append(model.blank_pdf_ref)
        if model.instrumented_pdf_ref:
            refs.append(model.instrumented_pdf_ref)
    return refs


# ── ExamModel CRUD ───────────────────────────────────────────


def create_model(
    *,
    exam: Exam,
    label: str,
    copy_from_id: str | None = None,
) -> ExamModel:
    """Create a model, optionally copying structure from another (RF-6.5)."""
    if not exam.subject.course.is_active:
        raise ExamServiceError(code="COURSE_ARCHIVED")

    try:
        with transaction.atomic():
            new_model = ExamModel.objects.create(
                label=label,
                exam=exam,
                instrumented_pdf_valid=False,
            )

            if copy_from_id:
                _copy_model_structure(source_id=copy_from_id, target=new_model)

    except IntegrityError:
        raise ExamServiceError(
            code="MODEL_LABEL_EXISTS",
            detail=f"Model '{label}' already exists in this exam.",
            field="label",
        ) from None

    return new_model


def _copy_model_structure(*, source_id: str, target: ExamModel) -> None:
    """Copy page profiles, zones, problems, rubrics from source model."""
    try:
        source = ExamModel.objects.get(pk=source_id, exam=target.exam)
    except ExamModel.DoesNotExist:
        raise ExamServiceError(code="SOURCE_MODEL_NOT_FOUND", field="copy_from_id") from None

    # Copy blank PDF reference (shared, not duplicated).
    if source.blank_pdf_ref:
        target.blank_pdf_ref = source.blank_pdf_ref
        target.blank_pdf_pages = source.blank_pdf_pages
        target.blank_pdf_size = source.blank_pdf_size
        target.save()

    # Copy page profiles and zones.
    zone_mapping: dict[str, RecognitionZone] = {}  # old_zone_pk → new_zone

    for profile in source.page_profiles.all():
        new_profile = PageProfile.objects.create(
            exam_model=target,
            page_number=profile.page_number,
            page_width=profile.page_width,
            page_height=profile.page_height,
        )
        for zone in profile.zones.all():
            new_zone = RecognitionZone.objects.create(
                page_profile=new_profile,
                zone_type=zone.zone_type,
                attribute=zone.attribute,
                x=zone.x,
                y=zone.y,
                width=zone.width,
                height=zone.height,
            )
            zone_mapping[str(zone.pk)] = new_zone

    # Copy problems and their rubrics.
    for problem in source.problems.all():
        old_zone_pks = list(problem.zones.values_list("pk", flat=True))
        new_problem = Problem.objects.create(
            name=problem.name,
            max_score=problem.max_score,
            exam_model=target,
            order=problem.order,
        )
        # Map old zones to new zones.
        for old_pk in old_zone_pks:
            new_zone = zone_mapping.get(str(old_pk))
            if new_zone:
                new_problem.zones.add(new_zone)

        # Copy rubric criteria.
        for criterion in problem.rubric_criteria.all():
            RubricCriterion.objects.create(
                problem=new_problem,
                description=criterion.description,
                score=criterion.score,
                order=criterion.order,
            )


# ── Blank PDF upload ─────────────────────────────────────────


def upload_blank_pdf(model: ExamModel, file_data: bytes, filename: str) -> dict:
    """Upload a blank PDF for a model (RF-6.7, RF-6.14).

    Stores in MinIO, extracts page count and dimensions,
    and invalidates the instrumented PDF.

    Returns:
        Dict with pages count and storage reference.
    """
    from apps.exams.services.storage import upload_to_minio

    # Extract page info from PDF.
    page_info = _extract_pdf_info(file_data)

    # Build MinIO key.
    exam = model.exam
    key = f"{exam.organization_id}/exams/{exam.pk}/models/{model.pk}/blank.pdf"

    # Delete old file if exists.
    if model.blank_pdf_ref:
        from apps.exams.services.storage import delete_minio_object

        delete_minio_object(model.blank_pdf_ref)

    # Upload new file.
    upload_to_minio(key=key, data=file_data, content_type="application/pdf")

    # Update model metadata.
    model.blank_pdf_ref = key
    model.blank_pdf_pages = page_info["num_pages"]
    model.blank_pdf_size = len(file_data)
    model.instrumented_pdf_valid = False
    model.save()

    return {
        "blank_pdf_ref": key,
        "pages": page_info["num_pages"],
        "page_dimensions": page_info["page_dimensions"],
        "size_bytes": len(file_data),
    }


def _extract_pdf_info(pdf_bytes: bytes) -> dict:
    """Extract page count and dimensions from a PDF.

    Returns:
        {
            "num_pages": int,
            "page_dimensions": [{"page": 1, "width": 595.0, "height": 842.0}, ...]
        }
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(pdf_bytes))
        num_pages = len(reader.pages)
        dimensions = []

        for i, page in enumerate(reader.pages):
            box = page.mediabox
            dimensions.append(
                {
                    "page": i + 1,
                    "width": float(box.width),
                    "height": float(box.height),
                }
            )

        return {"num_pages": num_pages, "page_dimensions": dimensions}
    except Exception as exc:
        raise ExamServiceError(
            code="INVALID_PDF",
            detail=f"Could not read PDF: {exc}",
        ) from exc


# ── PageProfile & Zone management ────────────────────────────


def create_page_profile(
    *,
    exam_model: ExamModel,
    page_number: int,
) -> PageProfile:
    """Create a page profile for a model page (RF-6.8)."""
    if page_number < 1 or (
        exam_model.blank_pdf_pages > 0 and page_number > exam_model.blank_pdf_pages
    ):
        raise ExamServiceError(
            code="INVALID_PAGE_NUMBER",
            detail=f"Page {page_number} is out of range (1-{exam_model.blank_pdf_pages}).",
            field="page_number",
        )

    # Get page dimensions from blank PDF if available.
    page_width = 0.0
    page_height = 0.0
    if exam_model.blank_pdf_ref:
        from botocore.exceptions import BotoCoreError, ClientError

        from apps.exams.services.storage import download_from_minio

        try:
            pdf_bytes = download_from_minio(exam_model.blank_pdf_ref)
        except (ClientError, BotoCoreError) as exc:
            raise ExamServiceError(
                code="BLANK_PDF_UNREADABLE",
                detail=f"Could not read blank PDF from storage: {exc}",
            ) from exc
        info = _extract_pdf_info(pdf_bytes)
        for dim in info["page_dimensions"]:
            if dim["page"] == page_number:
                page_width = dim["width"]
                page_height = dim["height"]
                break
        else:
            raise ExamServiceError(
                code="INVALID_PAGE_NUMBER",
                detail=(
                    f"Page {page_number} is not present in the blank PDF "
                    f"(it has {len(info['page_dimensions'])} pages)."
                ),
                field="page_number",
            )

    try:
        profile = PageProfile.objects.create(
            exam_model=exam_model,
            page_number=page_number,
            page_width=page_width,
            page_height=page_height,
        )
    except IntegrityError:
        raise ExamServiceError(
            code="PAGE_PROFILE_EXISTS",
            detail=f"Page profile for page {page_number} already exists.",
            field="page_number",
        ) from None

    return profile


def create_zone(
    *,
    page_profile: PageProfile,
    zone_type: str,
    attribute: str,
    x: float,
    y: float,
    width: float,
    height: float,
) -> RecognitionZone:
    """Create a recognition zone within a page profile (RF-6.8)."""
    # Validate zone type.
    if zone_type not in ZoneType.values:
        raise ExamServiceError(code="INVALID_ZONE_TYPE", field="zone_type")

    # Force attribute for QR zones.
    if zone_type == ZoneType.QR:
        attribute = "exam_qr"

    # Basic coordinate validation.
    if width <= 0 or height <= 0:
        raise ExamServiceError(code="INVALID_DIMENSIONS", field="width")
    if x < 0 or y < 0:
        raise ExamServiceError(code="INVALID_COORDINATES", field="x")

    # Validate against page dimensions if known.
    if page_profile.page_width > 0 and page_profile.page_height > 0:
        if x + width > page_profile.page_width + 1:  # 1pt tolerance
            raise ExamServiceError(code="ZONE_EXCEEDS_PAGE_WIDTH", field="x")
        if y + height > page_profile.page_height + 1:
            raise ExamServiceError(code="ZONE_EXCEEDS_PAGE_HEIGHT", field="y")

    zone = RecognitionZone.objects.create(
        page_profile=page_profile,
        zone_type=zone_type,
        attribute=attribute,
        x=x,
        y=y,
        width=width,
        height=height,
    )

    # Invalidate instrumented PDF when QR zones change.
    if zone_type == ZoneType.QR:
        page_profile.exam_model.invalidate_instrumented_pdf()

    return zone


def delete_zone(zone: RecognitionZone) -> None:
    """Delete a zone. Invalidates instrumented PDF if QR zone."""
    is_qr = zone.zone_type == ZoneType.QR
    model = zone.page_profile.exam_model
    zone.delete()
    if is_qr:
        model.invalidate_instrumented_pdf()


# ── Problem & Rubric ─────────────────────────────────────────


def create_problem(
    *,
    exam_model: ExamModel,
    name: str,
    max_score: Decimal,
    order: int = 0,
    zone_ids: list[str] | None = None,
) -> Problem:
    """Create a problem within a model (RF-6.9)."""
    problem = Problem.objects.create(
        name=name,
        max_score=max_score,
        exam_model=exam_model,
        order=order,
    )
    if zone_ids:
        zones = RecognitionZone.objects.filter(
            pk__in=zone_ids,
            page_profile__exam_model=exam_model,
        )
        problem.zones.set(zones)

    return problem


def set_rubric(
    *,
    problem: Problem,
    criteria: list[dict],
) -> list[RubricCriterion]:
    """Replace the rubric for a problem (RF-6.11).

    Deletes existing criteria and creates new ones.
    """
    problem.rubric_criteria.all().delete()

    created = []
    for i, item in enumerate(criteria):
        criterion = RubricCriterion.objects.create(
            problem=problem,
            description=item["description"],
            score=Decimal(str(item["score"])),
            order=i,
        )
        created.append(criterion)

    return created


# ── Convocation ──────────────────────────────────────────────


def set_convocation(
    *,
    exam: Exam,
    student_ids: list[str],
) -> dict:
    """Replace the exam's convocation list (RF-6.4).

    Validates that all students are active members of the subject
    with an assigned group.

    Returns:
        Dict with counts: valid, invalid, total.
    """
    from apps.subjects.models import MembershipRole, SubjectMembership

    valid_students = []
    invalid_entries = []

    for student_id in student_ids:
        membership = SubjectMembership.unfiltered.filter(
            user_id=student_id,
            subject=exam.subject,
            role=MembershipRole.STUDENT,
            is_active=True,
        ).first()
        if not membership:
            invalid_entries.append(
                {
                    "user_id": student_id,
                    "reason": "NOT_ACTIVE_STUDENT",
                }
            )
        elif not membership.group:
            invalid_entries.append(
                {
                    "user_id": student_id,
                    "reason": "NO_GROUP_ASSIGNED",
                }
            )
        else:
            valid_students.append(student_id)

    # Replace convocation list atomically.
    with transaction.atomic():
        ExamConvocation.objects.filter(exam=exam).delete()
        ExamConvocation.objects.bulk_create(
            [ExamConvocation(exam=exam, student_id=sid) for sid in valid_students]
        )

    return {
        "total": len(student_ids),
        "valid": len(valid_students),
        "invalid": invalid_entries,
    }
