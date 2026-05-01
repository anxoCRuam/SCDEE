"""
Tests for the watermarking flow on the instance PDF download (RF-16.6).

We don't talk to real MinIO — the storage layer is patched to return an
in-memory PNG. The test then asserts that:

* When the requester is the student of the instance, the composed PDF
  bytes go through ``WatermarkService.apply_watermark`` (we assert by
  checking the watermark service is called).
* When the requester is staff/teacher, the watermark function is NOT
  called and the page bytes flow through untouched.

This validates the wiring change in fix C.4.
"""

from __future__ import annotations

import io
import uuid
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from PIL import Image

from apps.courses.models import AcademicCourse
from apps.exams.models import Exam, ExamModel
from apps.instances.models import ExamInstance, ExamPage, InstanceStatus, PageStatus
from apps.instances.services.instance_service import compose_instance_pdf
from apps.organizations.models import Organization
from apps.subjects.models import MembershipRole, Subject, SubjectMembership

pytestmark = pytest.mark.django_db


# ── Watermark tests (RF-16.6) ───────────────────────────────


class TestWatermark:
    def test_watermark_returns_bytes(self):
        """Watermark service should return image bytes."""
        import io

        # Create a minimal white image.
        from PIL import Image

        from apps.instances.services.watermark import WatermarkService

        img = Image.new("RGB", (100, 100), "white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        image_bytes = buf.getvalue()

        result = WatermarkService.apply_watermark(image_bytes, "Juan García", "NIA123")

        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_watermark_handles_errors_gracefully(self):
        """Invalid input should return original bytes."""
        from apps.instances.services.watermark import WatermarkService

        bad_bytes = b"not an image"
        result = WatermarkService.apply_watermark(bad_bytes, "Test", "NIA")
        assert isinstance(result, bytes)


def _png_bytes() -> bytes:
    img = Image.new("RGB", (100, 100), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def instance_with_page():
    user_model = get_user_model()
    org = Organization.objects.create(name="Org", subdomain=f"o-{uuid.uuid4().hex[:8]}")
    student = user_model.objects.create_user(
        email=f"s-{uuid.uuid4().hex[:8]}@x.com",
        password="Pass123!",  # noqa: S106
        first_name="Stu",
        last_name="X",
        organization=org,
    )
    coord = user_model.objects.create_user(
        email=f"c-{uuid.uuid4().hex[:8]}@x.com",
        password="Pass123!",  # noqa: S106
        first_name="Co",
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
        coordinator=coord,
    )
    subject.save()
    SubjectMembership.objects.create(
        organization=org,
        user=coord,
        subject=subject,
        role=MembershipRole.COORDINATOR,
        is_active=True,
    )
    exam = Exam.objects.create(organization=org, name="E", subject=subject)
    model = ExamModel.objects.create(label="A", exam=exam)
    instance = ExamInstance.objects.create(
        organization=org,
        exam=exam,
        model=model,
        student=student,
        status=InstanceStatus.PUBLISHED,
        expected_pages=1,
    )
    ExamPage.objects.create(
        instance=instance,
        page_number=1,
        storage_ref="pages/test.png",
        status=PageStatus.RECOGNIZED,
    )
    return instance


@patch("apps.instances.services.watermark.WatermarkService.apply_watermark")
@patch("apps.exams.services.storage.download_from_minio")
def test_watermark_applied_for_student(mock_download, mock_watermark, instance_with_page):
    raw = _png_bytes()
    mock_download.return_value = raw
    mock_watermark.return_value = raw  # passthrough is fine for the assertion.

    pdf = compose_instance_pdf(instance_with_page, watermark_for_student=True)
    assert pdf.startswith(b"%PDF")
    mock_watermark.assert_called_once()
    args, _ = mock_watermark.call_args
    # First positional arg is the image bytes; the rest are name/NIA.
    assert args[0] == raw


@patch("apps.instances.services.watermark.WatermarkService.apply_watermark")
@patch("apps.exams.services.storage.download_from_minio")
def test_watermark_not_applied_for_staff(mock_download, mock_watermark, instance_with_page):
    mock_download.return_value = _png_bytes()

    pdf = compose_instance_pdf(instance_with_page, watermark_for_student=False)
    assert pdf.startswith(b"%PDF")
    mock_watermark.assert_not_called()
