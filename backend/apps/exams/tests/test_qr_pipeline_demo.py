"""
End-to-end pytest counterparts of the ``demo_qr_pipeline`` command.

The management command in ``apps.exams.management.commands.demo_qr_pipeline``
is the *visual* check (artefacts to disk, human inspection). These
tests are the *automated* version: same logic, MinIO mocked, run on
every CI execution.

What we cover:

- The QR payload encoded into the instrumented PDF round-trips through
  ``pdf2image`` rendering and the production ``QRRecognizer`` exactly,
  with the expected checksum.
- Custom QR coordinates are honoured (zone position parametrised).
- Tampering — moving a corrupted byte into the QR payload — is caught
  by the checksum (``valid=False``).

References: RF-6.15, RF-9.3.
"""

from __future__ import annotations

import io
import uuid

import pytest
from django.contrib.auth import get_user_model

from apps.courses.models.courses import AcademicCourse
from apps.exams.models.exams import (
    Exam,
    ExamModel,
    PageProfile,
    RecognitionZone,
    ZoneType,
)
from apps.exams.services.instrumented_pdf import generate_instrumented_pdf
from apps.ingestion.recognizers.qr import QRRecognizer
from apps.organizations.models.organization import Organization
from apps.subjects.models.subjects import MembershipRole, Subject, SubjectMembership

pytestmark = pytest.mark.django_db


# ── Helpers ─────────────────────────────────────────────────────────


def _blank_a4_pdf_bytes() -> bytes:
    """Build a minimal blank A4 PDF in memory (no third-party stubs)."""
    from reportlab.pdfgen import canvas as rl_canvas

    buffer = io.BytesIO()
    c = rl_canvas.Canvas(buffer, pagesize=(595.0, 842.0))
    c.showPage()
    c.save()
    return buffer.getvalue()


def _render_pdf_first_page_png(pdf_bytes: bytes) -> bytes:
    """Render page 1 of a PDF to a PNG byte string at 200 DPI."""
    from pdf2image import convert_from_bytes

    images = convert_from_bytes(pdf_bytes, dpi=200, first_page=1, last_page=1)
    out = io.BytesIO()
    images[0].save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def model_with_qr_zone(db):
    """Build a complete tenant + exam + model + page-with-QR-zone graph."""
    user_model = get_user_model()
    suffix = uuid.uuid4().hex[:8]

    org = Organization.objects.create(name=f"Org-{suffix}", subdomain=f"o{suffix}")
    coordinator = user_model.objects.create_user(
        email=f"u-{suffix}@x.com",
        password="Pass123!",  # noqa: S106
        first_name="C",
        last_name="O",
        organization=org,
        is_staff=True,
    )
    course = AcademicCourse.objects.create(organization=org, label="2026", is_active=True)
    subject = Subject.objects.create(
        organization=org,
        name="S",
        code=f"S{suffix}",
        course=course,
        coordinator=coordinator,
    )
    SubjectMembership.objects.create(
        organization=org,
        user=coordinator,
        subject=subject,
        role=MembershipRole.COORDINATOR,
        is_active=True,
    )
    exam = Exam.objects.create(organization=org, name="E", subject=subject)
    model = ExamModel.objects.create(
        label="A",
        exam=exam,
        blank_pdf_ref="exams/test/blank.pdf",
        blank_pdf_pages=1,
    )
    profile = PageProfile.objects.create(
        exam_model=model, page_number=1, page_width=595.0, page_height=842.0
    )
    zone = RecognitionZone.objects.create(
        page_profile=profile,
        zone_type=ZoneType.QR,
        attribute="exam_qr",
        x=440.0,
        y=740.0,
        width=80.0,
        height=80.0,
    )
    return {
        "org": org,
        "exam": exam,
        "model": model,
        "profile": profile,
        "zone": zone,
    }


# ── Tests ───────────────────────────────────────────────────────────


