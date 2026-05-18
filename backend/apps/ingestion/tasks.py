"""
Celery tasks for the ingestion and recognition pipeline.

Tasks:
- recognize_page_task: Process a single page through the recognition pipeline.
- ingest_page_task: Receive a new page or PDF, normalize to PNG, store in MinIO,
  create ExamPage(s), enqueue recognition.
- detect_missing_pages: Periodic task to detect stalled ASSEMBLING instances.

All recognition tasks run on the 'recognition' queue.

References: RF-9.2, RF-9.4, RF-7.6
"""

from __future__ import annotations

import base64
import io
import logging
from datetime import UTC, datetime, timedelta

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)


@shared_task(
    queue="recognition",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def recognize_page_task(self, page_id: str) -> dict:
    """Run the recognition dispatcher on a single ExamPage."""
    from django.db import transaction

    from apps.instances.models.instances import ExamPage, PageStatus

    with transaction.atomic():
        try:
            page = ExamPage.objects.select_for_update().get(pk=page_id)
        except ExamPage.DoesNotExist:
            logger.error("ExamPage %s not found.", page_id)
            return {"error": "PAGE_NOT_FOUND"}

        if page.status != PageStatus.PENDING_RECOGNITION:
            logger.info(
                "Skipping recognize_page_task for %s: already in status %s.",
                page_id,
                page.status,
            )
            return {"page_id": page_id, "status": "skipped", "reason": "ALREADY_PROCESSED"}

        try:
            from apps.ingestion.services.dispatcher import recognize_page

            results = recognize_page(page)
            return {"page_id": page_id, "status": "completed", "results": str(results)[:500]}

        except Exception as exc:
            logger.error("Recognition failed for page %s: %s", page_id, exc)
            raise self.retry(exc=exc) from exc


@shared_task(
    queue="recognition",
    bind=True,
    max_retries=2,
    default_retry_delay=10,
)
def ingest_page_task(
    self,
    file_data_b64: str,
    filename: str,
    organization_id: str | None = None,
    batch_id: str | None = None,
) -> dict:
    """Ingest a new page or PDF: normalize to PNG pages, store in MinIO,
    create one ExamPage per page, and enqueue recognition for each.

    If ``batch_id`` is provided, all created ExamPages are linked to that
    IngestionBatch. Otherwise a new batch is created automatically.
    """
    from apps.exams.services.storage import upload_to_minio
    from apps.ingestion.models.ingestion import IngestionBatch
    from apps.instances.models.instances import ExamPage, PageStatus

    try:
        file_data = base64.b64decode(file_data_b64)
    except Exception:
        return {"error": "INVALID_DATA"}

    max_size_mb = getattr(settings, "INGESTION_MAX_PAGE_SIZE_MB", 50)
    if len(file_data) > max_size_mb * 1024 * 1024:
        return {"error": "FILE_TOO_LARGE", "max_mb": max_size_mb}

    # ── Normalize: split PDF into pages, convert everything to PNG ──
    normalized_pages = _normalize_to_png_pages(file_data, filename)

    # ── Create or reuse IngestionBatch ───────────────────────────
    if batch_id:
        try:
            batch = IngestionBatch.objects.get(pk=batch_id)
            # Update total_pages in case we now know the exact count
            if batch.total_pages != len(normalized_pages):
                batch.total_pages = len(normalized_pages)
                batch.save(update_fields=["total_pages", "updated_at"])
        except IngestionBatch.DoesNotExist:
            batch = IngestionBatch.objects.create(
                source="watcher",  # default; caller can set via other means
                total_pages=len(normalized_pages),
            )
            batch_id = str(batch.id)
    else:
        batch = IngestionBatch.objects.create(
            source="watcher",
            total_pages=len(normalized_pages),
        )
        batch_id = str(batch.id)

    created_pages: list[dict] = []
    timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")

    for page_index, png_bytes in enumerate(normalized_pages):
        if len(normalized_pages) > 1:
            page_filename = f"{filename}_p{page_index + 1}.png"
        else:
            page_filename = f"{filename}.png"

        storage_key = f"pending/ingestion/{timestamp}_{page_filename}"

        try:
            upload_to_minio(key=storage_key, data=png_bytes, content_type="image/png")
        except Exception as exc:
            logger.error("MinIO upload failed for page %d: %s", page_index, exc)
            raise self.retry(exc=exc) from exc

        page = ExamPage.objects.create(
            instance=None,
            page_number=0,
            storage_ref=storage_key,
            status=PageStatus.PENDING_RECOGNITION,
            organization_id=organization_id,
            batch_id=batch_id,
        )

        recognize_page_task.delay(str(page.pk))

        created_pages.append(
            {
                "page_id": str(page.pk),
                "storage_ref": storage_key,
                "page_index": page_index + 1,
                "status": "queued",
            }
        )

    return {
        "filename": filename,
        "batch_id": batch_id,
        "total_pages": len(normalized_pages),
        "pages": created_pages,
    }


def _normalize_to_png_pages(file_data: bytes, filename: str) -> list[bytes]:
    """Convert any supported input to a list of PNG page images."""
    if file_data.startswith(b"%PDF"):
        return _pdf_to_png_pages(file_data)
    return [_raster_to_png(file_data)]


def _pdf_to_png_pages(pdf_bytes: bytes) -> list[bytes]:
    """Render each page of a PDF to a PNG at 200 DPI."""
    from pdf2image import convert_from_bytes

    images = convert_from_bytes(pdf_bytes, dpi=200)
    result: list[bytes] = []
    for image in images:
        out = io.BytesIO()
        image.save(out, format="PNG")
        result.append(out.getvalue())

    logger.info(
        "PDF converted: %d pages → %d PNG images.",
        len(images),
        len(result),
    )
    return result


def _raster_to_png(image_bytes: bytes) -> bytes:
    """Ensure a raster image is in PNG format."""
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return image_bytes
    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


@shared_task(queue="default")
def detect_missing_pages() -> dict:
    """Periodic task: flag stalled ASSEMBLING instances and notify coordinators."""
    from apps.instances.models.instances import (
        ExamInstance,
        InstanceIssueType,
        InstanceStatus,
    )
    from apps.notifications.services.notifications import notify_ingestion_issue

    timeout_minutes = getattr(settings, "INGESTION_MISSING_PAGE_TIMEOUT_MINUTES", 10)
    cutoff = datetime.now(tz=UTC) - timedelta(minutes=timeout_minutes)

    stalled = ExamInstance.unfiltered.filter(
        status=InstanceStatus.ASSEMBLING,
        created_at__lt=cutoff,
    ).exclude(
        issue_types__contains=[InstanceIssueType.MISSING_PAGE],
    )

    affected_exams: dict[str, int] = {}
    count = 0
    for instance in stalled:
        instance.add_issue(InstanceIssueType.MISSING_PAGE)
        instance.save(update_fields=["has_issues", "issue_types", "updated_at"])
        count += 1
        affected_exams[str(instance.exam_id)] = affected_exams.get(str(instance.exam_id), 0) + 1
        logger.info(
            "Marked instance %s as MISSING_PAGE (stalled since %s).",
            instance.pk,
            instance.created_at,
        )

    if affected_exams:
        from apps.exams.models.exams import Exam

        for exam_id, missing_count in affected_exams.items():
            try:
                exam = Exam.unfiltered.select_related("subject__coordinator").get(pk=exam_id)
            except Exam.DoesNotExist:
                continue
            coordinator = exam.subject.coordinator
            if coordinator is None:
                continue
            notify_ingestion_issue(
                coordinator,
                exam,
                {
                    "missing_pages": missing_count,
                    "detail": (
                        f"{missing_count} instance(s) marked as MISSING_PAGE "
                        f"after the {timeout_minutes}-minute timeout."
                    ),
                },
            )

    return {"stalled_instances": count, "exams_notified": len(affected_exams)}


@shared_task(queue="default")
def assemble_exam_pages(exam_id: str) -> dict:
    """Ensambla páginas de un examen tras la ventana de espera."""
    from apps.ingestion.services.assembler import assemble_exam

    return assemble_exam(exam_id)
