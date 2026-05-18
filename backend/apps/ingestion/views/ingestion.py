"""
Ingestion-related API views.

Minimal HTTP surface — most ingestion happens via the SFTP watcher
(``manage.py sftp_watcher``) which feeds Celery directly. The HTTP
``ingest`` endpoint exists so manual / automated tests can drive the
recognition pipeline without an SFTP server.

Endpoints:
    GET  /api/v1/recognition/engines/ — List available OCR engines (RF-9.9)
    POST /api/v1/recognition/ingest/  — Manual page ingestion (manager-only)

References: RF-9.2, RF-9.8, RF-9.9.
"""

from __future__ import annotations

import base64
import logging

from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models.permissions import IsOrgManager
from apps.core.openapi.errors import ErrorCode, error_response
from apps.ingestion.models.ingestion import IngestionBatch
from apps.ingestion.serializers.ingestion import (
    MANUAL_INGEST_RESPONSE_EXAMPLE,
    OCR_ENGINE_LIST_EXAMPLE,
    ManualIngestRequestSerializer,
    ManualIngestResponseSerializer,
    OCREngineSerializer,
)

logger = logging.getLogger(__name__)


class OCREngineListView(APIView):
    """List available OCR engines (RF-9.9)."""

    permission_classes = [IsOrgManager]
    serializer_class = OCREngineSerializer

    @extend_schema(
        operation_id="recognition_engines_list",
        tags=["Ingestion"],
        summary="List registered OCR engines (RF-9.9)",
        description=(
            "**Return the OCR engines registered in this deployment.**\n\n"
            "OCR engines are pluggable (see :class:`BaseOCREngine`) and "
            "registered at startup. Typical entries:\n"
            "- ``easyocr`` — pure-Python, GPU-optional, default choice.\n"
            "- ``tesseract`` — system binary, faster on CPU for printed text.\n\n"
            "QR and CHECKBOX recognisers are *not* listed: they are fixed "
            "components of the system and cannot be swapped per organisation "
            "(RF-9.9).\n\n"
            "Manager-only: students and graders do not need this listing."
        ),
        responses={200: OCREngineSerializer(many=True)},
        examples=[OCR_ENGINE_LIST_EXAMPLE],
    )
    def get(self, request: Request) -> Response:
        from apps.ingestion.recognizers.base import list_ocr_engines

        engines = list_ocr_engines()
        return Response(engines)


class ManualIngestView(APIView):
    """Manual page ingestion for testing (not for production scanners).

    Accepts a single page image or multi-page PDF upload, normalises it,
    stores each page in MinIO, creates ExamPage rows, and returns
    details about every page created.
    """

    permission_classes = [IsOrgManager]
    parser_classes = [MultiPartParser]
    serializer_class = ManualIngestRequestSerializer

    @extend_schema(
        operation_id="recognition_manual_ingest",
        tags=["Ingestion"],
        summary="Manually ingest pages (PDF or image) (RF-9.2)",
        description=(
            "**Upload a file (PDF or image) into the recognition pipeline.**\n\n"
            "Multi-page PDFs are split and each page is stored as a separate "
            "``ExamPage``. Recognition tasks are enqueued asynchronously, "
            "but the response includes the list of created page IDs immediately.\n\n"
            "**Process**\n\n"
            "1. Validate the multipart payload (``file`` is required).\n"
            "2. Convert to PNG pages (splitting PDFs if needed).\n"
            "3. Upload each page to MinIO and create an ``ExamPage`` row.\n"
            "4. Enqueue a ``recognize_page_task`` for each page.\n"
            "5. Return the list of created pages and their storage references.\n\n"
            "**Output**\n\n"
            "``202 Accepted`` with details about every page created."
        ),
        request={"multipart/form-data": ManualIngestRequestSerializer},
        responses={
            202: ManualIngestResponseSerializer,
            400: error_response(
                [ErrorCode.NO_FILE_PROVIDED],
                status_code=400,
                description="The ``file`` field was missing from the multipart payload.",
            ),
        },
        examples=[MANUAL_INGEST_RESPONSE_EXAMPLE],
    )
    def post(self, request: Request) -> Response:
        if "file" not in request.FILES:
            return Response(
                {"error_code": "NO_FILE_PROVIDED"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        file_data = request.FILES["file"].read()
        filename = request.FILES["file"].name
        file_data_b64 = base64.b64encode(file_data).decode("ascii")

        # ── Crear IngestionBatch ──────────────────────────────
        batch = IngestionBatch.objects.create(
            source="manual",
            total_pages=1,  # la tarea lo corregirá si es PDF
        )

        from apps.ingestion.tasks import ingest_page_task

        result = ingest_page_task.delay(
            file_data_b64,
            filename,
            organization_id=str(request.user.organization_id),
            batch_id=str(batch.id),
        )

        return Response(
            {
                "task_id": result.id,
                "filename": filename,
                "batch_id": str(batch.id),
                "total_pages": 1,
                "status": "queued",
            },
            status=status.HTTP_202_ACCEPTED,
        )