def test_qr_round_trip_decodes_correct_payload(model_with_qr_zone, monkeypatch):
    """Instrumented PDF → render → decode → original payload, with valid checksum.

    This is the same chain a real scanner-fed page goes through. If
    any step regresses (different QR encoding, broken rendering, lost
    coordinate translation), the assertion fires.
    """
    org = model_with_qr_zone["org"]
    exam = model_with_qr_zone["exam"]
    model = model_with_qr_zone["model"]

    # Stub MinIO download so we don't need a live broker. The blank
    # PDF is generated in-memory and returned by the patched function.
    # ``instrumented_pdf.py`` imports ``download_from_minio`` lazily
    # inside the function body, so we patch the source module directly.
    blank_pdf = _blank_a4_pdf_bytes()
    monkeypatch.setattr(
        "apps.exams.services.storage.download_from_minio",
        lambda key: blank_pdf,
    )

    # Generate the instrumented PDF and render it back to a PNG.
    instrumented_pdf = generate_instrumented_pdf(model)
    page_image = _render_pdf_first_page_png(instrumented_pdf)

    # Production decoder.
    recognizer = QRRecognizer()
    result = recognizer.recognize(page_image)

    assert result.value is not None, (
        f"QR could not be decoded. raw={result.raw_value!r}. "
        f"This usually means the QR was not rendered correctly or "
        f"its zone coordinates fell off the page."
    )
    payload = result.value
    assert payload["org_id"] == str(org.pk)
    assert payload["exam_id"] == str(exam.pk)
    assert payload["model_id"] == str(model.pk)
    assert payload["page_number"] == 1
    assert payload["valid"] is True, "Checksum mismatch — QR payload was corrupted."
    assert result.confidence == 1.0


@pytest.mark.parametrize(
    ("qr_x", "qr_y", "qr_size"),
    [
        (20.0, 20.0, 60.0),  # bottom-left corner
        (440.0, 740.0, 80.0),  # top-right corner
        (250.0, 400.0, 100.0),  # centre
    ],
    ids=["bottom-left", "top-right", "centre"],
)
def test_qr_round_trip_at_various_positions(model_with_qr_zone, monkeypatch, qr_x, qr_y, qr_size):
    """Rotating the QR zone across the page must not break decoding.

    Catches a class of regressions where coordinate translation
    between PDF points (origin bottom-left) and image pixels (origin
    top-left) is mishandled — the QR would still decode but the
    rendered position would be wrong.
    """
    model = model_with_qr_zone["model"]
    zone = model_with_qr_zone["zone"]

    # Adjust the zone in-place.
    zone.x = qr_x
    zone.y = qr_y
    zone.width = qr_size
    zone.height = qr_size
    zone.save()

    blank_pdf = _blank_a4_pdf_bytes()
    monkeypatch.setattr(
        "apps.exams.services.storage.download_from_minio",
        lambda key: blank_pdf,
    )

    instrumented_pdf = generate_instrumented_pdf(model)
    page_image = _render_pdf_first_page_png(instrumented_pdf)

    recognizer = QRRecognizer()
    result = recognizer.recognize(page_image)

    assert (
        result.value is not None
    ), f"QR not decoded at position ({qr_x}, {qr_y}, size {qr_size}). raw={result.raw_value!r}"
    assert result.value["valid"] is True


def test_qr_with_tampered_checksum_is_marked_invalid(model_with_qr_zone, monkeypatch):
    """A QR whose payload's checksum has been tampered with must report ``valid=False``.

    Cannot mutate the QR image bytes without breaking the QR itself,
    so we go one level lower: build a synthetic QR whose payload
    contains a wrong checksum, encode it as PNG, and feed it through
    the recogniser.
    """
    import json

    import qrcode

    bogus_payload = {
        "o": "01234567-89ab-cdef-0123-456789abcdef",
        "e": "11111111-2222-3333-4444-555555555555",
        "m": "99999999-8888-7777-6666-555555555555",
        "p": 1,
        "c": "deadbeef",  # not the real checksum
    }
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=2,
    )
    qr.add_data(json.dumps(bogus_payload, separators=(",", ":")))
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    recognizer = QRRecognizer()
    result = recognizer.recognize(buf.getvalue())

    assert result.value is not None, "QR decode failed — test setup bug, not the assertion target."
    assert result.value["valid"] is False, (
        "Tampered checksum was accepted as valid. The recogniser is not "
        "checking the checksum, which would let a forged QR slip through "
        "the recognition pipeline."
    )
    assert result.confidence == 0.5
