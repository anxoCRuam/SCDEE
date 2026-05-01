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

from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.parsers import MultiPartParser
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.permissions import IsOrgManager
from apps.core.openapi.openapi import ErrorCode, error_response
from apps.ingestion.openapi import MANUAL_INGEST_RESPONSE_EXAMPLE, OCR_ENGINE_LIST_EXAMPLE


class OCREngineSerializer(serializers.Serializer):
    """One row of the OCR engine listing."""

    engine_id = serializers.CharField(read_only=True)
    type = serializers.CharField(read_only=True)


class ManualIngestRequestSerializer(serializers.Serializer):
    """Multipart input for the manual ingest endpoint."""

    file = serializers.FileField(
        required=True,
        help_text=(
            "Single page image (PNG/JPG/TIFF) or single-page PDF. "
            "The recognition pipeline will decode the embedded QR to "
            "identify exam, model and page number — see RF-9.3."
        ),
    )


class ManualIngestResponseSerializer(serializers.Serializer):
    """Output of the manual ingest endpoint (Celery task receipt)."""

    task_id = serializers.CharField(
        read_only=True,
        help_text="Celery task UUID. Use it to correlate the page with its recognition log.",
    )
    filename = serializers.CharField(
        read_only=True,
        help_text="Original filename echoed back for the caller's convenience.",
    )
    status = serializers.CharField(
        read_only=True,
        help_text="Always ``queued`` — the task runs asynchronously.",
    )


class OCREngineListView(APIView):
    """List available OCR engines (RF-9.9)."""

    permission_classes = [IsOrgManager]
    serializer_class = OCREngineSerializer

    @extend_schema(
        operation_id="recognition_engines_list",
        tags=["Recognition"],
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

    Accepts a single page image upload and enqueues it for recognition.
    """

    permission_classes = [IsOrgManager]
    parser_classes = [MultiPartParser]
    serializer_class = ManualIngestRequestSerializer

    @extend_schema(
        operation_id="recognition_manual_ingest",
        tags=["Recognition"],
        summary="Manually ingest a single scanned page (RF-9.2)",
        description=(
            "**Push one page image into the recognition pipeline.**\n\n"
            "This is the HTTP equivalent of dropping a file in the SFTP "
            "inbox watched by ``manage.py sftp_watcher`` (RF-9.1). It "
            "exists for manual/automated testing and one-off corrections; "
            "the production path for bulk scanning is the SFTP watcher.\n\n"
            "**Process**\n\n"
            "1. Validate the multipart payload (``file`` is required and "
            "non-empty).\n"
            "2. Read the file contents in memory and base64-encode them so "
            "they fit in a Celery task message.\n"
            "3. Enqueue ``ingest_page_task`` on the ``recognition`` queue "
            "with the caller's organisation. The task will:\n"
            "   - Decode the QR to identify exam/model/page (RF-9.3).\n"
            "   - Run the recognisers declared on the matching ``PageProfile`` "
            "(RF-9.4).\n"
            "   - Assemble the page into the corresponding ``ExamInstance`` "
            "(RF-9.10) or mark it as ``ORPHAN`` for manual handling.\n\n"
            "**Output**\n\n"
            "``202 Accepted`` with the Celery ``task_id``. The page does "
            "*not* exist in the database yet at the time the response is "
            "sent — the task runs asynchronously.\n\n"
            "**Authorisation**\n\n"
            "Manager-only (``IsOrgManager``)."
        ),
        request={"multipart/form-data": ManualIngestRequestSerializer},
        responses={
            202: ManualIngestResponseSerializer,
            400: error_response(
                [ErrorCode.NO_FILE_PROVIDED],
                status_code=400,
                description=(
                    "The ``file`` field was missing from the multipart "
                    "payload. Note: empty files are accepted at this stage; "
                    "format validation happens in the Celery task."
                ),
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

        from apps.ingestion.tasks import ingest_page_task

        result = ingest_page_task.delay(
            file_data_b64,
            filename,
            organization_id=str(request.user.organization_id),
        )

        return Response(
            {"task_id": result.id, "filename": filename, "status": "queued"},
            status=status.HTTP_202_ACCEPTED,
        )
