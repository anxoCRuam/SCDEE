"""
Maintenance Celery tasks (RF-15.3, RF-15.4).

- perform_auto_deletion: Delete exams from archived courses older
  than auto_delete_frequency_days. Preserves AuditLog.
- check_upcoming_deletions: Notify managers X days before deletion.
- cleanup_minio_orphans: Drain a list of MinIO refs after a DB delete
  has committed. Scheduled via ``transaction.on_commit`` from the
  service that performs the deletion so the MinIO deletion only runs
  if the DB transaction succeeds; Celery's retry handles partial
  MinIO failures so we never leave half-deleted state.

References: RF-15.3, RF-15.4, RF-16.1, RF-7.10, RF-6.3
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

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


@shared_task(queue="default")
def perform_auto_deletion() -> dict:
    """Delete exams from archived courses past retention (RF-15.3).

    For each organization:
    1. Read auto_delete_frequency_days from OrganizationConfig.
    2. Find archived courses older than that threshold.
    3. Delete exams, instances, pages, annotations, grades, and MinIO files.
    4. Never delete AuditLog entries.
    5. Log the operation to AuditLog.
    """
    from apps.courses.models import AcademicCourse, CourseStatus
    from apps.organizations.models import Organization
    from apps.organizations.services import get_org_config

    total_deleted = {"exams": 0, "instances": 0, "files": 0}

    for org in Organization.objects.filter(is_active=True):
        try:
            config = get_org_config(org)
            cutoff_days = config.auto_delete_frequency_days

            if cutoff_days <= 0:
                continue

            cutoff_date = datetime.now(tz=UTC) - timedelta(days=cutoff_days)

            old_courses = AcademicCourse.unfiltered.filter(
                organization=org,
                status=CourseStatus.ARCHIVED,
                updated_at__lt=cutoff_date,
            )

            for course in old_courses:
                result = _delete_course_data(course, org)
                total_deleted["exams"] += result["exams"]
                total_deleted["instances"] += result["instances"]
                total_deleted["files"] += result["files"]

        except Exception as exc:
            logger.error("Auto-deletion failed for org %s: %s", org.pk, exc)

    # Audit log for the bulk operation.
    if total_deleted["exams"] > 0:
        from apps.audit.services import log_event

        log_event(
            event_type="AUTO_DELETION",
            payload=total_deleted,
        )

    logger.info("Auto-deletion complete: %s", total_deleted)
    return total_deleted


def _delete_course_data(course, org) -> dict:
    """Delete all exam data for a course. Preserves AuditLog."""
    from apps.exams.models import Exam

    result = {"exams": 0, "instances": 0, "files": 0}

    exams = Exam.unfiltered.filter(
        subject__course=course,
        organization=org,
    )

    for exam in exams:
        # Delete MinIO files for all instances.
        for instance in exam.instances.all():
            for page in instance.pages.all():
                if page.storage_ref:
                    _safe_delete_file(page.storage_ref)
                    result["files"] += 1

            # Delete annotation files.
            for annotation in instance.annotations.all():
                if annotation.storage_ref:
                    _safe_delete_file(annotation.storage_ref)
                    result["files"] += 1

            result["instances"] += 1

        # Delete model PDFs.
        for model in exam.models.all():
            if model.blank_pdf_ref:
                _safe_delete_file(model.blank_pdf_ref)
                result["files"] += 1
            if model.instrumented_pdf_ref:
                _safe_delete_file(model.instrumented_pdf_ref)
                result["files"] += 1

        # Cascade delete handles DB records.
        exam.delete()
        result["exams"] += 1

    return result


def _safe_delete_file(storage_ref: str) -> None:
    """Delete a MinIO file, catching errors."""
    try:
        from apps.exams.services.storage import delete_minio_object

        delete_minio_object(storage_ref)
    except Exception as exc:
        logger.warning("Failed to delete %s: %s", storage_ref, exc)


@shared_task(queue="default")
def check_upcoming_deletions() -> dict:
    """Notify managers of upcoming auto-deletions (RF-15.4).

    Checks whether any org has courses that will be auto-deleted within
    ``delete_notice_days``. Sends at most one notification per org per
    notice window — the deduplication key (``notice_key``) is stored in
    the notification's metadata.
    """
    from django.contrib.auth import get_user_model

    from apps.courses.models import AcademicCourse, CourseStatus
    from apps.exams.models import Exam
    from apps.notifications.models import Notification, NotificationType
    from apps.notifications.services import notify_auto_deletion_reminder
    from apps.organizations.models import Organization
    from apps.organizations.services import get_org_config

    user_model = get_user_model()
    notified = 0

    for org in Organization.objects.filter(is_active=True):
        try:
            config = get_org_config(org)
            cutoff_days = config.auto_delete_frequency_days
            notice_days = config.delete_notice_days

            if cutoff_days <= 0:
                continue

            deletion_date = datetime.now(tz=UTC) + timedelta(days=notice_days)
            cutoff_date = deletion_date - timedelta(days=cutoff_days)

            candidates = AcademicCourse.unfiltered.filter(
                organization=org,
                status=CourseStatus.ARCHIVED,
                updated_at__lt=cutoff_date,
            )

            if not candidates.exists():
                continue

            notice_key = f"deletion_notice_{org.pk}_{cutoff_date.date()}"
            already_notified = Notification.objects.filter(
                notification_type=NotificationType.DELETION_REMINDER,
                metadata__notice_key=notice_key,
            ).exists()
            if already_notified:
                continue

            exam_count = Exam.unfiltered.filter(
                subject__course__in=candidates,
            ).count()

            managers = user_model.objects.filter(
                organization=org,
                is_staff=True,
                is_active=True,
            )

            for manager in managers:
                notify_auto_deletion_reminder(
                    manager,
                    days_until_deletion=notice_days,
                    exam_count=exam_count,
                    notice_key=notice_key,
                )
                notified += 1

        except Exception as exc:
            logger.error("Deletion notice check failed for org %s: %s", org.pk, exc)

    return {"managers_notified": notified}
