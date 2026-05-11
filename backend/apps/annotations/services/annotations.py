"""
Annotation business logic.

Handles:
- Creation with type-specific validation (RF-10.1)
- Audio upload to MinIO with duration validation (RF-10.6)
- Update (author-only, RF-10.2)
- Deletion with MinIO cleanup (RF-10.3)
- Grade annotation with automatic OCR trigger (RF-10.5)
- OCR on stylus annotations (RF-9.13)

References: RF-10.1 through RF-10.7, RF-9.12, RF-9.13
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.conf import settings

from apps.annotations.models.annotations import Annotation, AnnotationType
from apps.exams.models.exams import Problem
from apps.exams.services.storage import delete_minio_object
from apps.organizations.services.organization_config import get_org_config

logger = logging.getLogger(__name__)


class AnnotationServiceError(Exception):
    """Raised when an annotation operation fails."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        super().__init__(detail or code)


def create_annotation(
    *,
    instance,
    annotation_type: str,
    content_bytes: bytes,
    page_number: int = 1,
    x: float = 0.0,
    y: float = 0.0,
    problem_id: str | None = None,
    author,
    is_grade_annotation: bool = False,
) -> Annotation:
    """Create an annotation on an instance (RF-10.1).

    For audio annotations, `audio_data` is uploaded to MinIO.
    For text/stylus, `payload` contains the inline content.
    """
    # Validate annotation type.
    if annotation_type not in AnnotationType.values:
        raise AnnotationServiceError(code="INVALID_TYPE")

    # Validate problem association if provided (RF-10.7).
    problem = None
    if problem_id:
        from apps.exams.models.exams import Problem

        try:
            problem = Problem.objects.get(pk=problem_id)
            # Validate problem belongs to the instance's model.
            if instance.model and problem.exam_model_id != instance.model_id:
                raise AnnotationServiceError(
                    code="PROBLEM_NOT_IN_MODEL",
                    detail="Problem does not belong to this instance's model.",
                )
        except Problem.DoesNotExist:
            raise AnnotationServiceError(code="PROBLEM_NOT_FOUND") from None

    if is_grade_annotation and not problem_id:
        raise AnnotationServiceError(
            code="GRADE_ANNOTATION_REQUIRES_PROBLEM",
            detail="Grade annotations must be associated with a problem.",
        )

    # Handle audio storage (RF-10.6) with per-org duration check.
    storage_ref = ""
    if annotation_type == AnnotationType.AUDIO:
        _validate_audio_duration(instance.organization, content_bytes)
        content_type = "audio/webm"
    elif annotation_type == AnnotationType.STYLUS:
        content_type = "application/json"
    elif annotation_type == AnnotationType.TEXT:
        content_type = "text/plain"
    else:
        raise AnnotationServiceError(
            code="NOT_SUPPORTED",
            detail="Not supported type",
        )
    storage_ref = _upload_content(instance, content_bytes, content_type=content_type)

    annotation = Annotation.objects.create(
        instance=instance,
        annotation_type=annotation_type,
        storage_ref=storage_ref,
        page_number=page_number,
        x=x,
        y=y,
        problem=problem,
        author=author,
        is_grade_annotation=is_grade_annotation,
    )

    # Trigger grade OCR if this is a grade annotation (RF-10.5).
    if is_grade_annotation and problem:
        _trigger_grade_ocr(annotation, author)

    return annotation


def update_annotation(
    annotation: Annotation,
    *,
    author,
    page_number: int | None = None,
    x: float | None = None,
    y: float | None = None,
    problem_id: str | None = None,
    content_bytes: bytes | None = None,
) -> Annotation:
    """Update an annotation's content (RF-10.2). Author-only."""
    if not author.is_staff and annotation.author_id != author.pk:
        raise AnnotationServiceError(code="NOT_AUTHOR")

    # Validate and assign problem if provided
    if problem_id is not None:
        if problem_id == "" or problem_id == "null":
            annotation.problem = None
        else:
            try:
                problem = Problem.objects.get(pk=problem_id)
                if (
                    annotation.instance.model
                    and problem.exam_model_id != annotation.instance.model_id
                ):
                    raise AnnotationServiceError(
                        code="PROBLEM_NOT_IN_MODEL",
                        detail="Problem does not belong to this instance's model.",
                    )
                annotation.problem = problem
            except Problem.DoesNotExist:
                raise AnnotationServiceError(code="PROBLEM_NOT_FOUND") from None

    # Update positional metadata
    if page_number is not None:
        annotation.page_number = page_number
    if x is not None:
        annotation.x = x
    if y is not None:
        annotation.y = y
    if content_bytes is not None:
        # Reemplazar contenido en MinIO
        if annotation.storage_ref:
            delete_minio_object(annotation.storage_ref)
        # Determinar content-type
        if annotation.annotation_type == AnnotationType.AUDIO:
            _validate_audio_duration(annotation.instance.organization, content_bytes)
            content_type = "audio/webm"
        elif annotation.annotation_type == AnnotationType.STYLUS:
            content_type = "application/json"
        else:
            content_type = "text/plain"
        annotation.storage_ref = _upload_content(
            annotation.instance, content_bytes, content_type=content_type
        )
    annotation.save()
    return annotation


