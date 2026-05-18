"""
Recognition dispatcher — runs recognizers on a page's zones.

Pipeline for each page:
1. Run QRRecognizer on all QR zones.
2. If QR succeeds → identify exam/model/page → run remaining zone recognizers.
3. If QR fails → apply temporal proximity fallback.
4. Store results on ExamPage.
5. Trigger instance assembly.

All pages are expected to be raster images (PNG) after ingestion
normalization, so no PDF detection is needed here.

References: RF-9.3, RF-9.4, RF-9.5, RF-9.6
"""

from __future__ import annotations

import io
import logging
from datetime import UTC, datetime

from apps.exams.models.exams import PageProfile, ZoneType
from apps.ingestion.recognizers.base import RecognitionResult
from apps.ingestion.recognizers.checkbox import CheckboxRecognizer
from apps.ingestion.recognizers.ocr import OCRRecognizer
from apps.ingestion.recognizers.qr import QRRecognizer
from apps.instances.models.instances import ExamPage, PageIssueType, PageStatus

logger = logging.getLogger(__name__)


def recognize_page(page: ExamPage) -> dict:
    """Run recognition pipeline on a single page.

    The page is expected to be a raster image (PNG) after the ingestion
    normalization step.

    Returns a dict of recognition results for storage.
    """
    from apps.exams.services.storage import download_from_minio

    # Download the page image from MinIO.
    try:
        image_bytes = download_from_minio(page.storage_ref)
    except Exception as exc:
        logger.error("Failed to download page %s: %s", page.pk, exc)
        page.status = PageStatus.ORPHAN
        page.issue_type = PageIssueType.QR_READ_ERROR
        page.save(update_fields=["status", "issue_type", "updated_at"])
        return {"error": str(exc)}

    results: dict = {"zones": []}

    # Step 1: Run QR recognition on the full page image.
    qr_result = _run_qr_recognition(image_bytes)

    if qr_result and qr_result.value and qr_result.value.get("valid"):
        # QR success — we know exam, model, page number.
        payload = qr_result.value
        results["qr_payload"] = payload

        # Resolve organization from the QR payload.
        _assign_organization(page, payload["org_id"])

        # Step 2: Find the PageProfile for this model+page.
        profile = _get_page_profile(
            exam_id=payload["exam_id"],
            model_id=payload["model_id"],
            page_number=payload["page_number"],
        )

        if profile:
            # Run non-QR recognizers on remaining zones.
            zone_results = _run_zone_recognizers(image_bytes, profile)
            results["zones"] = zone_results
        else:
            # PageProfile not found — could be EXTRA_PAGE.
            page.issue_type = PageIssueType.EXTRA_PAGE
            results["issue"] = "EXTRA_PAGE"

        # Update page metadata.
        results["zones"] = zone_results
        page.status = PageStatus.RECOGNIZED
        page.recognized_at = datetime.now(tz=UTC)

    else:
        # QR failed — mark issue and leave for proximity fallback.
        page.issue_type = PageIssueType.QR_READ_ERROR
        page.status = PageStatus.PENDING_RECOGNITION
        results["qr_payload"] = None

    page.recognized_data = results
    page.save()

    # Step 3: Trigger assembly (handles both QR success and fallback).
    # from apps.ingestion.services.assembler import assemble_page
    # assemble_page(page, results)

    from apps.ingestion.services.assembler import schedule_assembly

    schedule_assembly(page)

    return results


def _run_qr_recognition(image_bytes: bytes) -> RecognitionResult | None:
    """Try to decode QR from the full page image.

    The image is expected to be a raster format (PNG/JPEG).
    """
    recognizer = QRRecognizer()
    result = recognizer.recognize(image_bytes)
    if result.confidence > 0.5:
        return result
    return None


def _get_page_profile(exam_id: str, model_id: str, page_number: int) -> PageProfile | None:
    """Find the PageProfile for a specific model and page number."""
    try:
        return (
            PageProfile.objects.filter(
                exam_model_id=model_id,
                exam_model__exam_id=exam_id,
                page_number=page_number,
            )
            .prefetch_related("zones")
            .first()
        )
    except Exception:
        return None


def _run_zone_recognizers(image_bytes: bytes, profile: PageProfile) -> list[dict]:
    """Run the appropriate recognizer on each non-QR zone."""
    results = []

    for zone in profile.zones.exclude(zone_type=ZoneType.QR):
        # Crop zone from image.
        cropped = _crop_zone(image_bytes, zone)
        if cropped is None:
            results.append(
                {
                    "zone_id": str(zone.pk),
                    "zone_type": zone.zone_type,
                    "attribute": zone.attribute,
                    "value": None,
                    "confidence": 0.0,
                }
            )
            continue

        # Select recognizer by zone type.
        if zone.zone_type in (ZoneType.OCR_TEXT, ZoneType.OCR_NUMBER):
            recognizer = OCRRecognizer(zone_type=zone.zone_type)
        elif zone.zone_type == ZoneType.CHECKBOX:
            recognizer = CheckboxRecognizer()
        else:
            continue

        result = recognizer.recognize(cropped, attribute=zone.attribute)
        results.append(
            {
                "zone_id": str(zone.pk),
                "zone_type": zone.zone_type,
                "attribute": zone.attribute,
                "value": result.value,
                "confidence": result.confidence,
                "raw_value": result.raw_value,
            }
        )

    return results


def _crop_zone(image_bytes: bytes, zone) -> bytes | None:
    """Crop a zone region from the page image.

    Zone coordinates are in PDF points (origin bottom-left).
    Image coordinates have origin top-left, so we need to flip Y.
    """
    try:
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes))
        img_width, img_height = image.size

        # Convert PDF points to pixel coordinates.
        page_width = zone.page_profile.page_width or img_width
        page_height = zone.page_profile.page_height or img_height

        scale_x = img_width / page_width if page_width > 0 else 1
        scale_y = img_height / page_height if page_height > 0 else 1

        # Convert from PDF coords (bottom-left) to image coords (top-left).
        left = int(zone.x * scale_x)
        bottom = int(zone.y * scale_y)
        right = int((zone.x + zone.width) * scale_x)
        top = int((zone.y + zone.height) * scale_y)

        # Flip Y axis.
        img_top = img_height - top
        img_bottom = img_height - bottom

        # Clamp to image bounds.
        left = max(0, left)
        img_top = max(0, img_top)
        right = min(img_width, right)
        img_bottom = min(img_height, img_bottom)

        if left >= right or img_top >= img_bottom:
            return None

        cropped = image.crop((left, img_top, right, img_bottom))

        buf = io.BytesIO()
        cropped.save(buf, format="PNG")
        return buf.getvalue()

    except Exception as exc:
        logger.warning("Failed to crop zone %s: %s", zone.pk, exc)
        return None


def _assign_organization(page: ExamPage, org_id: str) -> None:
    """Set the organization on a page from the QR payload."""
    from apps.organizations.models.organization import Organization

    try:
        org = Organization.objects.get(pk=org_id)
        page.organization = org
    except Organization.DoesNotExist:
        logger.warning(
            "QR payload references unknown organization %s for page %s. "
            "Page will remain unorganized.",
            org_id,
            page.pk,
        )
