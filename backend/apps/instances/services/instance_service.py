"""
Exam instance business logic.

Core operations:
- State machine transitions with validation (RF-7.5)
- Instance creation/modification (RF-7.1, RF-7.2)
- Page management: reorder, delete, move, attach (RF-7.14)
- Issue recalculation (RF-7.6, RF-7.7)
- PDF composition on demand (RF-7.15)
- Bulk publish (RF-7.9)
- Instance deletion with MinIO cleanup (RF-7.10)

References: RF-7.1 through RF-7.15
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
from decimal import Decimal
from typing import Any

from django.db import transaction

from apps.instances.models.instances import (
    ExamInstance,
    ExamPage,
    InstanceIssueType,
    InstanceStatus,
    PageStatus,
    is_valid_transition,
)

logger = logging.getLogger(__name__)


class InstanceServiceError(Exception):
    """Raised when an instance operation violates business rules."""

    def __init__(self, code: str, detail: str = "", field: str = "") -> None:
        self.code = code
        self.field = field
        super().__init__(detail or code)


def create_instance(
    *,
    exam,
    organization,
    student_id: str | None = None,
    model_id: str | None = None,
    expected_pages: int = 0,
) -> ExamInstance:
    """Create an instance manually (manager only)."""
    return ExamInstance.objects.create(
        organization=organization,
        exam=exam,
        student_id=student_id,
        model_id=model_id,
        expected_pages=expected_pages,
        status=InstanceStatus.ASSEMBLING,
    )


# ── State machine ────────────────────────────────────────────


def transition_instance(
    instance: ExamInstance,
    target_status: str,
) -> None:
    """Execute a state transition with validation (RF-7.5).

    The check-and-update is serialised with ``SELECT FOR UPDATE`` so two
    concurrent transitions cannot both succeed when only one is legal —
    in particular this prevents two parallel ``GRADED → PUBLISHED``
    requests from racing past the ``has_issues`` precondition.

    Raises:
        InstanceServiceError: If the transition is illegal or
            preconditions are not met.
    """
    with transaction.atomic():
        locked = ExamInstance.unfiltered.select_for_update().get(pk=instance.pk)

        if not is_valid_transition(locked.status, target_status):
            raise InstanceServiceError(
                code="INVALID_TRANSITION",
                detail=f"Cannot transition from {locked.status} to {target_status}.",
            )

        if target_status == InstanceStatus.PUBLISHED and locked.has_issues:
            raise InstanceServiceError(
                code="HAS_UNRESOLVED_ISSUES",
                detail="Cannot publish an instance with unresolved issues.",
            )

        locked.status = target_status
        locked.save(update_fields=["status", "updated_at"])

    # Reflect the new state on the caller's instance object.
    instance.status = target_status


def check_assembling_complete(instance: ExamInstance) -> bool:
    """Check if all expected pages have arrived and auto-transition.

    Returns True if the instance transitioned to RECEIVED.
    """
    if instance.status != InstanceStatus.ASSEMBLING:
        return False

    if instance.expected_pages <= 0:
        return False

    distinct_pages = (
        instance.pages.exclude(status=PageStatus.DISCARDED)
        .values("page_number")
        .distinct()
        .count()
    )
    if distinct_pages >= instance.expected_pages:
        instance.status = InstanceStatus.RECEIVED
        instance.save(update_fields=["status", "updated_at"])
        return True

    return False


# ── Instance CRUD ────────────────────────────────────────────


def update_instance(
    instance: ExamInstance,
    *,
    data: dict,
) -> dict[str, Any]:
    """Update instance fields (RF-7.2). Returns changes dict.

    Handles student assignment with duplicate detection.
    """
    changes: dict[str, Any] = {}

    if "student_id" in data:
        new_student_id = data["student_id"]
        old_student_id = str(instance.student_id) if instance.student_id else None

        if str(new_student_id) != old_student_id:
            # Check for duplicate student.
            duplicate = (
                ExamInstance.unfiltered.filter(
                    exam=instance.exam,
                    student_id=new_student_id,
                )
                .exclude(pk=instance.pk)
                .exists()
            )

            if duplicate:
                raise InstanceServiceError(
                    code="DUPLICATE_STUDENT",
                    detail="This student already has an instance for this exam.",
                    field="student_id",
                )

            from django.contrib.auth import get_user_model

            user_model = get_user_model()
            try:
                student = user_model.objects.get(pk=new_student_id)
            except user_model.DoesNotExist:
                raise InstanceServiceError(code="USER_NOT_FOUND", field="student_id") from None

            changes["student"] = {"old": old_student_id, "new": str(new_student_id)}
            instance.student = student

            # Resolve STUDENT_NOT_IDENTIFIED issue if present.
            instance.remove_issue(InstanceIssueType.STUDENT_NOT_IDENTIFIED)
            instance.remove_issue(InstanceIssueType.DUPLICATE_STUDENT)

    if "model_id" in data:
        from apps.exams.models.exams import ExamModel

        new_model_id = data["model_id"]
        old_model_id = str(instance.model_id) if instance.model_id else None

        if str(new_model_id) != old_model_id:
            try:
                model = ExamModel.objects.get(pk=new_model_id, exam=instance.exam)
            except ExamModel.DoesNotExist:
                raise InstanceServiceError(code="MODEL_NOT_FOUND", field="model_id") from None

            changes["model"] = {"old": old_model_id, "new": str(new_model_id)}
            instance.model = model
            instance.expected_pages = model.page_profiles.count()

    if changes:
        instance.recalculate_issues()
        instance.save()

    return changes


def delete_instance(instance: ExamInstance) -> None:
    """Delete an instance and its pages + MinIO files (RF-7.10).

    The MinIO cleanup is scheduled via ``transaction.on_commit`` so it
    only runs after the DB delete has actually persisted; if the
    transaction rolls back, no MinIO objects are touched. The cleanup
    task is idempotent and retries on failure, so partial MinIO
    failures do not leave half-deleted state.
    """
    from apps.core.tasks.maintenance import cleanup_minio_orphans

    refs: list[str] = [page.storage_ref for page in instance.pages.all() if page.storage_ref]

    with transaction.atomic():
        instance.delete()
        if refs:
            transaction.on_commit(lambda: cleanup_minio_orphans.delay(refs))


# ── Page management (RF-7.14) ────────────────────────────────


def reorder_pages(instance: ExamInstance, page_ids: list[str]) -> None:
    """Reorder pages within an instance."""
    pages = {str(p.pk): p for p in instance.pages.all()}

    for i, page_id in enumerate(page_ids, start=1):
        page = pages.get(page_id)
        if page:
            page.page_number = i
            page.save(update_fields=["page_number", "updated_at"])

    instance.recalculate_issues()
    instance.save(update_fields=["has_issues", "issue_types", "updated_at"])


def discard_page(page: ExamPage) -> None:
    """Mark a page as discarded (logical delete, RF-7.14)."""
    page.status = PageStatus.DISCARDED
    page.issue_type = ""
    page.save(update_fields=["status", "issue_type", "updated_at"])

    if page.instance:
        page.instance.recalculate_issues()
        page.instance.save(update_fields=["has_issues", "issue_types", "updated_at"])


def move_page(page: ExamPage, target_instance: ExamInstance) -> None:
    """Move a page to a different instance of the same exam (RF-7.14)."""
    if page.instance and page.instance.exam_id != target_instance.exam_id:
        raise InstanceServiceError(
            code="DIFFERENT_EXAM",
            detail="Can only move pages within the same exam.",
        )

    old_instance = page.instance
    page.instance = target_instance
    page.page_number = target_instance.pages.count() + 1
    page.save(update_fields=["instance", "page_number", "updated_at"])

    # Recalculate issues on both instances.
    if old_instance:
        old_instance.recalculate_issues()
        old_instance.save(update_fields=["has_issues", "issue_types", "updated_at"])

    target_instance.recalculate_issues()
    target_instance.save(update_fields=["has_issues", "issue_types", "updated_at"])
    check_assembling_complete(target_instance)


def attach_orphan_page(page: ExamPage, target_instance: ExamInstance) -> None:
    """Attach an orphan page to an instance (RF-7.14)."""
    if page.instance is not None:
        raise InstanceServiceError(
            code="PAGE_NOT_ORPHAN",
            detail="This page is already attached to an instance.",
        )

    page.instance = target_instance
    page.page_number = target_instance.pages.count() + 1
    page.status = PageStatus.RECOGNIZED  # Assume it's been recognized.
    page.issue_type = ""  # Clear ORPHAN_PAGE issue.
    page.save()

    target_instance.recalculate_issues()
    target_instance.save(update_fields=["has_issues", "issue_types", "updated_at"])
    check_assembling_complete(target_instance)


def accept_extra_page(page: ExamPage) -> None:
    """Accept an extra page, clearing its issue without deleting it (RF-7.14)."""
    page.issue_type = ""
    page.save(update_fields=["issue_type", "updated_at"])

    if page.instance:
        page.instance.recalculate_issues()
        page.instance.save(update_fields=["has_issues", "issue_types", "updated_at"])


# ── Grading helpers ──────────────────────────────────────────


def recalculate_total_score(instance: ExamInstance) -> Decimal:
    """Recalculate total_score from all grades (RF-11.3). Min 0."""
    from django.db.models import Sum

    from apps.grading.models.grading import Grade

    total = Grade.objects.filter(instance=instance).aggregate(total=Sum("score"))[
        "total"
    ] or Decimal("0.00")

    # Minimum score is 0.
    instance.total_score = max(total, Decimal("0.00"))
    instance.save(update_fields=["total_score", "updated_at"])
    return instance.total_score


# ── Bulk publish (RF-7.9) ────────────────────────────────────


def bulk_publish(exam, *, notify_students: bool = True) -> dict[str, Any]:
    """Publish all GRADED instances for an exam (RF-7.9).

    When ``notify_students`` is true (the default and the requirement of
    RF-7.9), every student whose instance has just been published is
    notified through the notification system.

    Returns:
        Dict with counts: published, skipped, errors.
    """
    from apps.notifications.services.notifications import notify_grades_published

    instances = ExamInstance.unfiltered.filter(
        exam=exam,
        status=InstanceStatus.GRADED,
    )

    published = 0
    skipped = 0
    errors: list[dict[str, str]] = []
    students_to_notify: list = []

    for instance in instances:
        if instance.has_issues:
            skipped += 1
            errors.append(
                {
                    "instance_id": str(instance.pk),
                    "reason": "HAS_UNRESOLVED_ISSUES",
                }
            )
            continue

        try:
            instance.status = InstanceStatus.PUBLISHED
            instance.save(update_fields=["status", "updated_at"])
            published += 1
            if instance.student is not None:
                students_to_notify.append(instance.student)
        except Exception as exc:
            errors.append(
                {
                    "instance_id": str(instance.pk),
                    "reason": str(exc),
                }
            )

    if notify_students and students_to_notify:
        notify_grades_published(exam, students_to_notify)

    return {
        "published": published,
        "skipped": skipped,
        "total": published + skipped,
        "errors": errors,
    }


# ── PDF composition on demand (RF-7.15) ──────────────────────


def compose_instance_pdf(
    instance: ExamInstance,
    *,
    watermark_for_student: bool = False,
    encrypt_for_user_id: str | None = None,
) -> bytes:
    """Compose a PDF from the instance's ExamPages (RF-7.15).

    Downloads each page from MinIO. All pages are expected to be raster
    images (PNG) after the ingestion normalization step.

    When ``watermark_for_student`` is true, every page is stamped with
    a dynamic watermark identifying the student before being embedded
    (RF-16.6).
    """
    from apps.exams.services.storage import download_from_minio
    from apps.instances.services.watermark import WatermarkService

    pages = instance.pages.exclude(status=PageStatus.DISCARDED).order_by("page_number")

    if not pages.exists():
        raise InstanceServiceError(
            code="NO_PAGES",
            detail="Instance has no pages to compose.",
        )

    student = instance.student if watermark_for_student else None
    student_name = ""
    student_nia = ""
    if student is not None:
        student_name = student.full_name or student.email
        student_nia = getattr(student, "nia", "") or ""

    from pypdf import PdfWriter

    writer = PdfWriter()

    for page in pages:
        if not page.storage_ref:
            continue
        try:
            page_bytes = download_from_minio(page.storage_ref)
        except Exception as exc:
            logger.warning("Failed to download page %s: %s", page.pk, exc)
            continue

        # Apply watermark if this is a student download.
        if student is not None:
            page_bytes = WatermarkService.apply_watermark(page_bytes, student_name, student_nia)

        # Convert the (possibly watermarked) image to a PDF page and append.
        writer.append(io.BytesIO(_image_to_pdf_page(page_bytes)))

    output = io.BytesIO()
    writer.write(output)
    writer.close()
    pdf_bytes = output.getvalue()

    if encrypt_for_user_id:
        key = derive_user_key(encrypt_for_user_id)
        pdf_bytes = encrypt_pdf(pdf_bytes, key)

    return pdf_bytes


def _image_to_pdf_page(image_bytes: bytes) -> bytes:
    """Convert a raster image (PNG/JPEG/TIFF) to a single-page PDF."""
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    out = io.BytesIO()
    image.save(out, format="PDF")
    return out.getvalue()


def encrypt_pdf(pdf_bytes: bytes, key: bytes) -> bytes:
    """Cifra el PDF con AES-256-GCM usando la clave proporcionada."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, pdf_bytes, None)
    # Devolvemos nonce + ciphertext (el frontend debe extraer el nonce).
    return nonce + ciphertext


def derive_user_key(user_id: str) -> bytes:
    """Deriva una clave de 32 bytes a partir del user_id."""
    return hashlib.sha256(f"scdee-pdf-key-{user_id}".encode()).digest()
