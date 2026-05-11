"""
Management command – seed_load_test_data

Provisions a complete tenant + exam + students + graders + instances
with real MinIO pages (A4-sized PNGs with embedded QR codes) so the
Locust load test exercises every user-class path without manual setup.

Writes four files to <output-dir>:
    students.csv       email,password,instance_id,exam_id,problem_id
    graders.csv        email,password,instance_id,problem_id,exam_id
    manager.txt        shell source for locust env vars

All entities are created in a single transaction. The password is
either supplied or auto-generated (printed to stdout).

References: RNF-2, RF-9.1, RF-12.4.
"""

from __future__ import annotations

import csv
import io
import secrets
import sys
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from PIL import Image, ImageDraw

from apps.exams.models.exams import PageProfile, RecognitionZone, ZoneType
from apps.exams.services.instrumented_pdf import _build_qr_content
from apps.exams.services.storage import upload_to_minio


def _make_fake_page_image(
    org_id: str, exam_id: str, model_id: str, page_number: int, width=1240, height=1754
) -> bytes:
    """Generate a compressible A4-sized PNG (150 DPI) with fake text and a real QR code."""
    img = Image.new("L", (width, height), color=255)
    draw = ImageDraw.Draw(img)

    # Grid
    for x in range(0, width, 100):
        draw.line([(x, 0), (x, height)], fill=230, width=1)
    for y in range(0, height, 120):
        draw.line([(0, y), (width, y)], fill=230, width=1)

    # Header fields
    draw.text((120, 100), "Nombre:", fill=0)
    draw.text((120, 170), "DNI:", fill=0)
    draw.text((120, 240), "Grupo:", fill=0)

    # QR code containing the real payload
    import qrcode

    qr_content = _build_qr_content(
        org_id=org_id,
        exam_id=exam_id,
        model_id=model_id,
        page_number=page_number,
    )
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

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class Command(BaseCommand):
    help = "Provision load-test fixtures and write CSVs/env files."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--output-dir", required=True)
        parser.add_argument("--students", type=int, default=1200)
        parser.add_argument("--graders", type=int, default=15)
        parser.add_argument("--password", default=None)

    def handle(self, *args, **options) -> None:
        output_dir = Path(options["output_dir"]).expanduser().resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        student_count = options["students"]
        grader_count = options["graders"]
        password = options["password"] or secrets.token_urlsafe(16)

        if student_count < 1 or grader_count < 1:
            raise CommandError("--students and --graders must be ≥ 1.")

        try:
            with transaction.atomic():
                world = self._provision(student_count, grader_count, password)
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"Provisioning failed: {exc}"))
            sys.exit(2)

        self._write_files(world, output_dir, password)
        self._print_summary(world, output_dir, password)

    def _provision(self, student_count, grader_count, password):
        from django.utils import timezone

        from apps.courses.models.courses import AcademicCourse
        from apps.exams.models.exams import (
            Exam,
            ExamConvocation,
            ExamModel,
            Problem,
            RubricCriterion,
        )
        from apps.grading.models.grading import AssignmentRule
        from apps.instances.models.instances import (
            ExamInstance,
            ExamPage,
            InstanceStatus,
            PageStatus,
        )
        from apps.organizations.models.organization import Organization
        from apps.reviews.models.reviews import ExamReview, ReviewStatus
        from apps.subjects.models.subjects import (
            MembershipRole,
            Subject,
            SubjectGroup,
            SubjectMembership,
        )

        user_model = get_user_model()
        suffix = uuid4().hex[:8]

        org = Organization.objects.create(name=f"LoadTest-{suffix}", subdomain=f"lt{suffix}")
        manager = user_model.objects.create_user(
            email=f"mgr-{suffix}@scdee.local",
            password=password,
            first_name="Load",
            last_name="Manager",
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
            name="Load Test Subject",
            code=f"LT-{suffix}",
            course=course,
            coordinator=manager,
        )
        SubjectMembership.objects.create(
            organization=org,
            user=manager,
            subject=subject,
            role=MembershipRole.COORDINATOR,
            is_active=True,
        )

        exam = Exam.objects.create(organization=org, name="Load Test Exam", subject=subject)
        model = ExamModel.objects.create(label="A", exam=exam)
        problem = Problem.objects.create(
            name="P1",
            max_score=Decimal("10.00"),
            exam_model=model,
            order=1,
        )
        RubricCriterion.objects.create(
            problem=problem,
            description="Correct solution",
            score=Decimal("10.00"),
            order=0,
        )

        # Upload a dummy blank PDF (for PDF composition to work)
        blank_key = f"{org.pk}/exams/{exam.pk}/models/{model.pk}/blank.pdf"
        upload_to_minio(key=blank_key, data=b"%PDF-1.4 minimal", content_type="application/pdf")
        model.blank_pdf_ref = blank_key
        model.blank_pdf_pages = 3
        model.blank_pdf_size = len(b"%PDF-1.4 minimal")
        model.page_dimensions = [
            {"page": 1, "width": 595.0, "height": 842.0},
            {"page": 2, "width": 595.0, "height": 842.0},
            {"page": 3, "width": 595.0, "height": 842.0},
        ]
        model.save()

        # Page profiles + QR zones for 3 pages
        for page_num in (1, 2, 3):
            pp = PageProfile.objects.create(
                exam_model=model,
                page_number=page_num,
                page_width=595.0,
                page_height=842.0,
            )
            RecognitionZone.objects.create(
                page_profile=pp,
                zone_type=ZoneType.QR,
                attribute="exam_qr",
                x=400,
                y=700,
                width=120,
                height=120,
            )

        # Students, groups, instances, pages
        group = SubjectGroup.objects.create(subject=subject, label="G1")
        students_instances = []
        for i in range(student_count):
            student = user_model.objects.create_user(
                email=f"s{i:04d}-{suffix}@scdee.local",
                password=password,
                first_name=f"S{i:04d}",
                last_name="Student",
                organization=org,
            )
            SubjectMembership.objects.create(
                organization=org,
                user=student,
                subject=subject,
                role=MembershipRole.STUDENT,
                group=group,
                is_active=True,
            )
            ExamConvocation.objects.create(exam=exam, student=student)

            instance = ExamInstance.objects.create(
                organization=org,
                exam=exam,
                model=model,
                student=student,
                status=InstanceStatus.IN_REVIEW,
                total_score=Decimal("8.00"),
                expected_pages=3,
            )
            for page_num in range(1, 4):
                page_key = f"{org.pk}/instances/{instance.pk}/page_{page_num}.png"
                image_bytes = _make_fake_page_image(
                    org_id=str(org.pk),
                    exam_id=str(exam.pk),
                    model_id=str(model.pk),
                    page_number=page_num,
                )
                upload_to_minio(key=page_key, data=image_bytes, content_type="image/png")
                ExamPage.objects.create(
                    instance=instance,
                    page_number=page_num,
                    storage_ref=page_key,
                    status=PageStatus.RECOGNIZED,
                )
            students_instances.append((student, instance))

        # Review window (open)
        now = timezone.now()
        ExamReview.objects.create(
            exam=exam,
            start_date=now - timezone.timedelta(hours=1),
            end_date=now + timezone.timedelta(days=7),
            status=ReviewStatus.OPEN,
        )

        # Graders + assignment rule
        graders = []
        for j in range(grader_count):
            grader = user_model.objects.create_user(
                email=f"g{j:03d}-{suffix}@scdee.local",
                password=password,
                first_name=f"G{j:03d}",
                last_name="Grader",
                organization=org,
                is_staff=False,
            )
            SubjectMembership.objects.create(
                organization=org,
                user=grader,
                subject=subject,
                role=MembershipRole.TEACHER,
                is_active=True,
            )
            graders.append(grader)

        AssignmentRule.objects.create(
            exam=exam,
            correctors=[str(g.pk) for g in graders],
            problems=[str(problem.pk)],
        )

        return {
            "org": org,
            "manager": manager,
            "exam": exam,
            "model": model,
            "problem": problem,
            "students": students_instances,
            "graders": graders,
        }

    def _write_files(self, world, output_dir, password):
        exam_id = str(world["exam"].pk)
        problem_id = str(world["problem"].pk)

        # students.csv
        students_csv = output_dir / "students.csv"
        with students_csv.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["email", "password", "instance_id", "exam_id", "problem_id"])
            for student, instance in world["students"]:
                w.writerow([student.email, password, str(instance.pk), exam_id, problem_id])

        # graders.csv – all hammer the first instance
        first_instance_id = str(world["students"][0][1].pk)
        graders_csv = output_dir / "graders.csv"
        with graders_csv.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["email", "password", "instance_id", "problem_id", "exam_id"])
            for grader in world["graders"]:
                w.writerow([grader.email, password, first_instance_id, problem_id, exam_id])

        # manager.txt (shell source)
        manager_txt = output_dir / "manager.txt"
        manager_txt.write_text(
            f"# Source this file before running locust\n"
            f"export SCDEE_LOAD_MANAGER_EMAIL={world['manager'].email}\n"
            f"export SCDEE_LOAD_MANAGER_PW={password}\n"
            f"export SCDEE_LOAD_EXAM_ID={exam_id}\n"
            f"export SCDEE_LOAD_STUDENT_FILE={output_dir / 'students.csv'}\n"
            f"export SCDEE_LOAD_GRADER_FILE={output_dir / 'graders.csv'}\n"
        )

    def _print_summary(self, world, output_dir, password):
        self.stdout.write(self.style.SUCCESS("✓ Load-test seed created."))
        self.stdout.write(f"  Students: {len(world['students'])}")
        self.stdout.write(f"  Graders:  {len(world['graders'])}")
        self.stdout.write(f"  Password: {password}")
        self.stdout.write(f"  Output:   {output_dir}")
