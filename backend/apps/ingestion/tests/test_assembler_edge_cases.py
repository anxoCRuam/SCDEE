"""
Edge-case integration tests for the v2 batched assembler.

These tests exercise ``assemble_exam(exam_id)`` — the deferred,
lot-based assembler that runs after the assembly window closes —
over awkward shapes that real-world scanners produce.

What we cover:

- Three pages of a single batch all end up in one instance regardless
  of the order they were created in.
- Duplicate page numbers within the same batch are attached but do
  not falsely complete the instance.
- Pages of a batch with more pages than the model declares are all
  attached (v2 does not separately tag extras as ``EXTRA_PAGE``).
- A page whose QR points to a different exam is not picked up by
  ``assemble_exam`` of this exam.
- A complete in-order batch transitions ASSEMBLING → RECEIVED.

The tests operate at the service-layer level (``assemble_exam``
directly) rather than going through HTTP, because the assembler is
the unit under test. The recognition step (which sets
``recognized_data`` on pages) is simulated by writing the structure
directly to each page.

References: RF-9.10, RF-9.11, RF-7.1.
"""

from __future__ import annotations

import uuid

import pytest
from django.contrib.auth import get_user_model

from apps.accounts.services.encryption import encrypt_dni
from apps.courses.models.courses import AcademicCourse
from apps.exams.models.exams import Exam, ExamConvocation, ExamModel, PageProfile
from apps.ingestion.models.ingestion import IngestionBatch
from apps.ingestion.services.assembler import assemble_exam
from apps.instances.models.instances import (
    ExamInstance,
    ExamPage,
    InstanceStatus,
    PageStatus,
)
from apps.organizations.models.organization import Organization
from apps.subjects.models.subjects import MembershipRole, Subject, SubjectMembership

pytestmark = pytest.mark.django_db


# ── Helpers ────────────────────────────────────────────────────────


