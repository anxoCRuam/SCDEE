# backend/apps/ingestion/services/ingestion_service.py
"""
Core ingestion logic shared by the Celery task and the manual HTTP endpoint.

Handles normalisation (PDF splitting, raster-to-PNG), MinIO storage,
ExamPage creation, and enqueuing the per-page recognition tasks.
"""

from __future__ import annotations

import io
import logging
from datetime import UTC, datetime

from django.conf import settings

from apps.exams.services.storage import upload_to_minio
from apps.instances.models.instances import ExamPage, PageStatus

logger = logging.getLogger(__name__)


def process_ingest_file(
    file_data: bytes,
    filename: str,
    organization_id: str | None = None,
) -> dict:
    """Normalise the uploaded file, store each page in MinIO, create ExamPage rows,
    and enqueue recognition tasks for every page.

    Returns a summary dict identical to the one ``ingest_page_task`` used to return,
    so both the Celery task and the synchronous view can use this function.
    """
    # Validate size
    max_size_mb = getattr(settings, "INGESTION_MAX_PAGE_SIZE_MB", 50)
    if len(file_data) > max_size_mb * 1024 * 1024:
        raise ValueError(f"File exceeds maximum size of {max_size_mb} MB")

    # Normalize to PNG pages
    normalized_pages = _normalize_to_png_pages(file_data, filename)

    created_pages: list[dict] = []
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d%H%M%S%f")

    for page_index, png_bytes in enumerate(normalized_pages):
        # Build a unique storage key per page
        if len(normalized_pages) > 1:
            page_filename = f"{filename}_p{page_index + 1}.png"
        else:
            page_filename = f"{filename}.png"

        storage_key = f"pending/ingestion/{timestamp}_{page_filename}"

        upload_to_minio(key=storage_key, data=png_bytes, content_type="image/png")

        page = ExamPage.objects.create(
            instance=None,
            page_number=0,
            storage_ref=storage_key,
            status=PageStatus.PENDING_RECOGNITION,
            organization_id=organization_id,
        )

        # Enqueue recognition asynchronously
        from apps.ingestion.tasks import recognize_page_task

        recognize_page_task.delay(str(page.pk))

        created_pages.append(
            {
                "page_id": str(page.pk),
                "storage_ref": storage_key,
                "page_index": page_index + 1,
                "status": "queued",
            }
        )

    return {
        "filename": filename,
        "total_pages": len(normalized_pages),
        "pages": created_pages,
    }


def _normalize_to_png_pages(file_data: bytes, filename: str) -> list[bytes]:
    """Convert any supported input to a list of PNG page images.

    - PDF: each page is rendered to a PNG at 200 DPI.
    - Raster images (PNG/JPEG/TIFF): returned as a single-element list
      after conversion to PNG if necessary.
    """
    if file_data.startswith(b"%PDF"):
        return _pdf_to_png_pages(file_data)
    return [_raster_to_png(file_data)]


def _pdf_to_png_pages(pdf_bytes: bytes) -> list[bytes]:
    """Render each page of a PDF to a PNG at 200 DPI."""
    from pdf2image import convert_from_bytes

    images = convert_from_bytes(pdf_bytes, dpi=200)
    result: list[bytes] = []
    for image in images:
        out = io.BytesIO()
        image.save(out, format="PNG")
        result.append(out.getvalue())
    logger.info("PDF converted: %d pages → %d PNG images.", len(images), len(result))
    return result


def _raster_to_png(image_bytes: bytes) -> bytes:
    """Ensure a raster image is in PNG format. If already PNG, return as-is."""
    if image_bytes[:8] == b"\x89PNG\r\n\x1a\n":
        return image_bytes

    from PIL import Image

    image = Image.open(io.BytesIO(image_bytes))
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
