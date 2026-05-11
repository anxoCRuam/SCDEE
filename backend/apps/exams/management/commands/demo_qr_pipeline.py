"""
Demo command: end-to-end QR pipeline.

Goal — let a human verify visually that:

1. A blank PDF can be uploaded and registered as an :class:`ExamModel`.
2. A QR :class:`RecognitionZone` placed on a :class:`PageProfile`
   produces a correctly-stamped *instrumented* PDF.
3. Rendering that instrumented PDF as an image and feeding it through
   the production :class:`QRRecognizer` yields the original payload
   (organisation, exam, model, page number, valid checksum).

In other words: this is the loop a real scanner-fed page would go
through, minus the actual scanner. If something breaks anywhere along
the chain, the command fails loudly and dumps every intermediate
artefact (blank PDF, instrumented PDF, rendered PNG) to a directory
the operator can open in any PDF/image viewer.

Why a management command, not just a pytest?
    There is also a pytest counterpart (see
    ``apps.exams.tests.test_qr_pipeline_demo``) that exercises the
    same logic in CI. The command exists so the operator can *see*
    the artefacts during manual verification — the QR position, the
    rendered image quality, the readability of the code on paper,
    etc. Tests assert; demos illustrate.

Run::

    python manage.py demo_qr_pipeline --output-dir=/tmp/scdee-qr-demo

Cleanup is the operator's responsibility: the command does not delete
the temporary ``Organization``, ``Exam``, ``ExamModel`` rows it
creates. That's intentional — leaving them around lets you poke at
them through the admin or the DRF endpoints.

References: RF-6.15, RF-9.3.
"""

from __future__ import annotations

import io
import logging
import sys
from pathlib import Path
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

logger = logging.getLogger(__name__)


# A4 in points (1 pt = 1/72 inch). reportlab's ``A4`` import would
# have introduced an extra dependency edge, and these are universal
# constants.
_A4_POINTS = (595.0, 842.0)


