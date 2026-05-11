"""
Unified grade export view.

Endpoints:
    GET /exams/{id}/export/  — Export grades in CSV or JSON format (RF-14.1, RF-14.2)

Query parameters:
    export_format : "csv" (default) or "json"
    For CSV, the column order and headers come from the organization configuration
    (OrganizationConfig.grade_export_columns).

References: RF-14
"""

from __future__ import annotations

import logging

from django.http import HttpResponse
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services.auditlog import DATA_EXPORTED, get_client_ip, log_event
from apps.exams.models.exams import Exam
from apps.subjects.models.permissions import SubjectPermission

logger = logging.getLogger(__name__)


class GradeExportView(APIView):
    """Export exam grades in CSV or JSON format.

    CSV format respects the organization's configured column mapping
    (OrganizationConfig.grade_export_columns).
    """

    permission_classes = [IsAuthenticated, SubjectPermission("can_export_grades")]

    @extend_schema(
        operation_id="export_grades",
        tags=["Exams"],
        summary="Export grades (CSV or JSON)",
        description=(
            "Download grades for a published exam. 'can_export_grades' permission required\n\n"
            "**CSV**: respects the column mapping defined in the "
            "organization configuration (`grade_export_columns`). "
            "Returns a CSV file.\n\n"
            "**JSON**: returns an array of grade objects with per-problem "
            "breakdown."
        ),
        parameters=[
            OpenApiParameter(
                name="export_format",
                description="Export format",
                required=False,
                type=OpenApiTypes.STR,
                enum=["csv", "json"],
                default="csv",
            ),
        ],
        responses={
            (200, "text/csv"): OpenApiTypes.BINARY,
            (200, "application/json"): OpenApiTypes.BINARY,
        },
    )
    def get(self, request: Request, exam_pk: str) -> HttpResponse | Response:
        # 1. Validate exam exists and belongs to the user's organization.
        try:
            exam = Exam.objects.get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response(
                {"error_code": "NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # 2. Determine export format.
        file_format = request.query_params.get("export_format", "csv").lower()
        if file_format not in ("csv", "json"):
            return Response(
                {"error_code": "INVALID_FORMAT", "detail": "Use 'csv' or 'json'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # 3. Get configured columns (only relevant for CSV).
        if file_format == "csv":
            from apps.organizations.services.organization_config import get_org_config

            config = get_org_config(request.user.organization)
            column_mapping = config.grade_export_columns or None
        else:
            column_mapping = None

        # 4. Generate export.
        from apps.exams.services.grade_export import export_grades

        content, content_type = export_grades(
            exam, file_format=file_format, column_mapping=column_mapping
        )

        # 5. Audit log.
        log_event(
            event_type=DATA_EXPORTED,
            actor=request.user,
            organization=request.user.organization,
            ip_address=get_client_ip(request),
            payload={
                "entity_type": "Grades",
                "export_format": file_format,
                "exam": exam.name,
            },
        )

        # 6. Return response.
        if file_format == "csv":
            filename = f"grades_{exam.pk}.csv"
            response = HttpResponse(content, content_type=content_type)
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response

        # JSON: return as JSON response directly.
        return Response(content, status=status.HTTP_200_OK)