def _build_three_page_exam_with_one_student() -> dict:
    """Build org + exam + model with three PageProfiles + one convoked student.

    The student has known identifiers (John Doe, NIA 111111, DNI 11111111H)
    so the OCR'd zones attached to each test page score 1.0 against them
    and the Hungarian assignment is trivial.
    """
    user_model = get_user_model()
    suffix = uuid.uuid4().hex[:8]

    org = Organization.objects.create(name=f"Org-{suffix}", subdomain=f"o{suffix}")
    coordinator = user_model.objects.create_user(
        email=f"c-{suffix}@x.com",
        password="P@ss123!",  # noqa: S106
        first_name="C",
        last_name="O",
        organization=org,
        is_staff=True,
    )
    course = AcademicCourse.objects.create(organization=org, label="2026", is_active=True)
    subject = Subject.objects.create(
        organization=org,
        name="Subject",
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
    model = ExamModel.objects.create(label="A", exam=exam, blank_pdf_pages=3)
    for i in (1, 2, 3):
        PageProfile.objects.create(
            exam_model=model,
            page_number=i,
            page_width=595.0,
            page_height=842.0,
        )

    student = user_model.objects.create_user(
        email=f"s-{suffix}@x.com",
        password="P@ss123!",  # noqa: S106
        first_name="John",
        last_name="Doe",
        organization=org,
        nia="111111",
    )
    encrypted, nonce = encrypt_dni("11111111H")
    student.encrypted_dni = encrypted
    student.dni_nonce = nonce
    student.save()
    SubjectMembership.objects.create(
        organization=org,
        user=student,
        subject=subject,
        role=MembershipRole.STUDENT,
        is_active=True,
    )
    ExamConvocation.objects.create(exam=exam, student=student)

    return {"org": org, "exam": exam, "model": model, "student": student}


def _make_batch(organization: Organization) -> IngestionBatch:
    """Create a manual-source IngestionBatch."""
    return IngestionBatch.objects.create(source="manual")


def _make_recognized_page(
    *,
    organization: Organization,
    exam: Exam,
    model: ExamModel,
    page_number: int,
    batch: IngestionBatch | None = None,
    ocr_name: str = "John Doe",
    ocr_nia: str = "111111",
    ocr_dni: str = "11111111H",
) -> ExamPage:
    """Create an ExamPage already in RECOGNIZED state with QR + OCR zones."""
    page = ExamPage.objects.create(
        organization=organization,
        batch=batch,
        storage_ref=f"scan/{uuid.uuid4().hex[:8]}.png",
        status=PageStatus.RECOGNIZED,
    )
    page.recognized_data = {
        "qr_payload": {
            "valid": True,
            "exam_id": str(exam.pk),
            "model_id": str(model.pk),
            "page_number": page_number,
            "org_id": str(organization.pk),
            "checksum": "test",
        },
        "zones": [
            {
                "attribute": "name",
                "value": ocr_name,
                "zone_type": "OCR_TEXT",
                "confidence": 0.9,
            },
            {
                "attribute": "nia",
                "value": ocr_nia,
                "zone_type": "OCR_NUMBER",
                "confidence": 0.9,
            },
            {
                "attribute": "dni",
                "value": ocr_dni,
                "zone_type": "OCR_TEXT",
                "confidence": 0.9,
            },
        ],
    }
    page.save()
    return page


# ── Tests ──────────────────────────────────────────────────────────


def test_pages_of_same_batch_assemble_into_one_instance():
    """Three pages in one batch end up in a single instance regardless
    of the order in which they were created.

    In v2 the batch_id is the strongest a-priori signal that pages
    belong together, so it is the lot boundary. Creation order is
    irrelevant: ``_group_pages_into_lots`` groups by batch_id and the
    assignment is done on the lot as a whole.
    """
    setup = _build_three_page_exam_with_one_student()
    org, exam, model = setup["org"], setup["exam"], setup["model"]
    batch = _make_batch(org)

    # Insert "out of order".
    _make_recognized_page(organization=org, exam=exam, model=model, page_number=3, batch=batch)
    _make_recognized_page(organization=org, exam=exam, model=model, page_number=2, batch=batch)
    _make_recognized_page(organization=org, exam=exam, model=model, page_number=1, batch=batch)

    assemble_exam(str(exam.pk))

    instances = list(ExamInstance.unfiltered.filter(exam=exam, student__isnull=False))
    assert len(instances) == 1, "Pages of one batch must collapse to one instance."
    instance = instances[0]
    assert instance.student_id == setup["student"].pk
    assert instance.pages.count() == 3
    # Page numbers come from the QR payload, not from creation order.
    assert sorted(p.page_number for p in instance.pages.all()) == [1, 2, 3]
    assert instance.status == InstanceStatus.RECEIVED


def test_duplicate_page_number_does_not_falsely_complete_assembly():
    """Pages 1, 2, 2 in one batch must NOT advance the instance to RECEIVED.

    All three pages attach to the same instance (the lot is one
    student's exam), but completion is supposed to be measured by
    distinct page numbers — page 3 is still missing. If the test fails
    by transitioning to RECEIVED, ``check_assembling_complete`` is
    counting attached pages and not deduplicating by ``page_number``.
    """
    setup = _build_three_page_exam_with_one_student()
    org, exam, model = setup["org"], setup["exam"], setup["model"]
    batch = _make_batch(org)

    _make_recognized_page(organization=org, exam=exam, model=model, page_number=1, batch=batch)
    _make_recognized_page(organization=org, exam=exam, model=model, page_number=2, batch=batch)
    _make_recognized_page(organization=org, exam=exam, model=model, page_number=2, batch=batch)

    assemble_exam(str(exam.pk))

    instance = ExamInstance.unfiltered.get(exam=exam, student__isnull=False)
    assert instance.pages.count() == 3, "All three pages must be attached to the instance."
    assert instance.status == InstanceStatus.ASSEMBLING, (
        "Two copies of page 2 must not be counted as 'page 2 + page 3'. "
        "Instance prematurely advanced to RECEIVED — check_assembling_complete "
        "is mistaking duplicates for distinct pages."
    )


def test_extra_page_in_batch_attaches_without_failing():
    """A batch with one more page than the model declares attaches every
    page; v2 does not flag extras as EXTRA_PAGE.

    This test pins down current behaviour explicitly. The legacy
    per-page assembler marked ``page_number > expected_pages`` as
    ``EXTRA_PAGE``; v2 attaches every page in the lot without
    page-level inspection. If you want EXTRA_PAGE detection back, add
    it inside ``_persist_lot`` in ``assembler_v2.py``.
    """
    setup = _build_three_page_exam_with_one_student()
    org, exam, model = setup["org"], setup["exam"], setup["model"]
    batch = _make_batch(org)

    for n in (1, 2, 3, 4):
        _make_recognized_page(
            organization=org,
            exam=exam,
            model=model,
            page_number=n,
            batch=batch,
        )

    assemble_exam(str(exam.pk))

    instance = ExamInstance.unfiltered.get(exam=exam, student__isnull=False)
    assert instance.pages.count() == 4, "All four pages must be attached."
    # Received >= expected → transitioned to RECEIVED.
    assert instance.status == InstanceStatus.RECEIVED


def test_page_with_qr_for_unknown_exam_is_not_processed():
    """A page whose QR points to a different exam is left alone.

    ``_load_pending_pages`` filters by ``qr_payload.exam_id``, so
    foreign pages are simply not in scope for an ``assemble_exam(other)``
    run. They remain instance-less and RECOGNIZED, waiting for an
    assembly of their own exam — which will never come if their
    exam_id is forged or stale. Either way the assembler does not
    crash and does not mis-attach them.
    """
    setup = _build_three_page_exam_with_one_student()
    org, exam = setup["org"], setup["exam"]

    fake_exam_id = uuid.uuid4()
    page = ExamPage.objects.create(
        organization=org,
        storage_ref="scan/fake.png",
        status=PageStatus.RECOGNIZED,
    )
    page.recognized_data = {
        "qr_payload": {
            "valid": True,
            "exam_id": str(fake_exam_id),  # not the real exam
            "model_id": str(uuid.uuid4()),
            "page_number": 1,
            "org_id": str(org.pk),
        },
        "zones": [],
    }
    page.save()

    assemble_exam(str(exam.pk))

    page.refresh_from_db()
    assert page.instance_id is None, "Foreign page must not be attached."
    assert page.status == PageStatus.RECOGNIZED, "Foreign page must not be re-statused."


def test_complete_assembly_three_pages_in_order():
    """Sanity: the happy path through ``assemble_exam`` works end-to-end.

    Catches regressions where edge-case fixes break the base case.
    """
    setup = _build_three_page_exam_with_one_student()
    org, exam, model = setup["org"], setup["exam"], setup["model"]
    batch = _make_batch(org)

    for n in (1, 2, 3):
        _make_recognized_page(
            organization=org,
            exam=exam,
            model=model,
            page_number=n,
            batch=batch,
        )

    assemble_exam(str(exam.pk))

    instance = ExamInstance.unfiltered.get(exam=exam, student__isnull=False)
    assert instance.pages.count() == 3
    assert instance.status == InstanceStatus.RECEIVED
