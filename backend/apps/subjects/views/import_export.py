"""
Subject import/export views.

POST /api/v1/subjects/import/ — Bulk import subjects from JSON (RF-4.2)
GET  /api/v1/subjects/export/ — Export subjects to JSON or CSV (RF-4.10)

References: RF-4.2, RF-4.10
"""

from __future__ import annotations

import json
import logging

from django.http import HttpResponse
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models.permissions import IsOrgManager
from apps.audit.services.auditlog import DATA_EXPORTED, DATA_IMPORTED, get_client_ip, log_event
from apps.subjects.models.subjects import Subject
from apps.subjects.serializers.import_export import (
    IMPORT_EXPORT_EXAMPLE,
    SubjectFileUploadSerializer,
    SubjectImportExportItemSerializer,
    SubjectImportResultSerializer,
    SubjectRawDataSerializer,
)
from apps.subjects.services.import_export import (
    export_subjects,
    import_subjects,
    parse_csv_to_subject_list,
)

logger = logging.getLogger(__name__)


class SubjectImportView(APIView):
    """Bulk import subjects from JSON (RF-4.2). Manager only."""

    permission_classes = [IsOrgManager]
    parser_classes = [MultiPartParser, JSONParser]

    @extend_schema(
        tags=["Subjects"],
        summary="Bulk import subjects from JSON or CSV",
        request={
            "multipart/form-data": SubjectFileUploadSerializer,
            "application/json": SubjectRawDataSerializer,
        },
        responses={200: SubjectImportResultSerializer},
    )
    def post(self, request: Request) -> Response:
        # Determinar fuente de datos
        if "file" in request.FILES:
            file = request.FILES["file"]
            file_name = file.name.lower()
            content = file.read().decode("utf-8-sig")
            if file_name.endswith(".csv"):
                data = parse_csv_to_subject_list(content)
            else:
                try:
                    data = json.loads(content)
                except json.JSONDecodeError as exc:
                    return Response(
                        {"error_code": "INVALID_JSON", "detail": str(exc)},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
        elif isinstance(request.data, list):
            # Raw JSON array
            data = request.data
        else:
            return Response(
                {"error_code": "NO_DATA_PROVIDED"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not isinstance(data, list) or not data:
            return Response(
                {"error_code": "EMPTY_OR_INVALID_DATA"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = import_subjects(organization=request.user.organization, data=data)

        log_event(
            event_type=DATA_IMPORTED,
            actor=request.user,
            organization=request.user.organization,
            ip_address=get_client_ip(request),
            payload={
                "entity_type": "Subject",
                "created": result.created,
                "errors": result.total_errors,
            },
        )
        return Response(result.to_dict(), status=status.HTTP_200_OK)


class SubjectExportView(APIView):
    """Export subjects to JSON or CSV (RF-4.10). Manager only."""

    permission_classes = [IsOrgManager]

    @extend_schema(
        tags=["Subjects"],
        summary="Export subjects",
        description="The text/csv response, is a CSV file with same columns as import format)",
        parameters=[
            OpenApiParameter("export_format", str, enum=["json", "csv"]),
            OpenApiParameter("course_id", str, description="Filter by course ID."),
        ],
        responses={
            200: OpenApiResponse(
                response=SubjectImportExportItemSerializer(many=True),
                description="JSON array of subjects (when format=json)",
                examples=[IMPORT_EXPORT_EXAMPLE],
            ),
        },
    )
    def get(self, request: Request) -> HttpResponse:
        file_format = request.query_params.get("export_format", "json").lower()
        if file_format not in ("json", "csv"):
            return Response(
                {"error_code": "INVALID_FORMAT"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        queryset = (
            Subject.objects.select_related("coordinator")
            .prefetch_related("groups", "memberships")
            .order_by("name")
        )

        course_id = request.query_params.get("course_id")
        if course_id:
            queryset = queryset.filter(course_id=course_id)

        content = export_subjects(queryset, file_format=file_format)

        log_event(
            event_type=DATA_EXPORTED,
            actor=request.user,
            organization=request.user.organization,
            ip_address=get_client_ip(request),
            payload={"entity_type": "Subject", "export_format": file_format},
        )

        if file_format == "json":
            data = json.loads(content)
            return Response(data, status=status.HTTP_200_OK)
        response = HttpResponse(content, content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="subjects.csv"'
        return response
