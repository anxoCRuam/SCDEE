"""
Celery tasks for automated review window management.

- process_review_openings: Opens review windows at start_date.
- process_review_closings: Closes review windows at end_date.

Both tasks run periodically via Celery Beat (every minute).

References: RF-12.2, RF-12.3
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(queue="default")
def process_review_openings() -> dict:
    """Open scheduled reviews whose start_date has passed (RF-12.2)."""
    from apps.reviews.models.reviews import ExamReview, ReviewStatus
    from apps.reviews.services.reviews import open_review

    now = datetime.now(tz=UTC)
    scheduled = ExamReview.objects.filter(
        status=ReviewStatus.SCHEDULED,
        start_date__lte=now,
    )

    opened = 0
    for review in scheduled:
        try:
            count = open_review(review)
            logger.info(
                "Opened review for exam %s — %d instances transitioned.",
                review.exam_id,
                count,
            )
            opened += 1
        except Exception as exc:
            logger.error("Failed to open review %s: %s", review.pk, exc)

    return {"opened": opened}


@shared_task(queue="default")
def process_review_closings() -> dict:
    """Close open reviews whose end_date has passed (RF-12.3)."""
    from apps.reviews.models.reviews import ExamReview, ReviewStatus
    from apps.reviews.services.reviews import close_review

    now = datetime.now(tz=UTC)
    open_reviews = ExamReview.objects.filter(
        status=ReviewStatus.OPEN,
        end_date__lte=now,
    )

    closed = 0
    for review in open_reviews:
        try:
            result = close_review(review)
            logger.info(
                "Closed review for exam %s — finalized=%d, pending=%d.",
                review.exam_id,
                result["finalized"],
                result["pending_review"],
            )
            closed += 1
        except Exception as exc:
            logger.error("Failed to close review %s: %s", review.pk, exc)

    return {"closed": closed}
