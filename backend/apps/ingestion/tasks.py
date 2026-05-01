"""
Celery tasks for the ingestion and recognition pipeline.

Tasks:
- recognize_page_task: Process a single page through the recognition pipeline.
- ingest_page_task: Receive a new page, store in MinIO, enqueue recognition.
- detect_missing_pages: Periodic task to detect stalled ASSEMBLING instances.

All recognition tasks run on the 'recognition' queue.

References: RF-9.2, RF-9.4, RF-7.6
"""

from __future__ import annotations

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
    """Run the recognition dispatcher on a single ExamPage.

    This is the main recognition task, enqueued after a page is ingested
    and stored in MinIO. Idempotent: ``SELECT ... FOR UPDATE`` claims the
    row before processing and the status check (``PENDING_RECOGNITION``)
    causes a no-op if another worker already processed this page —
    important because Celery retries and re-deliveries can cause the
    same task to fire more than once.
    """
    from django.db import transaction

    from apps.instances.models import ExamPage, PageStatus

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
) -> dict:
    """Ingest a new page: validate, store in MinIO, create ExamPage, enqueue recognition.

    ``organization_id`` is optional. The primary organisation discovery
    channel is the QR code — populated later by the recognition dispatcher.
    Callers that already know the org (e.g. a manager-initiated manual
    ingestion via the API) can supply it here so the page row carries the
    org from the start.

    Args:
        file_data_b64: Base64-encoded image/PDF bytes.
        filename: Original filename for logging.
        organization_id: Optional org PK (UUID string).
    """
    import base64
    from datetime import UTC, datetime

    from apps.exams.services.storage import upload_to_minio
    from apps.instances.models import ExamPage, PageStatus

    try:
        file_data = base64.b64decode(file_data_b64)
    except Exception:
        return {"error": "INVALID_DATA"}

    max_size_mb = getattr(settings, "INGESTION_MAX_PAGE_SIZE_MB", 50)
    if len(file_data) > max_size_mb * 1024 * 1024:
        return {"error": "FILE_TOO_LARGE", "max_mb": max_size_mb}

    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S%f")
    storage_key = f"pending/ingestion/{timestamp}_{filename}"

    try:
        upload_to_minio(key=storage_key, data=file_data, content_type="image/png")
    except Exception as exc:
        logger.error("MinIO upload failed: %s", exc)
        raise self.retry(exc=exc) from exc

    page = ExamPage.objects.create(
        instance=None,
        page_number=0,
        storage_ref=storage_key,
        status=PageStatus.PENDING_RECOGNITION,
        organization_id=organization_id,
    )

    recognize_page_task.delay(str(page.pk))

    return {"page_id": str(page.pk), "storage_ref": storage_key, "status": "queued"}


@shared_task(queue="default")
def detect_missing_pages() -> dict:
    """Periodic task: flag stalled ASSEMBLING instances and notify coordinators.

    Runs every few minutes. If an instance has been in ASSEMBLING for
    longer than the configured timeout, marks it with ``MISSING_PAGE``
    and notifies the coordinator of the affected exam (RF-7.6, RF-9.11,
    RF-13.7). Multiple newly-marked instances of the same exam yield a
    single grouped notification per coordinator per run.
    """
    from apps.instances.models import (
        ExamInstance,
        InstanceIssueType,
        InstanceStatus,
    )
    from apps.notifications.services import notify_ingestion_issue

    timeout_minutes = getattr(settings, "INGESTION_MISSING_PAGE_TIMEOUT_MINUTES", 10)
    cutoff = datetime.now(tz=UTC) - timedelta(minutes=timeout_minutes)

    stalled = ExamInstance.unfiltered.filter(
        status=InstanceStatus.ASSEMBLING,
        created_at__lt=cutoff,
    ).exclude(
        issue_types__contains=[InstanceIssueType.MISSING_PAGE],
    )

    affected_exams: dict[str, int] = {}  # exam_id → count
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
        from apps.exams.models import Exam

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
