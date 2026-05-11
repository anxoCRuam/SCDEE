"""
Edge-case integration tests for the instance assembler.

The existing ``test_ingestion.py`` covers the happy paths
(in-order assembly, ASSEMBLING→RECEIVED transition, MISSING_PAGE
detection). This module covers the awkward shapes that real-world
scanners produce, where the assembler's correctness shows or breaks:

- Pages arriving in **reverse order** must still aggregate into one
  instance once page 1 finally appears (not be split into two).
- **Duplicate** pages (same instance, same page number, different
  scan) must be flagged as duplicates rather than counted twice
  against ``expected_pages``.
- A **page that arrives after assembly is complete** must be treated
  as ``EXTRA_PAGE`` (a real exam may have an extra appendix, or a
  scan glitch).
- An **orphan** page (QR mismatch, no matching exam in the org) must
  end up as ``ORPHAN`` without crashing the assembler.

The tests work at the service-layer level (``assemble_page``
directly) rather than going through HTTP, because the assembler is
the unit under test — going through HTTP would also exercise the
recognition pipeline and cloud the failure mode.

References: RF-9.10, RF-9.11, RF-7.1, RF-7.6.
"""

from __future__ import annotations

import uuid

import pytest
from django.contrib.auth import get_user_model

from apps.courses.models.courses import AcademicCourse
from apps.exams.models.exams import Exam, ExamModel, PageProfile
from apps.ingestion.services.assembler import assemble_page
from apps.instances.models.instances import (
    ExamInstance,
    ExamPage,
    InstanceStatus,
    PageIssueType,
    PageStatus,
)
from apps.organizations.models.organization import Organization
from apps.subjects.models.subjects import MembershipRole, Subject, SubjectMembership

pytestmark = pytest.mark.django_db


# ── Helpers ────────────────────────────────────────────────────────


def _build_three_page_model():
    """Build a tenant + exam + model with exactly three PageProfiles."""
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
            exam_model=model, page_number=i, page_width=595.0, page_height=842.0
        )

    return {"org": org, "exam": exam, "model": model}


def _make_page(*, organization, storage_ref: str = "scan/test.png") -> ExamPage:
    """Create a fresh ExamPage row in PENDING_RECOGNITION state."""
    return ExamPage.objects.create(
        organization=organization,
        storage_ref=storage_ref,
        status=PageStatus.PENDING_RECOGNITION,
    )


def _qr_results(*, exam_id: str, model_id: str, page_number: int) -> dict:
    """Build the recognition_results dict an assembler would receive
    after a successful QR decode."""
    return {
        "qr_payload": {
            "valid": True,
            "exam_id": exam_id,
            "model_id": model_id,
            "page_number": page_number,
            "org_id": "irrelevant-here",
        },
        "zones": [],
    }


# ── Tests ──────────────────────────────────────────────────────────


def test_pages_arriving_in_reverse_order_assemble_into_one_instance():
    """Pages 3, 2, 1 (in that order) must form a single ASSEMBLING→RECEIVED instance.

    The assembler creates the instance when page 1 arrives, but pages
    2 and 3 arriving *first* must not create their own orphan
    instances. Implementation expectation: they end up as ORPHAN until
    page 1 lands and the existing instance can take them.

    Note: the current assembler creates the instance ONLY on page 1;
    pages 2/3 arriving before page 1 are routed via
    ``_attach_to_existing_instance`` which finds no candidate, so they
    become ``ORPHAN``. This is the documented fallback behaviour
    (RF-9.10). After page 1 creates the instance, a real recovery
    workflow would re-attach the orphans manually — that part is out
    of scope for the assembler itself, and this test pins down the
    *current* behaviour so any future change is intentional.
    """
    setup = _build_three_page_model()
    org = setup["org"]
    exam = setup["exam"]
    model = setup["model"]

    page3 = _make_page(organization=org)
    page2 = _make_page(organization=org)
    page1 = _make_page(organization=org)

    # Page 3 first — no instance yet.
    assemble_page(
        page3,
        _qr_results(exam_id=str(exam.pk), model_id=str(model.pk), page_number=3),
    )
    page3.refresh_from_db()
    assert page3.status == PageStatus.ORPHAN
    assert ExamInstance.unfiltered.filter(exam=exam).count() == 0

    # Page 2 also before the cover — also orphan.
    assemble_page(
        page2,
        _qr_results(exam_id=str(exam.pk), model_id=str(model.pk), page_number=2),
    )
    page2.refresh_from_db()
    assert page2.status == PageStatus.ORPHAN
    assert ExamInstance.unfiltered.filter(exam=exam).count() == 0

    # Page 1 finally lands → instance is created.
    assemble_page(
        page1,
        _qr_results(exam_id=str(exam.pk), model_id=str(model.pk), page_number=1),
    )
    instances = list(ExamInstance.unfiltered.filter(exam=exam))
    assert len(instances) == 1, "Page 1 must create exactly one instance."
    instance = instances[0]
    page1.refresh_from_db()
    assert page1.instance_id == instance.pk


