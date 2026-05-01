"""
Instance assembler — groups pages into instances.

Handles:
- Page 1 (portada) → create new ExamInstance in ASSEMBLING.
- Subsequent pages → find existing instance and attach.
- Temporal proximity fallback for pages without QR (RF-9.11).
- Student identification from recognition results (RF-9.7).
- ASSEMBLING → RECEIVED transition when all pages arrive.

References: RF-9.10, RF-9.11, RF-7.1
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.db import transaction

from apps.instances.models import (
    ExamInstance,
    ExamPage,
    InstanceIssueType,
    InstanceStatus,
    PageIssueType,
    PageStatus,
)
from apps.instances.services.instance_service import check_assembling_complete

logger = logging.getLogger(__name__)

# Default temporal proximity window (seconds).
PROXIMITY_WINDOW_SECONDS = getattr(settings, "INGESTION_PROXIMITY_WINDOW_SECONDS", 30)


def assemble_page(page: ExamPage, recognition_results: dict) -> None:
    """Assemble a recognized page into an instance.

    Called by the dispatcher after recognition completes.
    """
    qr_payload = recognition_results.get("qr_payload")

    if qr_payload and qr_payload.get("valid"):
        _assemble_with_qr(page, qr_payload, recognition_results)
    else:
        _assemble_by_proximity(page)


def _assemble_with_qr(
    page: ExamPage,
    qr_payload: dict,
    recognition_results: dict,
) -> None:
    """Assemble a page using its QR data."""
    from apps.exams.models import Exam, ExamModel

    exam_id = qr_payload["exam_id"]
    model_id = qr_payload["model_id"]
    page_number = qr_payload["page_number"]

    # Validate exam and model exist.
    try:
        exam = Exam.unfiltered.get(pk=exam_id)
        model = ExamModel.objects.get(pk=model_id, exam=exam)
    except (Exam.DoesNotExist, ExamModel.DoesNotExist):
        page.issue_type = PageIssueType.QR_MISMATCH
        page.status = PageStatus.ORPHAN
        page.save(update_fields=["issue_type", "status", "updated_at"])
        return

    # Check if page_number exceeds expected profiles.
    expected_pages = model.page_profiles.count()
    if expected_pages > 0 and page_number > expected_pages:
        page.issue_type = PageIssueType.EXTRA_PAGE
        page.save(update_fields=["issue_type", "updated_at"])

    page.page_number = page_number

    if page_number == 1:
        # Portada → create new instance.
        _create_instance_from_cover(page, exam, model, recognition_results)
    else:
        # Subsequent page → find existing instance and attach.
        _attach_to_existing_instance(page, exam, model)


def _create_instance_from_cover(
    page: ExamPage,
    exam,
    model,
    recognition_results: dict,
) -> None:
    """Create a new ExamInstance from a cover page (page 1)."""
    expected_pages = model.page_profiles.count()

    with transaction.atomic():
        instance = ExamInstance.objects.create(
            organization=exam.organization,
            exam=exam,
            model=model,
            status=InstanceStatus.ASSEMBLING,
            expected_pages=expected_pages,
        )

        page.instance = instance
        page.save(update_fields=["instance", "page_number", "updated_at"])

    # Try student identification from recognition results.
    _try_identify_student(instance, recognition_results)

    # Check if single-page exam → auto-transition.
    check_assembling_complete(instance)

    logger.info(
        "Created instance %s for exam %s from cover page.",
        instance.pk,
        exam.pk,
    )


def _attach_to_existing_instance(page: ExamPage, exam, model) -> None:
    """Attach a non-cover page to an existing instance."""
    # Find the most recent ASSEMBLING instance for this exam+model.
    instance = (
        ExamInstance.unfiltered.filter(
            exam=exam,
            model=model,
            status=InstanceStatus.ASSEMBLING,
        )
        .order_by("-created_at")
        .first()
    )

    if instance:
        page.instance = instance
        page.save(update_fields=["instance", "page_number", "updated_at"])
        check_assembling_complete(instance)
    else:
        # No matching instance — page becomes orphan.
        page.status = PageStatus.ORPHAN
        page.organization = exam.organization
        page.save(update_fields=["status", "organization", "updated_at"])
        logger.warning(
            "No ASSEMBLING instance found for page %s (exam=%s, model=%s, page_num=%d).",
            page.pk,
            exam.pk,
            model.pk,
            page.page_number,
        )


def _assemble_by_proximity(page: ExamPage) -> None:
    """Fallback: attach page to the most recent instance by temporal proximity (RF-9.11)."""
    cutoff = datetime.now(tz=UTC) - timedelta(seconds=PROXIMITY_WINDOW_SECONDS)

    # Find the instance whose last page arrived most recently.
    candidate = (
        ExamInstance.unfiltered.filter(
            status=InstanceStatus.ASSEMBLING,
            pages__created_at__gte=cutoff,
        )
        .order_by("-pages__created_at")
        .first()
    )

    if candidate:
        page.instance = candidate
        page.status = PageStatus.ORPHAN
        page.issue_type = PageIssueType.ORPHAN_PAGE
        page.page_number = candidate.pages.count() + 1
        page.save()

        candidate.recalculate_issues()
        candidate.save(update_fields=["has_issues", "issue_types", "updated_at"])

        logger.info(
            "Attached orphan page %s to instance %s by proximity.",
            page.pk,
            candidate.pk,
        )
    else:
        # No candidate — leave as orphan.
        page.status = PageStatus.ORPHAN
        page.save(update_fields=["status", "updated_at"])
        logger.warning("No proximity candidate for orphan page %s.", page.pk)


def _try_identify_student(
    instance: ExamInstance,
    recognition_results: dict,
) -> None:
    """Try to identify the student from recognition results (RF-9.7)."""
    from apps.ingestion.services.matching import match_student

    zones = recognition_results.get("zones", [])
    if not zones:
        instance.add_issue(InstanceIssueType.STUDENT_NOT_IDENTIFIED)
        instance.save(update_fields=["has_issues", "issue_types", "updated_at"])
        return

    # Extract identification attributes.
    id_attrs = {}
    for zone_result in zones:
        attr = zone_result.get("attribute", "")
        value = zone_result.get("value")
        if attr in ("dni", "nia", "name") and value:
            id_attrs[attr] = value

    if not id_attrs:
        instance.add_issue(InstanceIssueType.STUDENT_NOT_IDENTIFIED)
        instance.save(update_fields=["has_issues", "issue_types", "updated_at"])
        return

    # Try matching.
    student, confidence = match_student(
        exam=instance.exam,
        attributes=id_attrs,
    )

    if student:
        # Check for duplicate.
        duplicate = (
            ExamInstance.unfiltered.filter(
                exam=instance.exam,
                student=student,
            )
            .exclude(pk=instance.pk)
            .exists()
        )

        if duplicate:
            instance.add_issue(InstanceIssueType.DUPLICATE_STUDENT)
            instance.save(update_fields=["has_issues", "issue_types", "updated_at"])
        else:
            instance.student = student
            instance.save(update_fields=["student", "has_issues", "issue_types", "updated_at"])
    else:
        instance.add_issue(InstanceIssueType.STUDENT_NOT_IDENTIFIED)
        instance.save(update_fields=["has_issues", "issue_types", "updated_at"])
