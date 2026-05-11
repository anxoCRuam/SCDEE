"""
Management command – benchmark_ingestion

Genera N imágenes sintéticas de páginas de examen (A4, QR, zonas OCR
para DNI, NIA, nombre, apellidos), las inyecta en el pipeline de
ingesta real y mide el tiempo que tarda cada página en ser reconocida
y cada instancia en estar lista para corrección.

Al finalizar, escribe un archivo CSV con los datos crudos y genera
cuatro gráficas de análisis en el directorio de salida.

Uso:
    python manage.py benchmark_ingestion \
        --instances=100 --pages-per-instance=4 \
        --output-dir=/tmp/ingestion_benchmark
"""

from __future__ import annotations

import csv
import io
import time
from pathlib import Path
from uuid import uuid4

from django.core.management.base import BaseCommand
from PIL import Image, ImageDraw

from apps.courses.models.courses import AcademicCourse
from apps.exams.models.exams import Exam, ExamModel, PageProfile, RecognitionZone, ZoneType
from apps.exams.services.instrumented_pdf import _build_qr_content
from apps.exams.services.storage import upload_to_minio
from apps.ingestion.services.dispatcher import recognize_page
from apps.instances.models.instances import ExamInstance, ExamPage, InstanceStatus, PageStatus
from apps.organizations.models.organization import Organization
from apps.subjects.models.subjects import MembershipRole, Subject, SubjectMembership


