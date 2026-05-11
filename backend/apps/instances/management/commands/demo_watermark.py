"""
Demo command: dynamic watermark visualisation.

Goal — let a human verify visually that the dynamic watermark applied
by :class:`WatermarkService` is:

1. Legible enough to be a deterrent (operator must read the student
   name + NIA + date when looking at the page).
2. Unobtrusive enough to keep the underlying content readable.
3. Repeated across the page so cropping does not remove it.
4. Applied correctly to every page of a multi-page document.

Modes:
    synthetic      — synthetic single-page image (fast, no deps)
    file           — local file (PDF or image)
    minio          — file stored in MinIO (by object key)
    instance       — compose a full ExamInstance PDF
    instance-pages — each page of an instance, separately

Output formats: png, pdf, both.

Run::

    # Synthetic image (fast):
    python manage.py demo_watermark --output=/tmp/wm.png

    # Local PDF, all pages, grid watermark, output as PDF:
    python manage.py demo_watermark \\
        --mode=file \\
        --input=/path/to/exam.pdf \\
        --format=pdf \\
        --output=/tmp/wm.pdf \\
        --student-name="Anxo Canay" --student-nia="123456"

    # Local PDF, pages 1 and 3 only, with and without watermark:
    python manage.py demo_watermark \\
        --mode=file \\
        --input=/path/to/exam.pdf \\
        --pages=1,3 \\
        --no-watermark \\
        --output=/tmp/wm/

    # MinIO object, diagonal style, PNG output:
    python manage.py demo_watermark \\
        --mode=minio \\
        --minio-key=exams/.../page.png \\
        --style=diagonal \\
        --output=/tmp/wm.png

    # Real instance, all pages separately, compare watermark on/off:
    python manage.py demo_watermark \\
        --mode=instance-pages \\
        --instance-id=<uuid> \\
        --no-watermark \\
        --output=/tmp/instance_wm/

References: RF-16.6.
"""

from __future__ import annotations

import io
import logging
import sys
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════════
# Constants
# ════════════════════════════════════════════════════════════════════

_DEMO_PAGE_WIDTH_PX = 1240  # ≈ A4 width at 150 DPI
_DEMO_PAGE_HEIGHT_PX = 1754  # ≈ A4 height at 150 DPI

_VALID_MODES = {"synthetic", "file", "minio", "instance", "instance-pages"}
_VALID_STYLES = {"grid", "diagonal", "center_stamp", "bottom_right", "cross"}
_VALID_FORMATS = {"png", "pdf", "both"}


