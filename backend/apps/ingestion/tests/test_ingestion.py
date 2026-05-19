"""
Tests for the ingestion and recognition pipeline (RF-9).

Tests cover:
1. QR recognizer payload validation and checksum.
2. Checkbox recognizer binary detection.
3. Student matching (Levenshtein, OCR tolerance).
4. Instance assembly from pages.
5. Temporal proximity fallback.
6. MISSING_PAGE detection.
7. State machine integration (ASSEMBLING → RECEIVED).
"""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from django.test import TestCase, override_settings
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.authentication import reset_auth_plugin
from apps.courses.models.courses import AcademicCourse
from apps.exams.models.exams import Exam, ExamModel, PageProfile
from apps.instances.models.instances import (
    ExamInstance,
    ExamPage,
    InstanceIssueType,
    InstanceStatus,
)
from apps.organizations.models.organization import Organization
from apps.subjects.models.subjects import MembershipRole, Subject, SubjectMembership

pytestmark = pytest.mark.django_db
VALID_KEY = "a" * 64


# ── Helpers ──────────────────────────────────────────────────


def _full_setup():
    from django.contrib.auth import get_user_model

    user_model = get_user_model()
    org = Organization.objects.create(name="Org", subdomain=f"o-{uuid.uuid4().hex[:8]}")
    manager = user_model.objects.create_user(
        email=f"m-{uuid.uuid4().hex[:8]}@x.com",
        password="MgrPass123!",  # noqa: S106
        first_name="Mgr",
        last_name="U",
        organization=org,
        is_staff=True,
    )
    course = AcademicCourse(organization=org, label="2025", is_active=True)
    course.save()
    coord = user_model.objects.create_user(
        email=f"c-{uuid.uuid4().hex[:8]}@x.com",
        password="Pass123!",  # noqa: S106
        first_name="C",
        last_name="U",
        organization=org,
    )
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

    exam = Exam.objects.create(organization=org, name="Exam", subject=subject)
    model = ExamModel.objects.create(label="A", exam=exam)

    return {
        "org": org,
        "manager": manager,
        "exam": exam,
        "model": model,
        "subject": subject,
        "coord": coord,
    }


def _auth(user, pw="MgrPass123!"):
    reset_auth_plugin()
    c = APIClient()
    r = c.post("/api/v1/auth/login/", {"email": user.email, "password": pw}, format="json")
    c.credentials(HTTP_AUTHORIZATION=f"Bearer {r.data['access_token']}")
    return c


# ── QR Recognizer tests ─────────────────────────────────────


class TestQRRecognizer:
    """Test QR recognizer checksum logic."""

    def test_checksum_computation(self):
        from apps.ingestion.recognizers.qr import QRRecognizer

        recognizer = QRRecognizer()
        checksum = recognizer._compute_checksum("org1", "exam1", "model1", 1)

        raw = "org1:exam1:model1:1"
        expected = hashlib.sha256(raw.encode()).hexdigest()[:8]
        assert checksum == expected

    def test_checksum_changes_with_page(self):
        from apps.ingestion.recognizers.qr import QRRecognizer

        recognizer = QRRecognizer()
        c1 = recognizer._compute_checksum("org", "exam", "model", 1)
        c2 = recognizer._compute_checksum("org", "exam", "model", 2)
        assert c1 != c2


# ── Checkbox Recognizer tests ────────────────────────────────


class TestCheckboxRecognizer:
    """Test checkbox pixel density analysis."""

    def test_mostly_white_is_unmarked(self):
        """An almost-white image should be detected as unmarked."""
        import io

        from PIL import Image

        from apps.ingestion.recognizers.checkbox import CheckboxRecognizer

        # Create a white image.
        img = Image.new("L", (50, 50), color=255)
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        recognizer = CheckboxRecognizer()
        result = recognizer.recognize(buf.getvalue())

        assert result.value is False

    def test_mostly_dark_is_marked(self):
        """A mostly dark image should be detected as marked."""
        import io

        from PIL import Image

        from apps.ingestion.recognizers.checkbox import CheckboxRecognizer

        # Create a dark image.
        img = Image.new("L", (50, 50), color=30)
        buf = io.BytesIO()
        img.save(buf, format="PNG")

        recognizer = CheckboxRecognizer()
        result = recognizer.recognize(buf.getvalue())

        assert result.value is True


# ── Student matching tests ───────────────────────────────────