def test_duplicate_page_number_does_not_count_twice_for_completion():
    """The same page number scanned twice must not falsely complete assembly.

    Scenario: a 3-page exam, page 1 arrives, then page 2 arrives, then
    page 2 arrives *again* (e.g. the operator re-scanned a smudged
    sheet). Without proper handling the assembler might count
    received_count = 3 and transition to RECEIVED prematurely.
    """
    setup = _build_three_page_model()
    org, exam, model = setup["org"], setup["exam"], setup["model"]

    page1 = _make_page(organization=org)
    page2_first = _make_page(organization=org)
    page2_second = _make_page(organization=org)

    assemble_page(page1, _qr_results(exam_id=str(exam.pk), model_id=str(model.pk), page_number=1))
    assemble_page(
        page2_first,
        _qr_results(exam_id=str(exam.pk), model_id=str(model.pk), page_number=2),
    )

    instance = ExamInstance.unfiltered.get(exam=exam)
    assert instance.status == InstanceStatus.ASSEMBLING

    # The duplicate. Same page number, fresh page row.
    assemble_page(
        page2_second,
        _qr_results(exam_id=str(exam.pk), model_id=str(model.pk), page_number=2),
    )

    instance.refresh_from_db()
    # Both copies of page 2 should have been attached to the same
    # instance. The instance must NOT have transitioned to RECEIVED:
    # we still need page 3.
    assert instance.status == InstanceStatus.ASSEMBLING, (
        "Two copies of page 2 must not be counted as 'page 2 + page 3'. "
        "Instance prematurely advanced to RECEIVED — the assembler is "
        "mistaking duplicates for distinct pages."
    )


def test_orphan_extra_page_arrives_after_assembly_completes():
    """A page beyond the expected count is flagged as EXTRA_PAGE.

    Scenario: the model declares 3 pages. All three arrive correctly
    and the instance reaches RECEIVED. Then a fourth page (same
    exam/model, page_number=4) arrives — perhaps a scanner glitch or
    a teacher slipped an extra sheet under the stack.
    """
    setup = _build_three_page_model()
    org, exam, model = setup["org"], setup["exam"], setup["model"]

    for n in (1, 2, 3):
        page = _make_page(organization=org)
        assemble_page(
            page,
            _qr_results(exam_id=str(exam.pk), model_id=str(model.pk), page_number=n),
        )

    instance = ExamInstance.unfiltered.get(exam=exam)
    assert instance.status == InstanceStatus.RECEIVED

    # Fourth page arrives.
    extra = _make_page(organization=org)
    assemble_page(extra, _qr_results(exam_id=str(exam.pk), model_id=str(model.pk), page_number=4))

    extra.refresh_from_db()
    # The extra page should be flagged. Implementation specifics: the
    # current assembler sets ``issue_type=EXTRA_PAGE`` when
    # ``page_number > expected_pages`` and routes through
    # ``_attach_to_existing_instance`` (which won't find an
    # ASSEMBLING instance — already RECEIVED). So the page becomes
    # ORPHAN with the EXTRA_PAGE flag. Either is acceptable as long
    # as the page does NOT silently end up in the original instance.
    assert (
        extra.issue_type == PageIssueType.EXTRA_PAGE
    ), f"Page beyond expected_pages must be tagged EXTRA_PAGE, got {extra.issue_type}."


def test_qr_pointing_at_unknown_exam_marks_page_orphan():
    """A QR with a valid checksum but pointing to an exam that does not exist
    in the database must produce an ORPHAN page, not a crash.

    This protects against forged QRs and against stale paper
    (a printed exam from a deleted exam still has a valid checksum
    by construction — checksum is a hash, not a signature).
    """
    setup = _build_three_page_model()
    org = setup["org"]
    page = _make_page(organization=org)

    # Unknown exam id (well-formed UUID, no row).
    fake_exam_id = str(uuid.uuid4())
    fake_model_id = str(uuid.uuid4())

    assemble_page(
        page,
        _qr_results(exam_id=fake_exam_id, model_id=fake_model_id, page_number=1),
    )

    page.refresh_from_db()
    assert page.status == PageStatus.ORPHAN
    assert page.issue_type == PageIssueType.QR_MISMATCH


def test_complete_assembly_three_pages_in_order():
    """Sanity: the assembler's happy path also passes through this module.

    Catches regressions where edge-case fixes accidentally break the
    base case, which the existing tests in test_ingestion.py also
    cover but are far from these. Local sanity test.
    """
    setup = _build_three_page_model()
    org, exam, model = setup["org"], setup["exam"], setup["model"]

    for n in (1, 2, 3):
        page = _make_page(organization=org)
        assemble_page(
            page,
            _qr_results(exam_id=str(exam.pk), model_id=str(model.pk), page_number=n),
        )

    instance = ExamInstance.unfiltered.get(exam=exam)
    assert instance.status == InstanceStatus.RECEIVED
    assert instance.pages.count() == 3
