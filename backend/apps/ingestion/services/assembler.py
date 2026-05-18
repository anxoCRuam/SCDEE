# apps/ingestion/services/assembler.py
"""Exam assembly: groups recognised pages into lots, matches each lot to a
convoked student, and persists `ExamInstance` records.

Public API:
    schedule_assembly(page)
        Queue a deferred assembly run for the exam this page belongs to.

    assemble_exam(exam_id) -> dict
        Perform the assembly synchronously: load all pending pages of the
        exam, group them into lots, solve the lot ↔ student assignment
        globally with the Hungarian algorithm, and persist the result.

The scoring and matching are delegated to `scoring.py` and `matching.py`,
which contain no Django imports. This module only orchestrates I/O
(loading pages, decrypting DNIs, creating instances, attaching pages,
raising incidences).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from django.conf import settings
from django.db import transaction

from apps.accounts.services.user_service import get_decrypted_dni
from apps.exams.models.exams import Exam, ExamConvocation
from apps.ingestion.services.matching import (
    LotCandidate,
    StudentCandidate,
    assign_lots_to_students,
)
from apps.ingestion.services.scoring import DEFAULT_STRATEGY, ScoringStrategy
from apps.instances.models.instances import (
    ExamInstance,
    ExamPage,
    InstanceIssueType,
    InstanceStatus,
    PageStatus,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def schedule_assembly(page: ExamPage) -> None:
    """Schedule deferred assembly for the exam this page belongs to.

    Uses a Celery task with a fixed countdown so that bursts of pages
    arriving close together are processed in a single assembly run. The
    task is idempotent: re-scheduling it costs nothing when no pages
    remain pending.
    """
    from apps.ingestion.tasks import assemble_exam_pages

    qr_payload = page.recognized_data.get("qr_payload")
    if not qr_payload:
        return
    exam_id = qr_payload.get("exam_id")
    if not exam_id:
        return

    countdown = getattr(settings, "ASSEMBLY_WINDOW_SECONDS", 180)  # 3 min default
    assemble_exam_pages.apply_async((exam_id,), countdown=countdown)


def assemble_exam(exam_id: str) -> dict[str, Any]:
    """Assemble every pending page of an exam into `ExamInstance` records.

    Algorithm:
        1. Collect every page of this exam that is RECOGNIZED and not yet
           attached to an instance.
        2. Group them into lots. Pages sharing a `batch_id` come from
           the same physical exam (one ingested PDF or one manual upload)
           and therefore belong to the same student a priori. Pages with
           no batch become single-page lots.
        3. Score each (lot, convoked student) pair using the OCR-aware
           similarity in `scoring.py`.
        4. Resolve the assignment globally with the Hungarian algorithm
           (`matching.py`): every lot is matched to at most one student
           and every student to at most one lot.
        5. For each matched lot, create or reuse the student's
           ExamInstance and attach the pages. Lots whose best match
           scores below `ASSEMBLY_MATCH_THRESHOLD` produce an
           unidentified instance with `STUDENT_NOT_IDENTIFIED`.

    Args:
        exam_id: primary key (UUID string) of the `Exam` to assemble.

    Returns:
        Summary dict with status and counters.
    """
    pending_pages = list(_load_pending_pages(exam_id))
    if not pending_pages:
        return {"status": "no_pages"}

    exam = Exam.objects.get(pk=exam_id)
    convoked = _get_convoked_students(exam)
    if not convoked:
        return {"status": "no_students", "pages": len(pending_pages)}

    lots = _group_pages_into_lots(pending_pages)
    strategy = _get_strategy()
    threshold = getattr(settings, "ASSEMBLY_MATCH_THRESHOLD", 0.05)

    lot_candidates = [_build_lot_candidate(key, pgs) for key, pgs in lots.items()]
    student_candidates = [_build_student_candidate(s) for s in convoked]
    convoked_by_id = {str(s.pk): s for s in convoked}

    assignment = assign_lots_to_students(
        lots=lot_candidates,
        students=student_candidates,
        strategy=strategy,
        threshold=threshold,
    )

    created_instances = 0
    assigned_pages = 0
    unassigned_lots = 0

    with transaction.atomic():
        for lot_key, lot_pages in lots.items():
            result = assignment[lot_key]
            student = (
                convoked_by_id.get(str(result.student_id))
                if result.student_id is not None
                else None
            )
            _persist_lot(exam=exam, student=student, lot_pages=lot_pages)
            created_instances += 1
            assigned_pages += len(lot_pages)
            if student is None:
                unassigned_lots += 1

    logger.info(
        "Assembled exam %s: %d instances from %d pages (%d unassigned)",
        exam_id,
        created_instances,
        assigned_pages,
        unassigned_lots,
    )
    return {
        "status": "ok",
        "instances": created_instances,
        "pages": assigned_pages,
        "unassigned": unassigned_lots,
    }


# ---------------------------------------------------------------------------
# Page collection and grouping
# ---------------------------------------------------------------------------


def _load_pending_pages(exam_id: str) -> Iterable[ExamPage]:
    """Pages of this exam ready to be assembled but not yet attached."""
    return ExamPage.objects.filter(
        instance__isnull=True,
        recognized_data__qr_payload__exam_id=exam_id,
        status=PageStatus.RECOGNIZED,
    ).select_related("batch")


def _group_pages_into_lots(pages: Iterable[ExamPage]) -> dict[Any, list[ExamPage]]:
    """Group pages by `batch_id`; pages without a batch become single-page lots.

    Pages sharing a `batch_id` come from the same physical ingestion
    unit (one multi-page PDF, one manual upload). The system already
    knows they belong to one exam and therefore to one student, so we
    keep them together. Treating them as a single lot also improves
    matching accuracy because the OCR evidence from all pages can be
    pooled when scoring the lot against each candidate student.
    """
    lots: dict[Any, list[ExamPage]] = defaultdict(list)
    for page in pages:
        key = page.batch_id or f"single_{page.pk}"
        lots[key].append(page)
    return dict(lots)


def _get_convoked_students(exam: Exam) -> list:
    """The list of users convoked to take this exam."""
    convs = ExamConvocation.objects.filter(exam=exam).select_related("student")
    return [c.student for c in convs]


# ---------------------------------------------------------------------------
# Strategy selection
# ---------------------------------------------------------------------------


def _get_strategy() -> ScoringStrategy:
    """Build the active scoring strategy, optionally overriding the weights.

    Reads `settings.SCORING_WEIGHTS` if present (a dict with keys
    'name', 'nia', 'dni') and uses it to override the default weights.
    The similarity functions themselves are not configurable from
    settings: the OCR-aware similarity is the only one we know to be
    robust across the dataset (see `tests/quality/evaluate_matching.py`).
    """
    override = getattr(settings, "SCORING_WEIGHTS", None)
    if not override:
        return DEFAULT_STRATEGY
    weights = (
        float(override.get("name", DEFAULT_STRATEGY.weights[0])),
        float(override.get("nia", DEFAULT_STRATEGY.weights[1])),
        float(override.get("dni", DEFAULT_STRATEGY.weights[2])),
    )
    return ScoringStrategy(
        name_scorer=DEFAULT_STRATEGY.name_scorer,
        nia_scorer=DEFAULT_STRATEGY.nia_scorer,
        dni_scorer=DEFAULT_STRATEGY.dni_scorer,
        weights=weights,
    )


# ---------------------------------------------------------------------------
# DTO construction
# ---------------------------------------------------------------------------


def _build_lot_candidate(key: Any, pages: list[ExamPage]) -> LotCandidate:
    """Aggregate OCR values from every page of the lot, per attribute.

    Multi-page lots may have multiple OCR values for the same attribute
    (e.g. several pages with a 'name' zone). They are all kept; the
    scoring strategy picks the highest-similarity one when scoring
    against each candidate student.
    """
    values: dict[str, list[str]] = {"name": [], "nia": [], "dni": []}
    for page in pages:
        for zone in page.recognized_data.get("zones", []):
            attr = zone.get("attribute", "")
            value = (zone.get("value") or "").strip()
            if attr in values and value:
                values[attr].append(value)
    return LotCandidate(key=key, ocr_values=values)


def _build_student_candidate(student) -> StudentCandidate:
    """Build a `StudentCandidate`, decrypting the DNI exactly once."""
    decrypted = get_decrypted_dni(student) or ""
    full_name = f"{student.first_name} {student.last_name}".strip()
    return StudentCandidate(
        student_id=str(student.pk),
        name=full_name,
        nia=student.nia or "",
        dni=decrypted,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _persist_lot(
    *,
    exam: Exam,
    student,  # User or None
    lot_pages: list[ExamPage],
) -> ExamInstance:
    """Create or extend the `ExamInstance` for a lot and attach its pages.

    Behaviour:
        - If `student` is provided: use `get_or_create` to either reuse
          an existing instance for this (exam, student) or create a new
          one. Existing instances are extended with the new pages.
        - If `student` is None (no convoked student matched above the
          threshold): always create a fresh unidentified instance and
          mark it with the `STUDENT_NOT_IDENTIFIED` issue. The model
          allows multiple unidentified instances per exam.
    """
    defaults = {
        "model_id": _extract_model_id(lot_pages),
        "status": InstanceStatus.ASSEMBLING,
        "expected_pages": _expected_pages_for(exam, lot_pages),
    }

    if student is None:
        instance = ExamInstance.objects.create(
            organization=exam.organization,
            exam=exam,
            student=None,
            **defaults,
        )
        instance.add_issue(InstanceIssueType.STUDENT_NOT_IDENTIFIED)
        instance.save(update_fields=["issue_types", "has_issues"])
    else:
        instance, _ = ExamInstance.objects.get_or_create(
            exam=exam,
            student=student,
            defaults={"organization": exam.organization, **defaults},
        )

    for page in lot_pages:
        page.instance = instance
        page.page_number = page.recognized_data["qr_payload"]["page_number"]
        page.save(update_fields=["instance", "page_number"])

    _check_assembling_complete(instance)
    return instance


def _expected_pages_for(exam: Exam, lot_pages: list[ExamPage]) -> int:
    """Determine how many pages this instance should have when complete.

    Pulled from the first `ExamModel`'s `PageProfile` count, with the
    actual lot size as a fallback when no model is configured. Matches
    the behaviour of the previous assembler.
    """
    first_model = exam.models.first()
    if first_model:
        count = first_model.page_profiles.count()
        if count:
            return count
    return len(lot_pages)


def _extract_model_id(pages: list[ExamPage]) -> Any:
    """Extract the model UUID from the first page that carries one in its QR."""
    for page in pages:
        qr = page.recognized_data.get("qr_payload")
        if qr and qr.get("model_id"):
            return qr["model_id"]
    return None


def _check_assembling_complete(instance: ExamInstance) -> None:
    """Transition the instance to RECEIVED if all expected pages have arrived.

    Delegates to the instance service; imported lazily to avoid a
    circular import (instance_service may import from this module via
    signals or other glue).
    """
    from apps.instances.services.instance_service import check_assembling_complete

    check_assembling_complete(instance)
