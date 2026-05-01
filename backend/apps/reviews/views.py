"""
Review API views.

Endpoints:
    POST   /exams/{id}/review/                   — Create review window (RF-12.1)
    GET    /exams/{id}/review/                   — Get review status
    POST   /instances/{id}/review-requests/       — Submit requests (RF-12.4)
    GET    /instances/{id}/review-requests/       — List requests
    PATCH  /review-requests/{id}/resolve/         — Resolve request (RF-12.7)

References: RF-12.1 through RF-12.8
"""

from __future__ import annotations

import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import get_client_ip, log_event
from apps.reviews.models import ExamReview, ReviewRequest
from apps.reviews.serializers import (
    CreateReviewSerializer,
    ResolveRequestSerializer,
    ReviewRequestResponseSerializer,
    ReviewResponseSerializer,
    SubmitReviewRequestSerializer,
)
from apps.reviews.services import (
    ReviewServiceError,
    create_review,
    resolve_request,
    submit_review_requests,
)

logger = logging.getLogger(__name__)


class ReviewCreateView(APIView):
    """Create and retrieve the review window for an exam (RF-12.1)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Reviews"],
        request=CreateReviewSerializer,
        responses={201: ReviewResponseSerializer},
        summary="Create review window (RF-12.1)",
    )
    def post(self, request: Request, exam_pk: str) -> Response:
        from apps.exams.models import Exam

        try:
            exam = Exam.objects.get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        serializer = CreateReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            review = create_review(
                exam=exam,
                start_date=serializer.validated_data["start_date"],
                end_date=serializer.validated_data["end_date"],
                notify_students=serializer.validated_data.get("notify_students", True),
            )
        except ReviewServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        log_event(
            event_type="REVIEW_CREATED",
            actor=request.user,
            organization=request.user.organization,
            entity=review,
            ip_address=get_client_ip(request),
            payload={"exam_name": exam.name},
        )

        review.request_count = 0
        return Response(ReviewResponseSerializer(review).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=["Reviews"],
        responses={200: ReviewResponseSerializer},
        summary="Get review status",
    )
    def get(self, request: Request, exam_pk: str) -> Response:
        try:
            review = ExamReview.objects.get(exam_id=exam_pk)
        except ExamReview.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        review.request_count = review.requests.count()
        return Response(ReviewResponseSerializer(review).data)


class ReviewRequestSubmitView(APIView):
    """Submit and list review requests for an instance (RF-12.4)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Reviews"],
        request=SubmitReviewRequestSerializer,
        responses={201: ReviewRequestResponseSerializer(many=True)},
        summary="Submit review requests (RF-12.4)",
    )
    def post(self, request: Request, instance_pk: str) -> Response:
        from apps.instances.models import ExamInstance

        try:
            instance = ExamInstance.objects.select_related("exam").get(pk=instance_pk)
        except ExamInstance.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        try:
            review = ExamReview.objects.get(exam=instance.exam)
        except ExamReview.DoesNotExist:
            return Response(
                {"error_code": "NO_REVIEW_WINDOW"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = SubmitReviewRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            requests = submit_review_requests(
                review=review,
                instance=instance,
                problems=serializer.validated_data["problems"],
                student=request.user,
            )
        except ReviewServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        log_event(
            event_type="REVIEW_REQUEST_SUBMITTED",
            actor=request.user,
            organization=request.user.organization,
            entity=instance,
            ip_address=get_client_ip(request),
            payload={"problem_count": len(requests)},
        )

        return Response(
            ReviewRequestResponseSerializer(requests, many=True).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Reviews"],
        responses={200: ReviewRequestResponseSerializer(many=True)},
        summary="List review requests for instance",
    )
    def get(self, request: Request, instance_pk: str) -> Response:
        requests = (
            ReviewRequest.objects.filter(instance_id=instance_pk)
            .select_related("problem")
            .order_by("-created_at")
        )
        return Response(ReviewRequestResponseSerializer(requests, many=True).data)


class ReviewRequestResolveView(APIView):
    """Resolve a review request (RF-12.7)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Reviews"],
        request=ResolveRequestSerializer,
        responses={200: ReviewRequestResponseSerializer},
        summary="Resolve review request (RF-12.7)",
    )
    def patch(self, request: Request, request_pk: str) -> Response:
        try:
            review_request = ReviewRequest.objects.select_related(
                "instance", "review", "problem"
            ).get(pk=request_pk)
        except ReviewRequest.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        serializer = ResolveRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            review_request = resolve_request(
                request=review_request,
                resolver=request.user,
                resolver_message=serializer.validated_data.get("resolver_message", ""),
            )
        except ReviewServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        log_event(
            event_type="REVIEW_REQUEST_RESOLVED",
            actor=request.user,
            organization=request.user.organization,
            entity=review_request,
            ip_address=get_client_ip(request),
            payload={"problem": review_request.problem.name},
        )

        return Response(ReviewRequestResponseSerializer(review_request).data)
