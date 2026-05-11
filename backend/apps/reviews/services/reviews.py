"""
Review business logic.

- Create review window with validation (RF-12.1)
- Submit review requests (RF-12.4)
- Resolve requests with auto-finalization (RF-12.7)
- Open/close window logic for Celery tasks (RF-12.2, RF-12.3)

References: RF-12.1 through RF-12.8
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from apps.grading.models.grading import AssignmentRule
from apps.instances.models.instances import ExamInstance, InstanceStatus
from apps.reviews.models.reviews import ExamReview, ReviewRequest, ReviewStatus

logger = logging.getLogger(__name__)


class ReviewServiceError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(detail or code)


def create_review(
    *,
    exam,
    start_date: datetime,
    end_date: datetime,
    notify_students: bool = True,
) -> ExamReview:
    """Create a review window for an exam (RF-12.1).

    Validates:
    - No existing review for this exam (v1.0 limit).
    - All instances are PUBLISHED.
    - start_date < end_date.
    """
    if hasattr(exam, "review"):
        raise ReviewServiceError(
            code="REVIEW_ALREADY_EXISTS",
            detail="This exam already has a review window (v1.0 limit).",
        )

    if start_date >= end_date:
        raise ReviewServiceError(
            code="INVALID_DATES",
            detail="start_date must be before end_date.",
        )

    # Check all instances are PUBLISHED.
    non_published = (
        ExamInstance.unfiltered.filter(
            exam=exam,
        )
        .exclude(
            status__in=[InstanceStatus.PUBLISHED, InstanceStatus.ARCHIVED],
        )
        .exists()
    )

    if non_published:
        raise ReviewServiceError(
            code="NOT_ALL_PUBLISHED",
            detail="All instances must be PUBLISHED before creating a review.",
        )

    return ExamReview.objects.create(
        exam=exam,
        start_date=start_date,
        end_date=end_date,
        status=ReviewStatus.SCHEDULED,
        notify_students=notify_students,
    )


def submit_review_requests(
    *,
    review: ExamReview,
    instance: ExamInstance,
    problems: list[dict],
    student,
) -> list[ReviewRequest]:
    """Submit review requests for specific problems (RF-12.4).

    Args:
        review: The ExamReview.
        instance: The student's instance.
        problems: List of {problem_id, message} dicts.
        student: The requesting student.

    Returns:
        List of created ReviewRequest objects.
    """
    if review.status != ReviewStatus.OPEN:
        raise ReviewServiceError(
            code="REVIEW_NOT_OPEN",
            detail="The review window is not currently open.",
        )

    if instance.student_id != student.pk:
        raise ReviewServiceError(
            code="NOT_INSTANCE_OWNER",
            detail="You can only request review of your own instance.",
        )

    if instance.status != InstanceStatus.IN_REVIEW:
        raise ReviewServiceError(
            code="INSTANCE_NOT_IN_REVIEW",
            detail="Instance must be in IN_REVIEW state.",
        )

    from apps.exams.models.exams import Problem

    created = []
    for item in problems:
        problem_id = item.get("problem_id")
        message = item.get("message", "")

        try:
            problem = Problem.objects.get(pk=problem_id)
        except Problem.DoesNotExist:
            continue  # Skip invalid problems.

        request, was_created = ReviewRequest.objects.get_or_create(
            review=review,
            instance=instance,
            problem=problem,
            defaults={
                "student_message": message,
                "resolved": False,
            },
        )
        if was_created:
            created.append(request)

    return created


def resolve_request(
    *,
    request: ReviewRequest,
    resolver,
    resolver_message: str = "",
) -> ReviewRequest:
    """Resolve a review request (RF-12.7).

    If all requests for the instance are resolved, transition to FINALIZED.
    """
    if request.resolved:
        raise ReviewServiceError(code="ALREADY_RESOLVED")

    request.resolved = True
    request.resolved_at = datetime.now(tz=UTC)
    request.resolver = resolver
    request.resolver_message = resolver_message
    request.save()

    # Check if all requests for this instance are resolved.
    _check_instance_finalization(request.instance, request.review)

    return request


def _check_instance_finalization(instance: ExamInstance, review: ExamReview) -> None:
    """If all review requests for an instance are resolved, finalize it."""
    pending = ReviewRequest.objects.filter(
        review=review,
        instance=instance,
        resolved=False,
    ).exists()

    if not pending:
        instance.status = InstanceStatus.FINALIZED
        instance.save(update_fields=["status", "updated_at"])

        # Notify the student (RF-12.8). Skipped silently if the instance
        # has no associated student (unlikely but possible after manual
        # cleanups by a coordinator).
        from apps.notifications.services.notifications import notify_review_result

        notify_review_result(instance)

        # Check if all instances are finalized → review COMPLETED.
        _check_review_completion(review)


def _check_review_completion(review: ExamReview) -> None:
    """If all requests in the review are resolved, mark COMPLETED."""
    pending = ReviewRequest.objects.filter(
        review=review,
        resolved=False,
    ).exists()

    if not pending:
        review.status = ReviewStatus.COMPLETED
        review.save(update_fields=["status", "updated_at"])


def open_review(review: ExamReview) -> int:
    """Open the review window (RF-12.2). Transition PUBLISHED → IN_REVIEW.

    Returns count of transitioned instances. Notifies students whose
    instances were transitioned, when the review was created with
    ``notify_students=True``.
    """
    affected = list(
        ExamInstance.unfiltered.filter(
            exam=review.exam,
            status=InstanceStatus.PUBLISHED,
        )
    )

    count = ExamInstance.unfiltered.filter(
        pk__in=[inst.pk for inst in affected],
    ).update(status=InstanceStatus.IN_REVIEW)

    review.status = ReviewStatus.OPEN
    review.save(update_fields=["status", "updated_at"])

    if review.notify_students:
        from apps.notifications.services.notifications import notify_review_opened

        students = [inst.student for inst in affected if inst.student]
        if students:
            notify_review_opened(review.exam, students)

    return count


def close_review(review: ExamReview) -> dict:
    """Close the review window (RF-12.3).

    - Instances without requests → FINALIZED.
    - Instances with pending requests → PENDING_REVIEW.
    - Correctors with at least one pending request receive a grouped
      summary notification (RF-12.5).

    Returns counts.
    """
    from django.contrib.auth import get_user_model

    from apps.grading.models.grading import AssignmentRule
    from apps.notifications.services.notifications import notify_review_requests_summary

    instances = ExamInstance.unfiltered.filter(
        exam=review.exam,
        status=InstanceStatus.IN_REVIEW,
    )

    finalized = 0
    pending_review = 0

    for instance in instances:
        has_requests = ReviewRequest.objects.filter(
            review=review,
            instance=instance,
            resolved=False,
        ).exists()

        if has_requests:
            instance.status = InstanceStatus.PENDING_REVIEW
            pending_review += 1
        else:
            instance.status = InstanceStatus.FINALIZED
            finalized += 1

        instance.save(update_fields=["status", "updated_at"])

    review.status = ReviewStatus.CLOSED
    review.save(update_fields=["status", "updated_at"])

    # Build a {corrector_id: count} grouping based on AssignmentRule
    # membership for the problems that have pending review requests.
    pending_requests = ReviewRequest.objects.filter(
        review=review,
        resolved=False,
    )
    if pending_requests.exists():
        rules = AssignmentRule.objects.filter(exam=review.exam)
        problem_to_correctors: dict[str, set[str]] = {}
        for rule in rules:
            for pid in rule.problems:
                problem_to_correctors.setdefault(str(pid), set()).update(
                    str(c) for c in rule.correctors
                )

        per_corrector: dict[str, int] = {}
        for req in pending_requests:
            for corr_id in problem_to_correctors.get(str(req.problem_id), set()):
                per_corrector[corr_id] = per_corrector.get(corr_id, 0) + 1

        if per_corrector:
            user_model = get_user_model()
            correctors = user_model.objects.filter(pk__in=per_corrector.keys())
            for corrector in correctors:
                notify_review_requests_summary(
                    corrector,
                    review.exam,
                    per_corrector[str(corrector.pk)],
                )

    return {"finalized": finalized, "pending_review": pending_review}


def get_pending_reviews_for_corrector(user, exam=None) -> list[dict]:
    """Return pending review requests assigned to the corrector via AssignmentRules.

    Each entry contains details of the review request and the associated instance/exam.
    """
    from apps.reviews.models.reviews import ReviewRequest

    rules = AssignmentRule.objects.filter(correctors__contains=[str(user.pk)])
    if exam:
        rules = rules.filter(exam=exam)

    pending_reviews: list[dict] = []
    for rule in rules:
        # Find pending, unresolved review requests whose problem_id is in rule.problems
        requests = (
            ReviewRequest.objects.filter(
                resolved=False,
                problem_id__in=rule.problems,
                instance__exam=rule.exam,
            )
            .select_related("instance__student", "problem", "review")
            .order_by("-created_at")
        )

        for req in requests:
            inst = req.instance
            # Check group/model filters if rule restricts them
            if rule.groups:
                if not inst.student:
                    continue
                try:
                    from apps.subjects.models import SubjectMembership

                    membership = SubjectMembership.unfiltered.get(
                        user=inst.student,
                        subject=rule.exam.subject,
                        is_active=True,
                    )
                    if str(membership.group_id) not in rule.groups:
                        continue
                except SubjectMembership.DoesNotExist:
                    continue
            if rule.models_filter and str(inst.model_id) not in rule.models_filter:
                continue

            pending_reviews.append(
                {
                    "review_request_id": str(req.pk),
                    "instance_id": str(inst.pk),
                    "exam_name": rule.exam.name,
                    "student_email": inst.student.email if inst.student else None,
                    "problem_name": req.problem.name,
                    "problem_id": str(req.problem_id),
                    "student_message": req.student_message,
                    "resolver_message": req.resolver_message,
                    "created_at": req.created_at.isoformat(),
                }
            )

    return pending_reviews