class Command(BaseCommand):
    """Walk a blank PDF through the QR instrumentation + decoding loop."""

    help = (
        "Generate a blank PDF, instrument it with a QR zone, render the "
        "instrumented PDF as an image, decode the QR, and verify the "
        "round-trip. All intermediate artefacts are dropped in the "
        "output directory for visual inspection."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--output-dir",
            required=True,
            type=str,
            help=(
                "Directory where the demo writes blank.pdf, "
                "instrumented.pdf and rendered_page1.png."
            ),
        )
        parser.add_argument(
            "--qr-x",
            type=float,
            default=440.0,
            help="QR zone X coordinate in PDF points (default: 440).",
        )
        parser.add_argument(
            "--qr-y",
            type=float,
            default=740.0,
            help="QR zone Y coordinate in PDF points from bottom (default: 740).",
        )
        parser.add_argument(
            "--qr-size",
            type=float,
            default=80.0,
            help="QR zone side length in PDF points (default: 80).",
        )
        parser.add_argument(
            "--keep",
            action="store_true",
            help=(
                "Do not roll back the demo Organization/Exam/Model rows "
                "after the run. Useful if you want to keep poking at "
                "them via the admin."
            ),
        )

    # ── Entry point ────────────────────────────────────────────

    def handle(self, *args, **options) -> None:
        output_dir = Path(options["output_dir"]).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        qr_x = options["qr_x"]
        qr_y = options["qr_y"]
        qr_size = options["qr_size"]
        keep = options["keep"]

        self.stdout.write(self.style.MIGRATE_HEADING("SCDEE QR pipeline demo"))
        self.stdout.write(f"Output directory: {output_dir}")
        self.stdout.write(f"QR zone: x={qr_x} y={qr_y} size={qr_size}x{qr_size} (PDF points)")
        self.stdout.write("")

        # We wrap the run in a transaction so ``--no-keep`` is the
        # default-clean behaviour: if anything fails we roll back, and
        # if everything succeeds we ALSO roll back unless ``--keep``.
        try:
            with transaction.atomic():
                outcome = self._run(
                    output_dir=output_dir,
                    qr_x=qr_x,
                    qr_y=qr_y,
                    qr_size=qr_size,
                )
                if not keep:
                    # Force rollback of the temporary rows.
                    transaction.set_rollback(True)
        except _DemoFailureError as exc:
            self.stderr.write(self.style.ERROR(f"FAILED: {exc}"))
            sys.exit(2)
        except Exception as exc:  # noqa: BLE001 — we want a clean diagnostic
            self.stderr.write(self.style.ERROR(f"FAILED with unexpected error: {exc}"))
            raise

        self._render_outcome(outcome, output_dir, keep=keep)

    # ── Pipeline ───────────────────────────────────────────────

    def _run(
        self,
        *,
        output_dir: Path,
        qr_x: float,
        qr_y: float,
        qr_size: float,
    ) -> dict:
        """Execute the full pipeline. Returns a summary dict."""
        # Late imports: Django settings must be loaded first.
        from django.contrib.auth import get_user_model

        from apps.courses.models.courses import AcademicCourse
        from apps.exams.models.exams import (
            Exam,
            ExamModel,
            PageProfile,
            RecognitionZone,
            ZoneType,
        )
        from apps.exams.services.instrumented_pdf import (
            generate_instrumented_pdf,
        )
        from apps.exams.services.storage import upload_to_minio
        from apps.ingestion.recognizers.qr import QRRecognizer
        from apps.organizations.models.organization import Organization
        from apps.subjects.models.subjects import MembershipRole, Subject, SubjectMembership

        # 1. Create the demo entity tree. The relational graph here
        #    mirrors what a real organisation looks like — Organization
        #    → AcademicCourse → Subject (with coordinator) → Exam → Model
        #    — because anything less violates database constraints.
        suffix = uuid4().hex[:8]
        user_model = get_user_model()

        org = Organization.objects.create(
            name=f"DemoOrg-{suffix}",
            subdomain=f"demoorg{suffix}",
        )
        coordinator = user_model.objects.create_user(
            email=f"demo-{suffix}@scdee.local",
            password="Demo!Pass1",  # noqa: S106 — throwaway demo user
            first_name="Demo",
            last_name="Coord",
            organization=org,
            is_staff=True,
        )
        course = AcademicCourse.objects.create(
            organization=org,
            label="2026",
            is_active=True,
        )
        subject = Subject.objects.create(
            organization=org,
            name="Demo Subject",
            code=f"DEMO{suffix}",
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
        exam = Exam.objects.create(
            organization=org,
            subject=subject,
            name=f"Demo Exam {suffix}",
        )

        # 2. Generate a blank A4 PDF.
        page_w, page_h = _A4_POINTS
        blank_pdf_bytes = self._make_blank_pdf(page_w=page_w, page_h=page_h)
        (output_dir / "blank.pdf").write_bytes(blank_pdf_bytes)

        # 3. Upload the blank PDF to MinIO and register it on the model.
        blank_key = f"{org.pk}/exams/{exam.pk}/models/demo/blank.pdf"
        upload_to_minio(key=blank_key, data=blank_pdf_bytes)

        model = ExamModel.objects.create(
            exam=exam,
            label="A",
            blank_pdf_ref=blank_key,
            blank_pdf_pages=1,
        )

        # 4. Define a single PageProfile + QR zone.
        profile = PageProfile.objects.create(
            exam_model=model,
            page_number=1,
            page_width=page_w,
            page_height=page_h,
        )
        zone = RecognitionZone.objects.create(
            page_profile=profile,
            zone_type=ZoneType.QR,
            attribute="exam_qr",
            x=qr_x,
            y=qr_y,
            width=qr_size,
            height=qr_size,
        )

        # 5. Instrument: overlay the QR on the blank PDF.
        instrumented_pdf_bytes = generate_instrumented_pdf(model)
        (output_dir / "instrumented.pdf").write_bytes(instrumented_pdf_bytes)

        # 6. Render page 1 of the instrumented PDF as a PNG. This is
        #    the closest thing to "what a scanner would feed us" we
        #    can produce without an actual printer + scanner roundtrip.
        rendered_png = self._render_pdf_page_to_png(
            pdf_bytes=instrumented_pdf_bytes,
            page_index=0,
        )
        (output_dir / "rendered_page1.png").write_bytes(rendered_png)

        # 7. Decode the QR with the *production* recogniser.
        recognizer = QRRecognizer()
        result = recognizer.recognize(rendered_png)

        # 8. Validate the round-trip.
        self._assert_round_trip(
            result=result,
            expected_org_id=str(org.pk),
            expected_exam_id=str(exam.pk),
            expected_model_id=str(model.pk),
            expected_page_number=1,
        )

        return {
            "organization_id": str(org.pk),
            "exam_id": str(exam.pk),
            "model_id": str(model.pk),
            "zone_id": str(zone.pk),
            "blank_pdf_path": output_dir / "blank.pdf",
            "instrumented_pdf_path": output_dir / "instrumented.pdf",
            "rendered_png_path": output_dir / "rendered_page1.png",
            "qr_payload": result.value,
            "qr_confidence": result.confidence,
            "qr_raw_value": result.raw_value,
        }

    # ── Helpers ────────────────────────────────────────────────

    @staticmethod
    def _make_blank_pdf(*, page_w: float, page_h: float) -> bytes:
        """Generate a minimal blank PDF with a single A4 page.

        Uses reportlab so the resulting PDF behaves like the ones the
        real product accepts (it is the same library used by
        ``instrumented_pdf.py``).
        """
        from reportlab.pdfgen import canvas as rl_canvas

        buffer = io.BytesIO()
        c = rl_canvas.Canvas(buffer, pagesize=(page_w, page_h))
        # Empty page; we want zero content so the QR overlay is the
        # only ink. A tiny watermark would make visual inspection
        # easier — uncomment the next line if you want one.
        # c.drawString(50, 50, "DEMO BLANK PAGE")
        c.showPage()
        c.save()
        return buffer.getvalue()

    @staticmethod
    def _render_pdf_page_to_png(*, pdf_bytes: bytes, page_index: int) -> bytes:
        """Render one PDF page to a PNG byte string at 200 DPI.

        200 DPI is a realistic compromise between scanner output (300+
        DPI) and processing speed. ``pdf2image`` is already a runtime
        dependency of the recognition pipeline.
        """
        from pdf2image import convert_from_bytes

        images = convert_from_bytes(
            pdf_bytes,
            dpi=200,
            first_page=page_index + 1,
            last_page=page_index + 1,
        )
        if not images:
            raise _DemoFailureError(
                f"Could not render page {page_index + 1} of the instrumented PDF."
            )

        out = io.BytesIO()
        images[0].save(out, format="PNG")
        return out.getvalue()

    @staticmethod
    def _assert_round_trip(
        *,
        result,
        expected_org_id: str,
        expected_exam_id: str,
        expected_model_id: str,
        expected_page_number: int,
    ) -> None:
        """Compare the decoded QR payload to the expected values."""
        if result.value is None:
            raise _DemoFailureError(
                "QR decode returned None. The recogniser did not detect any "
                f"QR in the rendered page. raw={result.raw_value!r}"
            )

        payload = result.value

        mismatches: list[str] = []
        for key, expected in [
            ("org_id", expected_org_id),
            ("exam_id", expected_exam_id),
            ("model_id", expected_model_id),
            ("page_number", expected_page_number),
        ]:
            actual = payload.get(key)
            if actual != expected:
                mismatches.append(f"{key}: expected {expected!r}, got {actual!r}")

        if not payload.get("valid"):
            mismatches.append("checksum invalid (payload['valid'] is False)")

        if mismatches:
            raise _DemoFailureError(
                "Round-trip verification failed:\n  - " + "\n  - ".join(mismatches)
            )

    # ── Output rendering ───────────────────────────────────────

    def _render_outcome(self, outcome: dict, output_dir: Path, *, keep: bool) -> None:
        """Print a friendly summary table and the artefact paths."""
        self.stdout.write(self.style.SUCCESS("✓ QR round-trip verified successfully."))
        self.stdout.write("")
        self.stdout.write("Decoded QR payload:")
        for key in ("org_id", "exam_id", "model_id", "page_number", "checksum", "valid"):
            value = outcome["qr_payload"].get(key)
            self.stdout.write(f"  {key:14}= {value}")
        self.stdout.write(f"  confidence    = {outcome['qr_confidence']:.2f}")
        self.stdout.write("")
        self.stdout.write("Artefacts (open in any PDF/image viewer):")
        self.stdout.write(f"  Blank PDF:         {outcome['blank_pdf_path']}")
        self.stdout.write(f"  Instrumented PDF:  {outcome['instrumented_pdf_path']}")
        self.stdout.write(f"  Rendered page 1:   {outcome['rendered_png_path']}")
        self.stdout.write("")
        if keep:
            self.stdout.write(
                self.style.WARNING(
                    f"--keep was set; database rows kept "
                    f"(organization {outcome['organization_id']}, "
                    f"exam {outcome['exam_id']})."
                )
            )
        else:
            self.stdout.write("Database rows rolled back. Use --keep to preserve them.")


class _DemoFailureError(CommandError):
    """Raised when the round-trip cannot be verified."""
