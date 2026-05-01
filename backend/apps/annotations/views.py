"""
Annotation API views.

Endpoints:
    POST   /instances/{id}/annotations/        — Create annotation (RF-10.1)
    GET    /instances/{id}/annotations/        — List with filters (RF-10.4)
    PUT    /annotations/{id}/                  — Update annotation (RF-10.2)
    DELETE /annotations/{id}/                  — Delete annotation (RF-10.3)
    POST   /annotations/{id}/ocr-grade/        — OCR grade extraction (RF-9.12)
    POST   /annotations/{id}/ocr/              — OCR stylus to text (RF-9.13)

References: RF-10.1 through RF-10.7, RF-9.12, RF-9.13
"""

from __future__ import annotations

import logging

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.annotations.models import Annotation
from apps.annotations.serializers import (
    AnnotationResponseSerializer,
    CreateAnnotationSerializer,
    OCRGradeResponseSerializer,
    OCRStylusResponseSerializer,
    UpdateAnnotationSerializer,
)
from apps.annotations.services import (
    AnnotationServiceError,
    create_annotation,
    delete_annotation,
    ocr_grade_annotation,
    ocr_stylus_annotation,
    update_annotation,
)
from apps.audit.services import get_client_ip, log_event
from apps.instances.models import ExamInstance

logger = logging.getLogger(__name__)


class AnnotationListCreateView(APIView):
    """Create and list annotations for an instance (RF-10.1, RF-10.4)."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, JSONParser]

    @extend_schema(
        tags=["Annotations"],
        request=CreateAnnotationSerializer,
        responses={201: AnnotationResponseSerializer},
        summary="Create annotation (RF-10.1)",
    )
    def post(self, request: Request, instance_pk: str) -> Response:
        try:
            instance = ExamInstance.objects.select_related("model").get(pk=instance_pk)
        except ExamInstance.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        # Handle JSON body or multipart form.
        data = request.data.dict() if hasattr(request.data, "dict") else dict(request.data)

        serializer = CreateAnnotationSerializer(data=data)
        serializer.is_valid(raise_exception=True)

        # Handle audio file upload.
        audio_data = None
        if "file" in request.FILES:
            audio_data = request.FILES["file"].read()

        try:
            annotation = create_annotation(
                instance=instance,
                annotation_type=serializer.validated_data["annotation_type"],
                payload=serializer.validated_data.get("payload", ""),
                audio_data=audio_data,
                page_number=serializer.validated_data.get("page_number", 1),
                x=serializer.validated_data.get("x", 0.0),
                y=serializer.validated_data.get("y", 0.0),
                problem_id=str(serializer.validated_data["problem_id"])
                if serializer.validated_data.get("problem_id")
                else None,
                author=request.user,
                is_grade_annotation=serializer.validated_data.get("is_grade_annotation", False),
            )
        except AnnotationServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_400_BAD_REQUEST)

        log_event(
            event_type="ANNOTATION_CREATED",
            actor=request.user,
            organization=request.user.organization,
            entity=annotation,
            ip_address=get_client_ip(request),
            payload={"type": annotation.annotation_type, "page": annotation.page_number},
        )

        return Response(
            AnnotationResponseSerializer(annotation).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Annotations"],
        responses={200: AnnotationResponseSerializer(many=True)},
        summary="List annotations (RF-10.4)",
        parameters=[
            OpenApiParameter("page_number", int),
            OpenApiParameter("problem_id", str),
            OpenApiParameter("annotation_type", str, enum=["TEXT", "STYLUS", "AUDIO"]),
            OpenApiParameter("author_id", str),
        ],
    )
    def get(self, request: Request, instance_pk: str) -> Response:
        try:
            ExamInstance.objects.get(pk=instance_pk)
        except ExamInstance.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        queryset = (
            Annotation.objects.filter(instance_id=instance_pk)
            .select_related("author", "problem")
            .order_by("page_number", "y", "x")
        )

        # Apply filters.
        if pn := request.query_params.get("page_number"):
            queryset = queryset.filter(page_number=pn)
        if pid := request.query_params.get("problem_id"):
            queryset = queryset.filter(problem_id=pid)
        if at := request.query_params.get("annotation_type"):
            queryset = queryset.filter(annotation_type=at)
        if aid := request.query_params.get("author_id"):
            queryset = queryset.filter(author_id=aid)

        return Response(AnnotationResponseSerializer(queryset, many=True).data)


class AnnotationDetailView(APIView):
    """Update and delete annotations (RF-10.2, RF-10.3)."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, JSONParser]

    @extend_schema(
        tags=["Annotations"],
        request=UpdateAnnotationSerializer,
        responses={200: AnnotationResponseSerializer},
        summary="Update annotation (RF-10.2)",
    )
    def put(self, request: Request, annotation_pk: str) -> Response:
        try:
            annotation = Annotation.objects.select_related("author", "instance").get(
                pk=annotation_pk
            )
        except Annotation.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        serializer = UpdateAnnotationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        audio_data = None
        if "file" in request.FILES:
            audio_data = request.FILES["file"].read()

        try:
            annotation = update_annotation(
                annotation,
                payload=serializer.validated_data.get("payload"),
                audio_data=audio_data,
                author=request.user,
            )
        except AnnotationServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_403_FORBIDDEN)

        log_event(
            event_type="ANNOTATION_UPDATED",
            actor=request.user,
            organization=request.user.organization,
            entity=annotation,
            ip_address=get_client_ip(request),
        )

        return Response(AnnotationResponseSerializer(annotation).data)

    @extend_schema(
        tags=["Annotations"],
        summary="Delete annotation (RF-10.3)",
        responses={204: None},
    )
    def delete(self, request: Request, annotation_pk: str) -> Response:
        try:
            annotation = Annotation.objects.select_related("author").get(pk=annotation_pk)
        except Annotation.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        try:
            delete_annotation(annotation, actor=request.user)
        except AnnotationServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_403_FORBIDDEN)

        return Response(status=status.HTTP_204_NO_CONTENT)


class OCRGradeView(APIView):
    """Run OCR on an annotation to extract a grade (RF-9.12)."""

    permission_classes = [IsAuthenticated]
    serializer_class = OCRGradeResponseSerializer

    @extend_schema(
        tags=["Annotations"],
        request=None,
        responses={200: OCRGradeResponseSerializer},
        summary="OCR grade extraction (RF-9.12)",
    )
    def post(self, request: Request, annotation_pk: str) -> Response:
        try:
            annotation = Annotation.objects.select_related("instance", "problem", "author").get(
                pk=annotation_pk
            )
        except Annotation.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        try:
            result = ocr_grade_annotation(annotation)
        except AnnotationServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_400_BAD_REQUEST)

        return Response(OCRGradeResponseSerializer(result).data)


class OCRStylusView(APIView):
    """Run OCR on stylus strokes to convert to text (RF-9.13)."""

    permission_classes = [IsAuthenticated]
    serializer_class = OCRStylusResponseSerializer

    @extend_schema(
        tags=["Annotations"],
        request=None,
        responses={200: OCRStylusResponseSerializer},
        summary="OCR stylus to text (RF-9.13)",
    )
    def post(self, request: Request, annotation_pk: str) -> Response:
        try:
            annotation = Annotation.objects.get(pk=annotation_pk)
        except Annotation.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        try:
            result = ocr_stylus_annotation(annotation)
        except AnnotationServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_400_BAD_REQUEST)

        return Response(OCRStylusResponseSerializer(result).data)
