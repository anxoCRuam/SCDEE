"""
Tests for the instrumented PDF generation pipeline (RF-6.15).

The existing test_exams.py covers invalidation behaviour (zones changed
→ ``instrumented_pdf_valid=False``) and the 409 path on the download
endpoint. These tests exercise the actual generation: a synthetic blank
PDF is fed in, the instrumented output is rendered, and we decode the
generated QR codes back to verify they match the expected payload.
"""

from __future__ import annotations

import io
import json
import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model

from apps.courses.models import AcademicCourse
from apps.exams.models import (
    Exam,
    ExamModel,
    PageProfile,
    RecognitionZone,
    ZoneType,
)
from apps.exams.services.instrumented_pdf import (
    InstrumentedPDFError,
    _build_qr_content,
    generate_instrumented_pdf,
)
from apps.organizations.models import Organization
from apps.subjects.models import MembershipRole, Subject, SubjectMembership

pytestmark = pytest.mark.django_db


# ── Helpers ──────────────────────────────────────────────────


def _blank_pdf_bytes(num_pages: int = 1) -> bytes:
    """Build a minimal multi-page PDF in memory (no third-party stubs)."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    pdf = canvas.Canvas(buf)
    for i in range(num_pages):
        pdf.drawString(100, 100, f"Page {i + 1}")
        pdf.showPage()
    pdf.save()
    return buf.getvalue()


@pytest.fixture
def model_with_qr_zone():
    user_model = get_user_model()
    org = Organization.objects.create(name="Org", subdomain=f"o-{uuid.uuid4().hex[:8]}")
    user = user_model.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:8]}@x.com",
        password="Pass123!",  # noqa: S106
        first_name="C",
        last_name="O",
        organization=org,
        is_staff=True,
    )
    course = AcademicCourse(organization=org, label="2026", is_active=True, status="ACTIVE")
    course.save()
    subject = Subject(
        organization=org,
        name="S",
        code=f"S{uuid.uuid4().hex[:4]}",
        course=course,
        coordinator=user,
    )
    subject.save()
    SubjectMembership.objects.create(
        organization=org,
        user=user,
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
        exam_model=model, page_number=1, page_width=595, page_height=842
    )
    RecognitionZone.objects.create(
        page_profile=profile,
        zone_type=ZoneType.QR,
        attribute="exam_qr",
        x=20,
        y=20,
        width=120,
        height=120,
    )
    return model


# ── QR payload format (RF-6.15 / dispatcher contract) ───────


class TestQRPayload:
    def test_payload_includes_required_fields(self):
        payload_str = _build_qr_content(org_id="O", exam_id="E", model_id="M", page_number=1)
        data = json.loads(payload_str)
        assert data["o"] == "O"
        assert data["e"] == "E"
        assert data["m"] == "M"
        assert data["p"] == 1
        assert "c" in data and len(data["c"]) >= 4  # checksum

    def test_payload_checksum_is_deterministic(self):
        a = _build_qr_content(org_id="O", exam_id="E", model_id="M", page_number=1)
        b = _build_qr_content(org_id="O", exam_id="E", model_id="M", page_number=1)
        assert a == b

    def test_payload_checksum_changes_on_data_drift(self):
        a = _build_qr_content(org_id="O", exam_id="E", model_id="M", page_number=1)
        b = _build_qr_content(org_id="O", exam_id="E", model_id="M", page_number=2)
        assert a != b


# ── Generation ───────────────────────────────────────────────


class TestGenerateInstrumentedPDF:
    def test_no_blank_pdf_raises(self):
        org = Organization.objects.create(name="Org", subdomain=f"o-{uuid.uuid4().hex[:8]}")
        course = AcademicCourse(organization=org, label="2026", is_active=True, status="ACTIVE")
        course.save()
        user = get_user_model().objects.create_user(
            email=f"u-{uuid.uuid4().hex[:8]}@x.com",
            password="Pass123!",  # noqa: S106
            first_name="C",
            last_name="O",
            organization=org,
            is_staff=True,
        )
        subject = Subject(
            organization=org,
            name="S",
            code=f"S{uuid.uuid4().hex[:4]}",
            course=course,
            coordinator=user,
        )
        subject.save()
        SubjectMembership.objects.create(
            organization=org,
            user=user,
            subject=subject,
            role=MembershipRole.COORDINATOR,
            is_active=True,
        )
        exam = Exam.objects.create(organization=org, name="E", subject=subject)
        # No blank PDF reference set.
        model = ExamModel.objects.create(label="A", exam=exam)

        with pytest.raises(InstrumentedPDFError) as exc_info:
            generate_instrumented_pdf(model)
        assert exc_info.value.code == "NO_BLANK_PDF"

    def test_no_qr_zones_raises(self, model_with_qr_zone):
        # Wipe the only QR zone.
        RecognitionZone.objects.filter(
            page_profile__exam_model=model_with_qr_zone, zone_type=ZoneType.QR
        ).delete()

        with pytest.raises(InstrumentedPDFError) as exc_info:
            generate_instrumented_pdf(model_with_qr_zone)
        assert exc_info.value.code == "NO_QR_ZONES"

    def test_generated_pdf_starts_with_pdf_header(self, model_with_qr_zone):
        with patch(
            "apps.exams.services.storage.download_from_minio",
            return_value=_blank_pdf_bytes(num_pages=1),
        ):
            pdf_bytes = generate_instrumented_pdf(model_with_qr_zone)
        assert pdf_bytes.startswith(b"%PDF")
        assert len(pdf_bytes) > 1024  # has actual content

    def test_generated_qr_decodes_to_expected_payload(self, model_with_qr_zone):
        """The QR overlaid on the PDF must decode to a payload matching
        the model's identifiers — that is the contract the ingestion
        dispatcher relies on (RF-9.3).

        Native dependencies (provided by the Docker image):
          * ``poppler-utils`` — used by ``pdf2image`` to rasterise.
          * ``libzbar0``      — used by ``pyzbar`` to decode QRs.
        """
        from pdf2image import convert_from_bytes
        from pyzbar.pyzbar import decode

        with patch(
            "apps.exams.services.storage.download_from_minio",
            return_value=_blank_pdf_bytes(num_pages=1),
        ):
            pdf_bytes = generate_instrumented_pdf(model_with_qr_zone)

        images = convert_from_bytes(pdf_bytes, dpi=200)
        assert images, "pdf2image returned no rendered pages."

        decoded = decode(images[0])
        assert decoded, "No QR detected in the instrumented PDF page."

        payload = json.loads(decoded[0].data.decode("utf-8"))
        exam = model_with_qr_zone.exam
        assert payload["o"] == str(exam.organization_id)
        assert payload["e"] == str(exam.pk)
        assert payload["m"] == str(model_with_qr_zone.pk)
        assert payload["p"] == 1
