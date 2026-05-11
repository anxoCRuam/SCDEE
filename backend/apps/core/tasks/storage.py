"""
Storage Celery tasks (RF-15.3, RF-15.4).

- cleanup_minio_orphans: Drain a list of MinIO refs after a DB delete
  has committed. Scheduled via ``transaction.on_commit`` from the
  service that performs the deletion so the MinIO deletion only runs
  if the DB transaction succeeds; Celery's retry handles partial
  MinIO failures so we never leave half-deleted state.

References: RF-15.3, RF-15.4, RF-16.1, RF-7.10, RF-6.3
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    queue="default",
    bind=True,
    max_retries=5,
    default_retry_delay=30,
    autoretry_for=(Exception,),
    retry_backoff=True,
)
def cleanup_minio_orphans(self, storage_refs: list[str]) -> dict:
    """Delete a batch of MinIO objects that no longer have DB owners.

    Called via ``transaction.on_commit`` after delete_instance,
    delete_exam or any other operation that removes rows whose
    binary payload lives in MinIO. If a single ref fails, the task
    retries the *entire batch* — ``delete_minio_object`` is itself
    idempotent (already-deleted objects do not raise) so re-running
    it is safe.
    """
    from apps.exams.services.storage import delete_minio_object

    failed: list[str] = []
    for ref in storage_refs:
        if not ref:
            continue
        try:
            delete_minio_object(ref)
        except Exception as exc:  # noqa: BLE001
            failed.append(ref)
            logger.warning("MinIO delete failed for %s: %s", ref, exc)

    if failed:
        # Trigger Celery retry on whatever subset still failed.
        raise RuntimeError(f"MinIO deletion failed for {len(failed)} objects")

    return {"deleted": len(storage_refs)}
