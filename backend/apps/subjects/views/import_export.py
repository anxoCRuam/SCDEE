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
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsOrgManager
from apps.audit.services import DATA_EXPORTED, DATA_IMPORTED, get_client_ip, log_event
from apps.subjects.models import Subject
from apps.subjects.services.import_export import export_subjects, import_subjects


class SubjectImportRequestSerializer(serializers.Serializer):
    """Multipart upload — JSON file with a list of subject definitions (RF-4.2)."""

    file = serializers.FileField(required=True)


class SubjectImportResultSerializer(serializers.Serializer):
    """Report shape returned by the bulk-import endpoint (matches
    ``SubjectImportResult.to_dict``)."""

    created = serializers.IntegerField(read_only=True)
    total_errors = serializers.IntegerField(read_only=True)
    errors = serializers.ListField(child=serializers.DictField(), read_only=True)


logger = logging.getLogger(__name__)


class SubjectImportView(APIView):
    """Bulk import subjects from JSON (RF-4.2). Manager only."""

    permission_classes = [IsOrgManager]
    parser_classes = [MultiPartParser, JSONParser]
    serializer_class = SubjectImportRequestSerializer

    @extend_schema(
        tags=["Subjects"],
        summary="Bulk import subjects from JSON",
        request={"multipart/form-data": SubjectImportRequestSerializer},
        responses={200: SubjectImportResultSerializer},
    )
    def post(self, request: Request) -> Response:
        # Accept file upload or raw JSON body.
        if "file" in request.FILES:
            file_content = request.FILES["file"].read().decode("utf-8-sig")
            try:
                data = json.loads(file_content)
            except json.JSONDecodeError as exc:
                return Response(
                    {"error_code": "INVALID_JSON", "detail": str(exc)},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        elif isinstance(request.data, list):
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
        parameters=[
            OpenApiParameter("format", str, enum=["json", "csv"]),
            OpenApiParameter("course_id", str, description="Filter by course ID."),
        ],
        responses={200: None},
    )
    def get(self, request: Request) -> HttpResponse:
        file_format = request.query_params.get("format", "json").lower()
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
            payload={"entity_type": "Subject", "format": file_format},
        )

        if file_format == "json":
            response = HttpResponse(content, content_type="application/json")
            response["Content-Disposition"] = 'attachment; filename="subjects.json"'
        else:
            response = HttpResponse(content, content_type="text/csv")
            response["Content-Disposition"] = 'attachment; filename="subjects.csv"'

        return response