class Command(BaseCommand):
    """Render watermarked sample page(s) from various sources."""

    help = (
        "Generate watermarked page(s) so you can inspect visually that "
        "the watermark is readable, repeated across every page, and not "
        "obscuring content. Supports multiple input sources and output "
        "formats."
    )

    def add_arguments(self, parser) -> None:
        # ── Input source ─────────────────────────────────────
        parser.add_argument(
            "--mode",
            choices=sorted(_VALID_MODES),
            default="synthetic",
            help="Input source: synthetic (default), file, minio, instance, instance-pages.",
        )
        parser.add_argument(
            "--input",
            type=str,
            default=None,
            help="Path to local file (required for --mode=file).",
        )
        parser.add_argument(
            "--minio-key",
            type=str,
            default=None,
            help="MinIO object key (required for --mode=minio).",
        )
        parser.add_argument(
            "--instance-id",
            type=str,
            default=None,
            help="UUID of the ExamInstance (required for --mode=instance and "
            "--mode=instance-pages).",
        )

        # ── Watermark configuration ──────────────────────────
        parser.add_argument(
            "--style",
            choices=sorted(_VALID_STYLES),
            default="grid",
            help="Watermark visual style (default: grid).",
        )
        parser.add_argument(
            "--no-watermark",
            action="store_true",
            default=False,
            help="Also generate a clean version (without watermark) for comparison.",
        )
        parser.add_argument(
            "--student-name",
            default="Anxo Canay Reguera",
            help="Student name for the watermark (used when not available from DB).",
        )
        parser.add_argument(
            "--student-nia",
            default="123456",
            help="Student NIA for the watermark (used when not available from DB).",
        )

        # ── Output configuration ─────────────────────────────
        parser.add_argument(
            "--output",
            required=True,
            type=str,
            help="Output path. For multi-file output (instance-pages, both format), "
            "this is a directory. For single-file output, this is the file path.",
        )
        parser.add_argument(
            "--format",
            choices=sorted(_VALID_FORMATS),
            default="png",
            help="Output format: png (one image per page), pdf (single composed PDF), "
            "both (png + pdf). Default: png.",
        )

        # ── Page selection (PDF inputs only) ────────────────
        parser.add_argument(
            "--pages",
            type=str,
            default="all",
            help="Pages to process, comma-separated (e.g. '1,3,5') or 'all'. "
            "Only meaningful for PDF inputs (file/minio modes).",
        )
        parser.add_argument(
            "--dpi",
            type=int,
            default=200,
            help="DPI for PDF→image conversion (default: 200).",
        )

    # ════════════════════════════════════════════════════════════
    # Entry point
    # ════════════════════════════════════════════════════════════

    def handle(self, *args, **options) -> None:
        mode = options["mode"]
        style = options["style"]
        output_path = Path(options["output"]).expanduser().resolve()

        try:
            if mode == "synthetic":
                self._validate_synthetic(options)
                self._handle_synthetic(output_path, options, style)
            elif mode == "file":
                self._validate_file(options)
                self._handle_file(output_path, options, style)
            elif mode == "minio":
                self._validate_minio(options)
                self._handle_minio(output_path, options, style)
            elif mode == "instance":
                self._validate_instance(options)
                self._handle_instance(output_path, options)
            elif mode == "instance-pages":
                self._validate_instance(options)
                self._handle_instance_pages(output_path, options)
        except CommandError:
            raise
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"FAILED: {exc}"))
            import traceback

            traceback.print_exc()
            sys.exit(2)

    # ════════════════════════════════════════════════════════════
    # Validators
    # ════════════════════════════════════════════════════════════

    def _validate_synthetic(self, options):
        pass  # Nothing extra needed

    def _validate_file(self, options):
        if not options["input"]:
            raise CommandError("--input is required when --mode=file.")
        input_path = Path(options["input"]).expanduser()
        if not input_path.exists():
            raise CommandError(f"File not found: {input_path}")

    def _validate_minio(self, options):
        if not options["minio_key"]:
            raise CommandError("--minio-key is required when --mode=minio.")

    def _validate_instance(self, options):
        if not options["instance_id"]:
            raise CommandError("--instance-id is required for instance modes.")

    # ════════════════════════════════════════════════════════════
    # Handlers for each mode
    # ════════════════════════════════════════════════════════════

    # ── synthetic ───────────────────────────────────────────

    def _handle_synthetic(self, output_path, options, style):
        """Single synthetic page image → watermark → output."""

        page_bytes = self._make_synthetic_page_image()
        student_name = options["student_name"]
        student_nia = options["student_nia"]

        self._process_single_page(
            page_bytes=page_bytes,
            page_label="synthetic",
            student_name=student_name,
            student_nia=student_nia,
            style=style,
            output_path=output_path,
            output_format=options["format"],
            no_watermark=options["no_watermark"],
        )

    # ── file ────────────────────────────────────────────────

    def _handle_file(self, output_path, options, style):
        """Local file → extract pages → watermark each → output."""
        input_path = Path(options["input"]).expanduser()
        file_bytes = input_path.read_bytes()
        self._process_file_bytes(
            file_bytes=file_bytes,
            source_label=input_path.name,
            output_path=output_path,
            options=options,
            style=style,
        )

    # ── minio ───────────────────────────────────────────────

    def _handle_minio(self, output_path, options, style):
        """MinIO object → download → extract pages → watermark → output."""
        from apps.exams.services.storage import download_from_minio

        file_bytes = download_from_minio(options["minio_key"])
        self._process_file_bytes(
            file_bytes=file_bytes,
            source_label=Path(options["minio_key"]).name,
            output_path=output_path,
            options=options,
            style=style,
        )

    # ── instance (composed PDF) ─────────────────────────────

    def _handle_instance(self, output_path, options):
        """Compose full instance PDF with watermark (production path)."""
        from apps.instances.services.instance_service import (
            compose_instance_pdf,
        )

        instance = self._get_instance(options["instance_id"])

        # Watermarked version
        pdf_bytes = compose_instance_pdf(instance, watermark_for_student=True)
        wm_path = self._output_path(output_path, "instance_watermarked.pdf", is_dir=False)
        wm_path.write_bytes(pdf_bytes)
        self._print_file_info(wm_path, len(pdf_bytes), "PDF with watermark")

        # Clean version (if requested)
        if options["no_watermark"]:
            clean_bytes = compose_instance_pdf(instance, watermark_for_student=False)
            clean_path = self._output_path(output_path, "instance_clean.pdf", is_dir=False)
            clean_path.write_bytes(clean_bytes)
            self._print_file_info(clean_path, len(clean_bytes), "PDF without watermark")

        self._print_instance_info(instance)

    # ── instance-pages (each page separately) ───────────────

    def _handle_instance_pages(self, output_path, options):
        """Download each page of an instance, watermark individually, save as PNG/PDF."""
        from apps.exams.services.storage import download_from_minio
        from apps.instances.models.instances import PageStatus

        instance = self._get_instance(options["instance_id"])

        student_name = instance.student.full_name if instance.student else options["student_name"]
        student_nia = getattr(instance.student, "nia", "") or options["student_nia"]

        output_path.mkdir(parents=True, exist_ok=True)  # Always a directory for this mode

        pages = instance.pages.exclude(status=PageStatus.DISCARDED).order_by("page_number")

        if not pages.exists():
            self.stderr.write(self.style.WARNING("Instance has no pages."))
            return

        self.stdout.write(f"Processing {pages.count()} page(s) for instance {instance.pk}...")
        self.stdout.write("")

        for page in pages:
            try:
                page_bytes = download_from_minio(page.storage_ref)
            except Exception as exc:
                self.stderr.write(
                    self.style.ERROR(f"  Page {page.page_number}: download failed ({exc})")
                )
                continue

            page_label = f"page_{page.page_number:02d}"
            self._process_single_page(
                page_bytes=page_bytes,
                page_label=page_label,
                student_name=student_name,
                student_nia=student_nia,
                style=options["style"],
                output_path=output_path,
                output_format=options["format"],
                no_watermark=options["no_watermark"],
            )

        self._print_instance_info(instance)

    # ════════════════════════════════════════════════════════════
    # Shared processing logic
    # ════════════════════════════════════════════════════════════

    def _process_file_bytes(self, file_bytes, source_label, output_path, options, style):
        """Process a file (PDF or image), extracting pages and watermarking each."""
        is_pdf = file_bytes.startswith(b"%PDF")
        student_name = options["student_name"]
        student_nia = options["student_nia"]
        dpi = options["dpi"]

        if is_pdf:
            pages_bytes = self._extract_pdf_pages(file_bytes, dpi=dpi)
            total_pages = len(pages_bytes)
            page_indices = self._parse_pages(options["pages"], total_pages)

            self.stdout.write(
                f"PDF '{source_label}': {total_pages} page(s), processing {len(page_indices)}."
            )
            self.stdout.write("")

            # If output is a single PDF, we compose all pages into one
            if options["format"] in ("pdf", "both"):
                self._compose_pdf_output(
                    pages_bytes=pages_bytes,
                    page_indices=page_indices,
                    student_name=student_name,
                    student_nia=student_nia,
                    style=style,
                    output_path=output_path,
                    source_label=source_label,
                    no_watermark=options["no_watermark"],
                )

            # If output is PNG (or both), save individual page images
            if options["format"] in ("png", "both"):
                output_dir = output_path if options["format"] == "png" else output_path.parent
                output_dir.mkdir(parents=True, exist_ok=True)

                for idx in page_indices:
                    page_label = f"page_{idx:02d}"
                    self._process_single_page(
                        page_bytes=pages_bytes[idx - 1],
                        page_label=page_label,
                        student_name=student_name,
                        student_nia=student_nia,
                        style=style,
                        output_path=output_dir,
                        output_format="png",
                        no_watermark=options["no_watermark"],
                    )
        else:
            # Single image file
            self._process_single_page(
                page_bytes=file_bytes,
                page_label=Path(source_label).stem,
                student_name=student_name,
                student_nia=student_nia,
                style=style,
                output_path=output_path,
                output_format=options["format"],
                no_watermark=options["no_watermark"],
            )

    def _process_single_page(
        self,
        page_bytes,
        page_label,
        student_name,
        student_nia,
        style,
        output_path,
        output_format,
        no_watermark,
    ):
        """Apply watermark to a single page image and save in the requested format(s)."""
        from apps.instances.services.watermark import WatermarkService

        # Determine if input is PDF or image
        is_pdf_input = page_bytes.startswith(b"%PDF")

        # If input is PDF and format is pdf, we can skip watermark
        # (watermark only applies to raster images)
        if is_pdf_input and output_format == "pdf":
            wm_bytes = page_bytes  # PDF passthrough
        else:
            # Convert PDF to image if needed
            image_bytes = self._pdf_page_to_image(page_bytes) if is_pdf_input else page_bytes

            # Apply watermark
            wm_bytes = WatermarkService.apply_watermark(
                image_bytes,
                student_name=student_name,
                student_nia=student_nia,
                style=style,
            )

        # Save watermarked version
        if output_format in ("png", "both"):
            if is_pdf_input and output_format == "png":
                # wm_bytes is already PNG from the watermark service (or converted)
                pass
            wm_path = self._output_path(
                output_path, f"{page_label}_wm.png", is_dir=(output_format != "png")
            )
            wm_path.write_bytes(
                wm_bytes
                if not is_pdf_input or output_format == "png"
                else self._ensure_png(wm_bytes)
            )
            self._print_file_info(wm_path, len(wm_bytes), "PNG with watermark")

        if output_format in ("pdf", "both"):
            pdf_bytes = wm_bytes if is_pdf_input else self._image_to_pdf_page(wm_bytes)
            pdf_path = self._output_path(
                output_path, f"{page_label}_wm.pdf", is_dir=(output_format != "pdf")
            )
            pdf_path.write_bytes(pdf_bytes)
            self._print_file_info(pdf_path, len(pdf_bytes), "PDF with watermark")

        # Save clean version if requested
        if no_watermark:
            clean_image = self._pdf_page_to_image(page_bytes) if is_pdf_input else page_bytes

            if output_format in ("png", "both"):
                clean_path = self._output_path(output_path, f"{page_label}_clean.png", is_dir=True)
                clean_path.write_bytes(
                    clean_image if not is_pdf_input else self._ensure_png(clean_image)
                )
                self._print_file_info(clean_path, len(clean_image), "PNG without watermark")

            if output_format in ("pdf", "both"):
                clean_pdf = page_bytes if is_pdf_input else self._image_to_pdf_page(clean_image)
                clean_pdf_path = self._output_path(
                    output_path, f"{page_label}_clean.pdf", is_dir=True
                )
                clean_pdf_path.write_bytes(clean_pdf)
                self._print_file_info(clean_pdf_path, len(clean_pdf), "PDF without watermark")

    def _compose_pdf_output(
        self,
        pages_bytes,
        page_indices,
        student_name,
        student_nia,
        style,
        output_path,
        source_label,
        no_watermark,
    ):
        """Compose all pages into a single PDF, with watermark on raster pages."""
        from pypdf import PdfWriter

        from apps.instances.services.watermark import WatermarkService

        # Watermarked version
        writer = PdfWriter()
        for idx in page_indices:
            page_bytes = pages_bytes[idx - 1]
            if page_bytes.startswith(b"%PDF"):
                writer.append(io.BytesIO(page_bytes))
            else:
                wm = WatermarkService.apply_watermark(
                    page_bytes, student_name, student_nia, style=style
                )
                writer.append(io.BytesIO(self._image_to_pdf_page(wm)))

        output = io.BytesIO()
        writer.write(output)
        writer.close()

        wm_pdf_path = (
            output_path
            if not output_path.is_dir()
            else output_path / f"{Path(source_label).stem}_wm.pdf"
        )
        wm_pdf_path.write_bytes(output.getvalue())
        self._print_file_info(wm_pdf_path, len(output.getvalue()), "Composed PDF with watermark")

        # Clean version
        if no_watermark:
            writer_clean = PdfWriter()
            for idx in page_indices:
                page_bytes = pages_bytes[idx - 1]
                if page_bytes.startswith(b"%PDF"):
                    writer_clean.append(io.BytesIO(page_bytes))
                else:
                    writer_clean.append(io.BytesIO(self._image_to_pdf_page(page_bytes)))

            output_clean = io.BytesIO()
            writer_clean.write(output_clean)
            writer_clean.close()

            clean_pdf_path = (
                output_path.parent / f"{Path(source_label).stem}_clean.pdf"
                if not output_path.is_dir()
                else output_path / f"{Path(source_label).stem}_clean.pdf"
            )
            clean_pdf_path.write_bytes(output_clean.getvalue())
            self._print_file_info(
                clean_pdf_path, len(output_clean.getvalue()), "Composed PDF without watermark"
            )

    # ════════════════════════════════════════════════════════════
    # PDF / Image utilities
    # ════════════════════════════════════════════════════════════

    @staticmethod
    def _extract_pdf_pages(pdf_bytes, dpi=200):
        """Convert each page of a PDF to a PNG image. Returns list of bytes."""
        from pdf2image import convert_from_bytes

        images = convert_from_bytes(pdf_bytes, dpi=dpi)
        result = []
        for img in images:
            out = io.BytesIO()
            img.save(out, format="PNG")
            result.append(out.getvalue())
        return result

    @staticmethod
    def _pdf_page_to_image(pdf_bytes, dpi=200):
        """Convert a single-page PDF to a PNG image. Returns bytes."""
        images = Command._extract_pdf_pages(pdf_bytes, dpi=dpi)
        return images[0] if images else pdf_bytes

    @staticmethod
    def _image_to_pdf_page(image_bytes):
        """Convert a raster image to a single-page PDF."""
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        out = io.BytesIO()
        image.save(out, format="PDF")
        return out.getvalue()

    @staticmethod
    def _ensure_png(image_bytes):
        """Ensure the bytes are PNG format (convert if necessary)."""
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes))
        out = io.BytesIO()
        image.save(out, format="PNG")
        return out.getvalue()

    @staticmethod
    def _parse_pages(pages_str, total_pages):
        """Parse a page selection string into a list of 1-based indices."""
        if pages_str.lower() == "all":
            return list(range(1, total_pages + 1))

        indices = []
        for part in pages_str.split(","):
            part = part.strip()
            if "-" in part:
                start, end = part.split("-", 1)
                indices.extend(range(int(start), int(end) + 1))
            else:
                indices.append(int(part))

        return sorted(i for i in indices if 1 <= i <= total_pages)

    # ════════════════════════════════════════════════════════════
    # Helpers
    # ════════════════════════════════════════════════════════════

    def _get_instance(self, instance_id):
        """Load ExamInstance or raise CommandError."""
        from apps.instances.models.instances import ExamInstance

        try:
            return ExamInstance.objects.select_related("student").get(pk=instance_id)
        except ExamInstance.DoesNotExist as exc:
            raise CommandError(f"ExamInstance {instance_id} not found.") from exc

    @staticmethod
    def _output_path(base, filename, is_dir):
        """Resolve output path. If is_dir, base is a directory; otherwise base is the file."""
        if is_dir:
            base = Path(base)
            base.mkdir(parents=True, exist_ok=True)
            return base / filename
        return Path(base)

    def _print_file_info(self, path, size, description):
        """Print a single file output line."""
        self.stdout.write(f"  ✓ {description}:")
        self.stdout.write(f"    Path: {path}")
        self.stdout.write(f"    Size: {size} bytes")

    def _print_instance_info(self, instance):
        """Print info about the instance."""
        self.stdout.write("")
        self.stdout.write(f"  Instance:  {instance.pk}")
        self.stdout.write(
            f"  Student:   {instance.student.full_name if instance.student else 'N/A'}"
        )
        self.stdout.write(f"  Pages:     {instance.pages.count()}")
        self.stdout.write(f"  Status:    {instance.status}")

    @staticmethod
    def _make_synthetic_page_image() -> bytes:
        """Build a synthetic page with sample content."""
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new("RGB", (_DEMO_PAGE_WIDTH_PX, _DEMO_PAGE_HEIGHT_PX), color="white")
        draw = ImageDraw.Draw(img)

        try:
            font_title = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size=42
            )
            font_body = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size=24
            )
        except OSError:
            font_title = ImageFont.load_default()
            font_body = ImageFont.load_default()

        draw.text((100, 100), "Examen Final — Asignatura Demo", fill="black", font=font_title)

        body_lines = [
            "Problema 1.  Demuestre que la suma de los n primeros enteros",
            "             impares es n². Justifique cada paso.",
            "",
            "Problema 2.  Calcule el límite cuando x → 0 de sin(x)/x.",
            "",
            "Problema 3.  Implemente una función que ordene una lista",
            "             de enteros usando merge sort. Analice su",
            "             complejidad temporal en el peor caso.",
            "",
            "─────────────────────────────────────────────────────",
            "",
            "Solución del estudiante (este texto debería seguir siendo",
            "legible bajo el watermark):",
            "",
            "Problema 1: Por inducción sobre n.",
            "  Base: n=1 → 1 = 1².",
            "  Paso: asumimos que 1+3+...+(2k-1) = k².",
            "        Sumando 2k+1: k² + 2k + 1 = (k+1)². ∎",
        ]

        y = 220
        for line in body_lines:
            draw.text((100, y), line, fill="black", font=font_body)
            y += 40

        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