def delete_annotation(annotation: Annotation, *, actor) -> None:
    """Delete an annotation and its MinIO file (RF-10.3)."""
    if annotation.author_id != actor.pk and not actor.is_staff:
        raise AnnotationServiceError(
            code="NOT_AUTHOR",
            detail="Only the author or a manager can delete an annotation.",
        )

    # Clean up MinIO file.
    if annotation.storage_ref:
        delete_minio_object(annotation.storage_ref)

    annotation.delete()


def ocr_grade_annotation(annotation: Annotation, author) -> dict:
    """Run OCR on an annotation to extract a numeric grade (RF-9.12).

    Returns dict with recognized value, confidence, and whether
    the grade was auto-applied.
    """
    if not author.is_staff and annotation.author_id != author.pk:
        raise AnnotationServiceError(code="NOT_AUTHOR")
    if not annotation.problem:
        raise AnnotationServiceError(code="NO_PROBLEM_ASSOCIATED")
    if not annotation.storage_ref:
        raise AnnotationServiceError(code="NO_CONTENT")

    # Descargar contenido desde MinIO
    from apps.exams.services.storage import download_from_minio

    content_bytes = download_from_minio(annotation.storage_ref)

    # Get image data from the annotation.
    if annotation.annotation_type == AnnotationType.STYLUS:
        image_bytes = _render_stylus_to_image(content_bytes)
    elif annotation.annotation_type == AnnotationType.TEXT:
        # For text, just parse the number directly.
        text = content_bytes.decode("utf-8")
        return _parse_text_grade(annotation, text)
    else:
        raise AnnotationServiceError(code="UNSUPPORTED_TYPE")

    # Run OCR.
    from apps.ingestion.recognizers.ocr import OCRRecognizer

    recognizer = OCRRecognizer(zone_type="OCR_NUMBER")
    result = recognizer.recognize(image_bytes)

    auto_applied = False
    confidence_threshold = getattr(settings, "GRADE_OCR_CONFIDENCE_THRESHOLD", 0.8)

    if result.value and result.confidence >= confidence_threshold:
        # Auto-apply the grade.
        try:
            # Obtener el Grade actual para este problema e instancia (si existe)
            from apps.grading.models.grading import Grade

            current_grade = Grade.objects.filter(
                problem=annotation.problem,
                instance=annotation.instance,
            ).first()
            current_score = current_grade.score if current_grade else None

            score = Decimal(str(result.value))
            from apps.grading.services.grading import grade_problem

            grade_problem(
                instance=annotation.instance,
                problem=annotation.problem,
                score=score,
                grader=annotation.author,
                old_score=current_score,
            )
            auto_applied = True
        except Exception as exc:
            logger.warning("Auto-grade failed for annotation %s: %s", annotation.pk, exc)

    return {
        "recognized_value": result.value,
        "confidence": result.confidence,
        "auto_applied": auto_applied,
        "raw_value": result.raw_value,
    }


def ocr_stylus_annotation(annotation: Annotation, author) -> dict:
    """Run OCR on stylus strokes to convert to text (RF-9.13)."""
    if not author.is_staff and annotation.author_id != author.pk:
        raise AnnotationServiceError(code="NOT_AUTHOR")
    if annotation.annotation_type != AnnotationType.STYLUS:
        raise AnnotationServiceError(code="NOT_STYLUS")
    if not annotation.storage_ref:
        raise AnnotationServiceError(code="NO_CONTENT")

    from apps.exams.services.storage import download_from_minio

    content_bytes = download_from_minio(annotation.storage_ref)
    image_bytes = _render_stylus_to_image(content_bytes)

    from apps.ingestion.recognizers.ocr import OCRRecognizer

    recognizer = OCRRecognizer(zone_type="OCR_TEXT")
    result = recognizer.recognize(image_bytes)

    return {
        "recognized_text": result.value,
        "confidence": result.confidence,
    }


# ── Private helpers ──────────────────────────────────────────


