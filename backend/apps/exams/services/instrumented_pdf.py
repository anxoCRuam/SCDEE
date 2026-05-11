"""
Instrumented PDF generation service.

Generates a PDF with QR codes overlaid on the blank PDF template.
Each QR zone defined in the model's PageProfiles gets a QR code
containing: org_id, exam_id, model_id, page_number, and a checksum.

The instrumented PDF is what gets printed and distributed to students.
During ingestion (Fase 6), the system reads these QR codes to identify
which exam, model, and page each scanned image belongs to.

Dependencies:
    - qrcode: QR code image generation
    - pypdf: PDF reading and writing
    - reportlab: Drawing QR images onto PDF pages

References: RF-6.15, RF-6.16
"""

from __future__ import annotations

import hashlib
import io
import json
import logging

from apps.exams.models.exams import ExamModel, PageProfile, ZoneType

logger = logging.getLogger(__name__)


class InstrumentedPDFError(Exception):
    """Raised when PDF generation fails."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(detail or code)


def generate_instrumented_pdf(model: ExamModel) -> bytes:
    """Generate the instrumented PDF for a model (RF-6.15).

    Process:
    1. Load the blank PDF from MinIO.
    2. For each PageProfile, find QR zones.
    3. Generate QR content (JSON with org_id, exam_id, model_id, page_number, checksum).
    4. Overlay QR image at the zone coordinates on each page.
    5. Return the resulting PDF bytes.

    Args:
        model: The ExamModel to generate for.

    Returns:
        The instrumented PDF as bytes.

    Raises:
        InstrumentedPDFError: If blank PDF is missing or no QR zones defined.
    """
    if not model.blank_pdf_ref:
        raise InstrumentedPDFError(
            code="NO_BLANK_PDF",
            detail="Upload a blank PDF before generating the instrumented version.",
        )

    # Check that at least one PageProfile has a QR zone.
    qr_zones_exist = (
        PageProfile.objects.filter(exam_model=model).filter(zones__zone_type=ZoneType.QR).exists()
    )
    if not qr_zones_exist:
        raise InstrumentedPDFError(
            code="NO_QR_ZONES",
            detail="Define at least one QR zone in a PageProfile before generating.",
        )

    # Load blank PDF from MinIO.
    from apps.exams.services.storage import download_from_minio

    blank_pdf_bytes = download_from_minio(model.blank_pdf_ref)

    # Import PDF libraries.
    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.units import inch  # noqa: F401

    reader = PdfReader(io.BytesIO(blank_pdf_bytes))
    writer = PdfWriter()

    exam = model.exam

    # Process each page.
    for page_idx, page in enumerate(reader.pages):
        page_number = page_idx + 1

        # Find QR zones for this page.
        profile = (
            PageProfile.objects.filter(exam_model=model, page_number=page_number)
            .prefetch_related("zones")
            .first()
        )

        qr_zones = []
        if profile:
            qr_zones = list(profile.zones.filter(zone_type=ZoneType.QR))

        if qr_zones:
            # Generate overlay page with QR codes.
            page_box = page.mediabox
            page_width = float(page_box.width)
            page_height = float(page_box.height)

            overlay_bytes = _create_qr_overlay(
                page_width=page_width,
                page_height=page_height,
                qr_zones=qr_zones,
                org_id=str(exam.organization_id),
                exam_id=str(exam.pk),
                model_id=str(model.pk),
                page_number=page_number,
            )

            # Merge overlay onto the original page.
            overlay_reader = PdfReader(io.BytesIO(overlay_bytes))
            page.merge_page(overlay_reader.pages[0])

        writer.add_page(page)

    # Write result to bytes.
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def _create_qr_overlay(
    *,
    page_width: float,
    page_height: float,
    qr_zones: list,
    org_id: str,
    exam_id: str,
    model_id: str,
    page_number: int,
) -> bytes:
    """Create a transparent PDF page with QR codes at zone positions.

    Uses reportlab to draw QR images on a blank page of the same
    dimensions as the original.

    Returns:
        PDF bytes of the overlay page.
    """
    import qrcode
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas as rl_canvas

    buffer = io.BytesIO()
    c = rl_canvas.Canvas(buffer, pagesize=(page_width, page_height))

    for zone in qr_zones:
        # Generate QR content.
        qr_content = _build_qr_content(
            org_id=org_id,
            exam_id=exam_id,
            model_id=model_id,
            page_number=page_number,
        )

        # Generate QR image.
        qr = qrcode.QRCode(
            version=None,  # Auto-size
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=0,
        )
        qr.add_data(qr_content)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white")

        # Convert to bytes for reportlab.
        img_buffer = io.BytesIO()
        qr_img.save(img_buffer, format="PNG")
        img_buffer.seek(0)

        # Draw QR at zone coordinates.
        # PDF coordinates: origin at bottom-left, y increases upward.
        c.drawImage(
            ImageReader(img_buffer),
            x=zone.x,
            y=zone.y,
            width=zone.width,
            height=zone.height,
            preserveAspectRatio=True,
            anchor="sw",
        )

    c.save()
    return buffer.getvalue()


def _build_qr_content(
    *,
    org_id: str,
    exam_id: str,
    model_id: str,
    page_number: int,
) -> str:
    """Build the JSON string encoded in each QR code.

    Format: minified JSON with a checksum for error detection.
    The checksum is a truncated SHA-256 of the data fields.

    Example:
        {"o":"abc123","e":"def456","m":"ghi789","p":1,"c":"a1b2c3d4"}
    """
    data = {
        "o": org_id,
        "e": exam_id,
        "m": model_id,
        "p": page_number,
    }

    # Compute checksum over the data fields.
    raw = f"{org_id}:{exam_id}:{model_id}:{page_number}"
    checksum = hashlib.sha256(raw.encode()).hexdigest()[:8]
    data["c"] = checksum

    return json.dumps(data, separators=(",", ":"))
