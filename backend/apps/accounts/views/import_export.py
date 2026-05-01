"""
User import/export API views.

POST /api/v1/users/import/ — Bulk import from CSV or JSON (RF-2.3)
GET  /api/v1/users/export/ — Export users to CSV or JSON (RF-2.13)

Why separate APIViews instead of @action on UserViewSet?
    1. Import accepts multipart/form-data (file upload), not JSON.
    2. Export returns a file download, not JSON.
    3. Both have different permission semantics and response shapes.
    4. Keeping them as standalone views makes the URL structure clearer
       and avoids bloating the ViewSet with non-CRUD logic.

References: RF-2.3, RF-2.13, RF-16.1
"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.http import HttpResponse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import serializers, status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.filters import UserFilter
from apps.accounts.permissions import IsOrgManager
from apps.accounts.services.import_export import (
    export_users,
    import_users,
    parse_csv_file,
    parse_json_file,
)
from apps.accounts.tasks import send_welcome_email
from apps.audit.services import (
    DATA_EXPORTED,
    DATA_IMPORTED,
    get_client_ip,
    log_event,
)
from apps.core.openapi.serializers import BinaryFileResponseSerializer


class UserImportRequestSerializer(serializers.Serializer):
    """Multipart upload — CSV or JSON file with user rows (RF-2.3)."""

    file = serializers.FileField(required=True)


class UserImportResultSerializer(serializers.Serializer):
    """Report shape returned by the bulk-import endpoint (matches
    ``ImportResult.to_dict``)."""

    created = serializers.IntegerField(read_only=True)
    reactivated = serializers.IntegerField(read_only=True)
    updated = serializers.IntegerField(read_only=True)
    total_processed = serializers.IntegerField(read_only=True)
    total_errors = serializers.IntegerField(read_only=True)
    errors = serializers.ListField(child=serializers.DictField(), read_only=True)


logger = logging.getLogger(__name__)

User = get_user_model()


class UserImportView(APIView):
    """Bulk import users from a CSV or JSON file (RF-2.3).

    Accepts a file upload via multipart/form-data. The file format
    is detected from the file extension or content-type.

    CSV format (fixed columns):
        first_name,last_name,email,dni,nia

    JSON format:
        [{"first_name": "...", "last_name": "...", "email": "...", ...}, ...]

    The import processes each row independently:
    - New email → create user with generated password.
    - Existing but inactive → reactivate and update data.
    - Existing and active → update data only.
    - Validation error → skip row and include in error report.

    Response includes a summary: created, reactivated, updated, errors.
    """

    permission_classes = [IsOrgManager]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    serializer_class = UserImportRequestSerializer

    @extend_schema(
        tags=["Users"],
        summary="Bulk import users from CSV or JSON",
        description="Upload a file to create/update/reactivate users (RF-2.3).",
        request={"multipart/form-data": UserImportRequestSerializer},
        responses={200: UserImportResultSerializer},
    )
    def post(self, request: Request) -> Response:
        # Accept either a file upload or raw JSON body.
        if "file" in request.FILES:
            uploaded_file = request.FILES["file"]
            file_content = uploaded_file.read().decode("utf-8-sig")  # Handle BOM
            filename = uploaded_file.name.lower()

            file_format = "json" if filename.endswith(".json") else "csv"
        elif isinstance(request.data, list):
            # Raw JSON array in request body.
            import json

            file_content = json.dumps(request.data)
            file_format = "json"
        else:
            return Response(
                {"error_code": "NO_FILE_PROVIDED"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Parse the file content.
        try:
            if file_format == "json":
                rows = parse_json_file(file_content)
            else:
                rows = parse_csv_file(file_content)
        except ValueError as exc:
            return Response(
                {"error_code": "INVALID_FILE_FORMAT", "detail": str(exc)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not rows:
            return Response(
                {"error_code": "EMPTY_FILE"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Process the import.
        result = import_users(
            organization=request.user.organization,
            rows=rows,
        )

        # Enqueue welcome emails for new and reactivated users.
        for email, raw_password in result.passwords.items():
            # Find the user's first_name for the email.
            try:
                user = User.objects.get(email=email, organization=request.user.organization)
                send_welcome_email.delay(email, raw_password, user.first_name)
            except User.DoesNotExist:
                pass  # User creation failed — no email needed.

        # Audit log.
        log_event(
            event_type=DATA_IMPORTED,
            actor=request.user,
            organization=request.user.organization,
            ip_address=get_client_ip(request),
            payload={
                "entity_type": "User",
                "format": file_format,
                "created": result.created,
                "reactivated": result.reactivated,
                "updated": result.updated,
                "errors": result.total_errors,
            },
        )

        return Response(result.to_dict(), status=status.HTTP_200_OK)


class UserExportView(APIView):
    """Export users to CSV or JSON file (RF-2.13).

    Returns a downloadable file with user data including decrypted DNIs.
    Supports the same filters as the list endpoint (search, is_active, etc.).

    Query parameters:
        format: "csv" (default) or "json"
        + all filters from UserFilter (search, is_active, is_staff, nia, email)
    """

    permission_classes = [IsOrgManager]
    serializer_class = BinaryFileResponseSerializer

    @extend_schema(
        tags=["Users"],
        summary="Export users to CSV or JSON",
        description="Download user data with decrypted DNIs (RF-2.13).",
        parameters=[
            OpenApiParameter(
                name="format",
                description="Export format: csv or json",
                required=False,
                type=str,
                enum=["csv", "json"],
            ),
        ],
        responses={
            (200, "text/csv"): {"type": "string", "format": "binary"},
            (200, "application/json"): {"type": "string", "format": "binary"},
        },
    )
    def get(self, request: Request) -> HttpResponse:
        file_format = request.query_params.get("format", "csv").lower()
        if file_format not in ("csv", "json"):
            return Response(
                {"error_code": "INVALID_FORMAT", "detail": "Use 'csv' or 'json'."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Apply the same filters as the list endpoint.
        queryset = User.objects.all().order_by("last_name", "first_name")
        filterset = UserFilter(request.query_params, queryset=queryset)
        if filterset.is_valid():
            queryset = filterset.qs

        # Generate file content.
        content = export_users(queryset, file_format=file_format)

        # Audit log.
        log_event(
            event_type=DATA_EXPORTED,
            actor=request.user,
            organization=request.user.organization,
            ip_address=get_client_ip(request),
            payload={
                "entity_type": "User",
                "format": file_format,
                "count": queryset.count(),
            },
        )

        # Return as downloadable file.
        if file_format == "json":
            response = HttpResponse(content, content_type="application/json")
            response["Content-Disposition"] = 'attachment; filename="users.json"'
        else:
            response = HttpResponse(content, content_type="text/csv")
            response["Content-Disposition"] = 'attachment; filename="users.csv"'

        return response