def _upload_content(instance, content: bytes, content_type: str) -> str:
    """Upload annotation content to MinIO. Returns the storage key."""
    import uuid

    from apps.exams.services.storage import upload_to_minio

    key = f"{instance.organization_id}/annotations/{instance.pk}/{uuid.uuid4().hex}.bin"
    upload_to_minio(key=key, data=content, content_type=content_type)
    return key


def _validate_audio_duration(organization, audio_data: bytes) -> None:
    """Reject audio annotations whose duration exceeds the org's limit (RF-10.6).

    Reads ``OrganizationConfig.max_audio_duration_minutes`` for the
    organisation. Uses ``mutagen.File`` which auto-detects the container
    (webm/ogg/opus/mp3/m4a/wav).
    """
    import io

    from mutagen import File as MutagenFile

    try:
        meta = MutagenFile(io.BytesIO(audio_data))
    except Exception as exc:  # mutagen raises a variety of subclasses
        raise AnnotationServiceError(
            code="AUDIO_FORMAT_UNREADABLE",
            detail=f"Could not parse audio metadata: {exc}",
        ) from exc

    if meta is None or not getattr(meta, "info", None):
        raise AnnotationServiceError(
            code="AUDIO_FORMAT_UNSUPPORTED",
            detail="Audio container is not recognised (expected webm/ogg/mp3/m4a/wav).",
        )

    duration_seconds = float(getattr(meta.info, "length", 0.0) or 0.0)
    max_minutes = get_org_config(organization).max_audio_duration_minutes

    if duration_seconds > max_minutes * 60:
        raise AnnotationServiceError(
            code="AUDIO_DURATION_EXCEEDED",
            detail=(
                f"Audio duration {duration_seconds:.1f}s exceeds the organisation's "
                f"limit of {max_minutes} minute(s)."
            ),
        )


def _trigger_grade_ocr(annotation: Annotation, author) -> None:
    """Trigger OCR extraction on a grade annotation asynchronously."""
    try:
        ocr_grade_annotation(annotation, author)
    except Exception as exc:
        logger.warning("Grade OCR trigger failed for annotation %s: %s", annotation.pk, exc)


def _render_stylus_to_image(strokes_bytes: str) -> bytes:
    """Render stylus strokes to a PNG image for OCR.

    In v1.0 this is a minimal implementation using PIL.
    The quality depends on the OCR engine used.
    """
    import io
    import json

    from PIL import Image, ImageDraw

    try:
        strokes = json.loads(strokes_bytes.decode("utf-8"))
    except (json.JSONDecodeError, TypeError):
        strokes = []

    # Create a white canvas.
    width, height = 400, 200
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)

    # Draw strokes. Each stroke is a list of points {x, y, pressure?}.
    for stroke in strokes:
        points = []
        if isinstance(stroke, list):
            for point in stroke:
                if isinstance(point, dict):
                    px = int(point.get("x", 0) * width / 100)
                    py = int(point.get("y", 0) * height / 100)
                    points.append((px, py))
            if len(points) > 1:
                draw.line(points, fill="black", width=2)

    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def _parse_text_grade(annotation: Annotation, text: str) -> dict:
    """Parse a numeric grade from a text annotation."""
    import re
    from decimal import InvalidOperation

    from apps.grading.services.grading import GradingServiceError, grade_problem

    match = re.search(r"[-+]?\d*[.,]?\d+", text)

    if match:
        value = match.group().replace(",", ".")
        try:
            score = Decimal(value)
        except InvalidOperation:
            logger.info("OCR-grade: text %r is not a valid decimal.", text)
        else:
            try:
                # Obtener el Grade actual (si existe) para pasar su score como old_score
                from apps.grading.models.grading import Grade

                current_grade = Grade.objects.filter(
                    problem=annotation.problem,
                    instance=annotation.instance,
                ).first()
                old_score = current_grade.score if current_grade else None

                grade_problem(
                    instance=annotation.instance,
                    problem=annotation.problem,
                    score=score,
                    grader=annotation.author,
                    old_score=old_score,  # ya existe en la firma actual
                )
            except GradingServiceError as exc:
                logger.info(
                    "OCR-grade: cannot auto-apply score %s on instance %s: %s",
                    score,
                    annotation.instance_id,
                    exc.code,
                )
            else:
                return {
                    "recognized_value": value,
                    "confidence": 1.0,
                    "auto_applied": True,
                    "raw_value": text,
                }

    return {
        "recognized_value": None,
        "confidence": 0.0,
        "auto_applied": False,
        "raw_value": text,
    }
