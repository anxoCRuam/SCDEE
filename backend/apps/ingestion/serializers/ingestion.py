"""
Reusable OpenAPI examples for the ``ingestion`` app.

References: RF-9.x.
"""

from __future__ import annotations

from drf_spectacular.utils import OpenApiExample
from rest_framework import serializers

MANUAL_INGEST_RESPONSE_EXAMPLE = OpenApiExample(
    name="ManualIngestQueued",
    summary="Pages created, recognition tasks queued",
    description=(
        "The endpoint returns immediately with details about every page "
        "created from the uploaded file. Recognition tasks run asynchronously."
    ),
    value={
        "task_id": "f1c92b5e-31a8-4ec5-a7e1-7b9b03f5e7e2",
        "filename": "exam_a_model_1.pdf",
        "total_pages": 3,
        "pages": [
            {
                "page_id": "b2a1c0d9-e8f7-6543-2109-87654321abcd",
                "storage_ref": "pending/ingestion/20260507..._p1.png",
                "page_index": 1,
                "status": "queued",
            },
            {
                "page_id": "c3b2d1e0-f9a8-7654-3210-98765432bcde",
                "storage_ref": "pending/ingestion/20260507..._p2.png",
                "page_index": 2,
                "status": "queued",
            },
            # ...
        ],
        "status": "queued",
    },
    response_only=True,
    status_codes=["202"],
)

OCR_ENGINE_LIST_EXAMPLE = OpenApiExample(
    name="OcrEngineList",
    summary="Available OCR engines for the caller's organisation",
    description=(
        "Returned by ``GET /recognition/engines/``. The frontend uses this "
        "to populate the ‘OCR engine’ selector in the organisation's "
        "configuration screen (RF-9.8, RF-9.9)."
    ),
    value=[
        {"engine_id": "easyocr", "type": "OCR_TEXT"},
        {"engine_id": "tesseract", "type": "OCR_TEXT"},
    ],
    response_only=True,
    status_codes=["200"],
)


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
    total_pages = serializers.IntegerField(
        read_only=True,
        help_text="Number of pages created from the uploaded file.",
    )
    pages = serializers.ListField(
        child=serializers.DictField(),
        read_only=True,
        help_text="List of created page IDs and their storage references.",
    )
    status = serializers.CharField(
        read_only=True,
        help_text="Always ``queued`` — the tasks run asynchronously.",
    )