class Command(BaseCommand):
    help = "Benchmark ingestion pipeline with realistic synthetic pages."

    def add_arguments(self, parser):
        parser.add_argument(
            "--instances",
            type=int,
            default=100,
            help="Number of instances to create (default: 100).",
        )
        parser.add_argument(
            "--pages-per-instance",
            type=int,
            default=4,
            help="Pages per instance (default: 4).",
        )
        parser.add_argument(
            "--output-dir",
            required=True,
            help="Directory where benchmark_results.csv and graphs are written.",
        )
        parser.add_argument(
            "--concurrency",
            type=int,
            default=1,
            help="Number of concurrent Celery workers for recognition (informational).",
        )

    def handle(self, *args, **options):
        output_dir = Path(options["output_dir"]).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        num_instances = options["instances"]
        pages_per_instance = options["pages_per_instance"]

        self.stdout.write("Provisioning organization, exam, model, and profiles...")
        world = self._provision_world(pages_per_instance)

        results = []  # list of dicts with timing data

        for i in range(num_instances):
            self.stdout.write(f"Processing instance {i + 1}/{num_instances}...")
            instance = ExamInstance.objects.create(
                organization=world["org"],
                exam=world["exam"],
                model=world["model"],
                status=InstanceStatus.ASSEMBLING,
                expected_pages=pages_per_instance,
            )

            # Generate and ingest each page
            pages = []
            for page_num in range(1, pages_per_instance + 1):
                image_bytes = self._make_page(
                    org_id=str(world["org"].pk),
                    exam_id=str(world["exam"].pk),
                    model_id=str(world["model"].pk),
                    page_number=page_num,
                )
                key = f"{world['org'].pk}/benchmark/{uuid4().hex}.png"
                upload_to_minio(key=key, data=image_bytes, content_type="image/png")

                page = ExamPage.objects.create(
                    instance=instance,
                    page_number=page_num,
                    storage_ref=key,
                    status=PageStatus.PENDING_RECOGNITION,
                )
                pages.append(page)

            # Measure recognition time per page
            t0 = time.perf_counter()
            for page in pages:
                t_page_start = time.perf_counter()
                recognize_page(page)
                t_page_end = time.perf_counter()
                page.refresh_from_db()
                results.append(
                    {
                        "instance_id": str(instance.pk),
                        "page_number": page.page_number,
                        "recognition_time_ms": (t_page_end - t_page_start) * 1000,
                        "status": page.status,
                    }
                )

            t_total = time.perf_counter() - t0
            instance.refresh_from_db()
            results.append(
                {
                    "instance_id": str(instance.pk),
                    "page_number": "ALL",
                    "recognition_time_ms": t_total * 1000,
                    "status": instance.status,
                }
            )

        # Write CSV
        csv_path = output_dir / "benchmark_results.csv"
        with csv_path.open("w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["instance_id", "page_number", "recognition_time_ms", "status"]
            )
            writer.writeheader()
            writer.writerows(results)

        self.stdout.write(self.style.SUCCESS(f"Raw data written to {csv_path}"))

        # Generate graphs
        self._generate_graphs(results, output_dir)

        self.stdout.write(self.style.SUCCESS("Benchmark finished."))

    def _provision_world(self, pages_per_instance):
        """Create a minimal organization + exam + model + page profiles."""
        from django.contrib.auth import get_user_model

        user_model = get_user_model()

        suffix = uuid4().hex[:8]
        org = Organization.objects.create(name=f"Bench-{suffix}", subdomain=f"bench{suffix}")
        user = user_model.objects.create_user(
            email=f"bench-{suffix}@scdee.local",
            password="benchpass123",  # noqa: S106
            first_name="Bench",
            last_name="User",
            organization=org,
            is_staff=True,
        )
        course = AcademicCourse.objects.create(organization=org, label="2026", is_active=True)
        subject = Subject.objects.create(
            organization=org,
            name="Bench Subject",
            code=f"B-{suffix}",
            course=course,
            coordinator=user,
        )
        SubjectMembership.objects.create(
            organization=org,
            user=user,
            subject=subject,
            role=MembershipRole.COORDINATOR,
            is_active=True,
        )
        exam = Exam.objects.create(organization=org, name="Bench Exam", subject=subject)
        model = ExamModel.objects.create(label="A", exam=exam)

        # Create page profiles with zones (QR + text fields)
        for page_num in range(1, pages_per_instance + 1):
            pp = PageProfile.objects.create(
                exam_model=model,
                page_number=page_num,
                page_width=595.0,
                page_height=842.0,
            )
            # QR zone
            RecognitionZone.objects.create(
                page_profile=pp,
                zone_type=ZoneType.QR,
                attribute="exam_qr",
                x=400,
                y=700,
                width=120,
                height=120,
            )
            # Text fields on page 1
            if page_num == 1:
                fields = [
                    ("name", 120, 100, 300, 40),
                    ("dni", 120, 170, 200, 40),
                    ("nia", 120, 240, 200, 40),
                ]
                for attr, x, y, w, h in fields:
                    RecognitionZone.objects.create(
                        page_profile=pp,
                        zone_type=ZoneType.OCR_TEXT,
                        attribute=attr,
                        x=x,
                        y=y,
                        width=w,
                        height=h,
                    )

        return {"org": org, "exam": exam, "model": model}

    def _make_page(self, org_id, exam_id, model_id, page_number, width=1240, height=1754):
        """Generate a synthetic A4 PNG with QR and text fields."""
        img = Image.new("L", (width, height), color=255)
        draw = ImageDraw.Draw(img)

        # Grid
        for x in range(0, width, 100):
            draw.line([(x, 0), (x, height)], fill=230, width=1)
        for y in range(0, height, 120):
            draw.line([(0, y), (width, y)], fill=230, width=1)

        # QR code
        import qrcode

        qr_content = _build_qr_content(org_id, exam_id, model_id, page_number)
        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=0,
        )
        qr.add_data(qr_content)
        qr.make(fit=True)
        qr_img = qr.make_image(fill_color="black", back_color="white").convert("L")
        qr_img = qr_img.resize((150, 150))
        img.paste(qr_img, (width - 180, 30))

        # Text fields on page 1
        if page_number == 1:
            draw.text((120, 100), "Nombre: Juan García", fill=0)
            draw.text((120, 170), "DNI: 12345678X", fill=0)
            draw.text((120, 240), "NIA: 987654", fill=0)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def _generate_graphs(self, results, output_dir):
        """Generate histograms and time-series graphs using matplotlib."""
        try:
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import pandas as pd

            df = pd.DataFrame(results)
            pages_df = df[df["page_number"] != "ALL"].copy()
            pages_df["recognition_time_ms"] = pd.to_numeric(pages_df["recognition_time_ms"])

            # 1. Histogram of per-page recognition time
            fig, ax = plt.subplots(figsize=(9, 5))
            ax.hist(pages_df["recognition_time_ms"], bins=30, edgecolor="black", alpha=0.7)
            ax.set_xlabel("Tiempo de reconocimiento (ms)")
            ax.set_ylabel("Frecuencia")
            ax.set_title("Distribución de tiempos de reconocimiento por página")
            ax.grid(axis="y", linestyle=":", alpha=0.5)
            fig.tight_layout()
            fig.savefig(output_dir / "01_histograma_reconocimiento.png", dpi=144)
            plt.close(fig)

            # 2. Bar chart of total time per instance
            instances_df = df[df["page_number"] == "ALL"].copy()
            instances_df["recognition_time_ms"] = pd.to_numeric(
                instances_df["recognition_time_ms"]
            )
            fig, ax = plt.subplots(figsize=(12, 5))
            ax.bar(
                range(len(instances_df)),
                instances_df["recognition_time_ms"] / 1000.0,
                color="tab:blue",
                alpha=0.8,
            )
            ax.set_xlabel("Instancia")
            ax.set_ylabel("Tiempo total (s)")
            ax.set_title("Tiempo de ensamblaje por instancia")
            ax.grid(axis="y", linestyle=":", alpha=0.5)
            fig.tight_layout()
            fig.savefig(output_dir / "02_tiempo_por_instancia.png", dpi=144)
            plt.close(fig)

            # 3. Summary statistics table saved as CSV
            stats = pages_df["recognition_time_ms"].describe()
            stats.to_csv(output_dir / "03_estadisticas.csv", header=["Valor"])

            self.stdout.write(f"Total páginas procesadas: {len(pages_df)}")
            self.stdout.write(f"Tiempo medio por página: {stats['mean']:.2f} ms")
            self.stdout.write(f"P95: {pages_df['recognition_time_ms'].quantile(0.95):.2f} ms")

        except ImportError as e:
            self.stderr.write(f"No se pudieron generar gráficas: {e}")
