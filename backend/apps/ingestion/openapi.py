"""
Reusable OpenAPI examples for the ``ingestion`` app.

References: RF-9.x.
"""

from __future__ import annotations

from drf_spectacular.utils import OpenApiExample

MANUAL_INGEST_RESPONSE_EXAMPLE = OpenApiExample(
    name="ManualIngestQueued",
    summary="Page accepted, recognition task queued",
    description=(
        "The endpoint returns immediately with ``202 Accepted`` after enqueuing "
        "the page. The caller can poll the task status separately or wait for "
        "the resulting ``ExamPage`` to appear in the instance list."
    ),
    value={
        "task_id": "f1c92b5e-31a8-4ec5-a7e1-7b9b03f5e7e2",
        "filename": "page_42.png",
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
