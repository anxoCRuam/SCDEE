"""
Maintenance Celery tasks (RF-15.3, RF-15.4).

- perform_auto_deletion: Delete exams from archived courses older
  than auto_delete_frequency_days. Preserves AuditLog.
- check_upcoming_deletions: Notify managers X days before deletion.

References: RF-15.3, RF-15.4, RF-16.1, RF-7.10, RF-6.3
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from celery import shared_task
from django.db import transaction

from apps.core.tasks.storage import cleanup_minio_orphans
from apps.exams.models.exams import Exam

logger = logging.getLogger(__name__)


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
    from apps.courses.models.courses import AcademicCourse, CourseStatus
    from apps.organizations.models.organization import Organization
    from apps.organizations.services.organization_config import get_org_config

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
        from apps.audit.services.auditlog import log_event

        log_event(
            event_type="AUTO_DELETION",
            payload=total_deleted,
        )

    logger.info("Auto-deletion complete: %s", total_deleted)
    return total_deleted


def _delete_course_data(course, org) -> dict:
    """Delete all exam data for a course. Preserves AuditLog."""

    result = {"exams": 0, "instances": 0, "files": 0}

    exams = Exam.unfiltered.filter(
        subject__course=course,
        organization=org,
    )

    for exam in exams:
        storage_refs: list[str] = []

        # Delete MinIO files for all instances.
        for instance in exam.instances.all():
            for page in instance.pages.all():
                if page.storage_ref:
                    storage_refs.append(page.storage_ref)

            # Delete annotation files.
            for annotation in instance.annotations.all():
                if annotation.storage_ref:
                    storage_refs.append(annotation.storage_ref)

            result["instances"] += 1

        # Delete model PDFs.
        for model in exam.models.all():
            if model.blank_pdf_ref:
                storage_refs.append(model.blank_pdf_ref)

        # Model PDFs attached to the exam
        for model in exam.models.all():
            if model.blank_pdf_ref:
                storage_refs.append(model.blank_pdf_ref)

        try:
            with transaction.atomic():
                exam.delete()
        except Exception:
            logger.exception("Failed to delete exam %s, skipping MinIO cleanup", exam.pk)
            continue

        if storage_refs:
            transaction.on_commit(lambda refs=storage_refs: cleanup_minio_orphans.delay(refs))
            result["files"] += len(storage_refs)

        result["exams"] += 1

    return result


@shared_task(queue="default")
def check_upcoming_deletions() -> dict:
    """Notify managers of upcoming auto-deletions (RF-15.4).

    Checks whether any org has courses that will be auto-deleted within
    ``delete_notice_days``. Sends at most one notification per org per
    notice window — the deduplication key (``notice_key``) is stored in
    the notification's metadata.
    """
    from django.contrib.auth import get_user_model

    from apps.courses.models.courses import AcademicCourse, CourseStatus
    from apps.notifications.models.notifications import Notification, NotificationType
    from apps.notifications.services.notifications import notify_auto_deletion_reminder
    from apps.organizations.models.organization import Organization
    from apps.organizations.services.organization_config import get_org_config

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