class TestStudentMatching:
    """Tests for the scoring primitives used by the v2 matcher.

    The legacy private helpers (``_ocr_tolerant_match``,
    ``_name_similarity``, ``_levenshtein_distance``) were replaced by a
    single OCR-aware similarity (``similarity_ocr_aware``) plus a
    ``ScoringStrategy`` that combines per-attribute scores. These tests
    target the new public API in ``apps.ingestion.services.scoring``.
    """

    def test_similarity_exact_match(self):
        from apps.ingestion.services.scoring import similarity_ocr_aware

        assert similarity_ocr_aware("12345678X", "12345678X") == 1.0

    def test_similarity_handles_zero_o_confusion(self):
        """'O' vs '0' is folded into one canonical character.

        EasyOCR routinely confuses these in handwriting, so the
        substitution is treated as zero-cost.
        """
        from apps.ingestion.services.scoring import similarity_ocr_aware

        assert similarity_ocr_aware("1234567OX", "12345670X") == 1.0

    def test_similarity_low_for_distinct_strings(self):
        from apps.ingestion.services.scoring import similarity_ocr_aware

        # Neither string folds into the other under any OCR confusion class.
        assert similarity_ocr_aware("ABCDEFGH", "12345678") < 0.3

    def test_similarity_name_close(self):
        """'l' vs 'i' is folded into one canonical character.

        Both are in the I1lL| confusion class, so 'Garcla' and 'Garcia'
        collapse to identical canonical forms.
        """
        from apps.ingestion.services.scoring import similarity_ocr_aware

        assert similarity_ocr_aware("Juan Garcla", "Juan Garcia") == 1.0

    def test_similarity_name_different(self):
        from apps.ingestion.services.scoring import similarity_ocr_aware

        score = similarity_ocr_aware("Pedro López", "María Sánchez")
        assert score < 0.5

    def test_score_lot_perfect_triplet(self):
        """The default strategy returns 1.0 for an exact (name, NIA, DNI)."""
        from apps.ingestion.services.scoring import DEFAULT_STRATEGY

        score = DEFAULT_STRATEGY.score_lot(
            ocr_values={
                "name": ["Juan Garcia"],
                "nia": ["123456"],
                "dni": ["11111111H"],
            },
            true_name="Juan Garcia",
            true_nia="123456",
            true_dni="11111111H",
        )
        assert score == 1.0

    def test_score_lot_unrelated_triplet(self):
        from apps.ingestion.services.scoring import DEFAULT_STRATEGY

        score = DEFAULT_STRATEGY.score_lot(
            ocr_values={
                "name": ["Pedro Lopez"],
                "nia": ["999999"],
                "dni": ["99999999Z"],
            },
            true_name="Juan Garcia",
            true_nia="123456",
            true_dni="11111111H",
        )
        assert score < 0.3

    def test_score_lot_picks_best_across_multi_page_values(self):
        """Multi-page lots have several OCR values per attribute.

        The strategy takes the maximum similarity per attribute, so the
        best page-level evidence wins. A noisy first read does not
        sabotage a clean second read.
        """
        from apps.ingestion.services.scoring import DEFAULT_STRATEGY

        score = DEFAULT_STRATEGY.score_lot(
            ocr_values={
                "name": ["completely wrong", "Juan Garcia"],
                "nia": ["123456"],
                "dni": ["11111111H"],
            },
            true_name="Juan Garcia",
            true_nia="123456",
            true_dni="11111111H",
        )
        assert score == 1.0


# ── Assembly tests ───────────────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestAssembly(TestCase):
    """Test instance assembly from pages."""

    def test_assembling_to_received_on_complete(self):
        """Instance transitions ASSEMBLING → RECEIVED when all pages arrive."""
        ctx = _full_setup()

        # Create 2 page profiles → expect 2 pages.
        PageProfile.objects.create(exam_model=ctx["model"], page_number=1)
        PageProfile.objects.create(exam_model=ctx["model"], page_number=2)

        instance = ExamInstance.objects.create(
            organization=ctx["org"],
            exam=ctx["exam"],
            model=ctx["model"],
            status=InstanceStatus.ASSEMBLING,
            expected_pages=2,
        )

        # Add 2 pages.
        ExamPage.objects.create(instance=instance, page_number=1, storage_ref="t/1.png")
        ExamPage.objects.create(instance=instance, page_number=2, storage_ref="t/2.png")

        from apps.instances.services.instance_service import check_assembling_complete

        transitioned = check_assembling_complete(instance)
        assert transitioned is True
        instance.refresh_from_db()
        assert instance.status == InstanceStatus.RECEIVED

    def test_no_transition_when_incomplete(self):
        """Instance stays ASSEMBLING when pages are missing."""
        ctx = _full_setup()

        instance = ExamInstance.objects.create(
            organization=ctx["org"],
            exam=ctx["exam"],
            model=ctx["model"],
            status=InstanceStatus.ASSEMBLING,
            expected_pages=3,
        )
        ExamPage.objects.create(instance=instance, page_number=1, storage_ref="t/1.png")

        from apps.instances.services.instance_service import check_assembling_complete

        transitioned = check_assembling_complete(instance)
        assert transitioned is False
        instance.refresh_from_db()
        assert instance.status == InstanceStatus.ASSEMBLING


# ── MISSING_PAGE detection ───────────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY, INGESTION_MISSING_PAGE_TIMEOUT_MINUTES=0)
class TestMissingPageDetection(TestCase):
    """Test the periodic MISSING_PAGE detection task."""

    def test_detects_stalled_instances(self):
        ctx = _full_setup()

        instance = ExamInstance.objects.create(
            organization=ctx["org"],
            exam=ctx["exam"],
            model=ctx["model"],
            status=InstanceStatus.ASSEMBLING,
            expected_pages=3,
        )
        # Backdate creation to simulate timeout.
        ExamInstance.objects.filter(pk=instance.pk).update(
            created_at=datetime.now(tz=UTC) - timedelta(minutes=15)
        )

        from apps.ingestion.tasks import detect_missing_pages

        result = detect_missing_pages()
        assert result["stalled_instances"] >= 1

        instance.refresh_from_db()
        assert InstanceIssueType.MISSING_PAGE in instance.issue_types
        assert instance.has_issues is True

    def test_ignores_non_assembling(self):
        ctx = _full_setup()

        ExamInstance.objects.create(
            organization=ctx["org"],
            exam=ctx["exam"],
            model=ctx["model"],
            status=InstanceStatus.RECEIVED,
            expected_pages=3,
        )

        from apps.ingestion.tasks import detect_missing_pages

        result = detect_missing_pages()
        assert result["stalled_instances"] == 0


# ── OCR engine listing endpoint ──────────────────────────────


@override_settings(ENCRYPTION_MASTER_KEY=VALID_KEY)
class TestOCREngineEndpoint(TestCase):
    def test_list_engines(self):
        ctx = _full_setup()
        client = _auth(ctx["manager"])

        resp = client.get("/api/v1/recognition/engines/")
        assert resp.status_code == status.HTTP_200_OK
        assert isinstance(resp.data, list)
