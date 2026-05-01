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

from apps.annotations.models import Annotation, AnnotationType
from apps.organizations.services import get_org_config

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
    payload: str = "",
    audio_data: bytes | None = None,
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
        from apps.exams.models import Problem

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

    # Handle audio storage (RF-10.6) with per-org duration check.
    storage_ref = ""
    if annotation_type == AnnotationType.AUDIO:
        if not audio_data:
            raise AnnotationServiceError(
                code="NO_AUDIO_DATA",
                detail="Audio annotations require audio file data.",
            )
        _validate_audio_duration(instance.organization, audio_data)
        storage_ref = _upload_audio(instance, audio_data)
        payload = ""  # Audio has no inline payload.

    elif annotation_type == AnnotationType.TEXT and not payload:
        raise AnnotationServiceError(
            code="EMPTY_PAYLOAD",
            detail="Text annotations require a non-empty payload.",
        )

    annotation = Annotation.objects.create(
        instance=instance,
        annotation_type=annotation_type,
        payload=payload,
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
        _trigger_grade_ocr(annotation)

    return annotation


def update_annotation(
    annotation: Annotation,
    *,
    payload: str | None = None,
    audio_data: bytes | None = None,
    author,
) -> Annotation:
    """Update an annotation's content (RF-10.2). Author-only."""
    if annotation.author_id != author.pk:
        raise AnnotationServiceError(
            code="NOT_AUTHOR",
            detail="Only the annotation's author can modify it.",
        )

    if annotation.annotation_type == AnnotationType.AUDIO and audio_data:
        # Replace audio file in MinIO (re-validate duration, RF-10.6).
        _validate_audio_duration(annotation.instance.organization, audio_data)
        if annotation.storage_ref:
            _delete_storage(annotation.storage_ref)
        annotation.storage_ref = _upload_audio(annotation.instance, audio_data)
    elif payload is not None:
        annotation.payload = payload

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
        _delete_storage(annotation.storage_ref)

    annotation.delete()


def ocr_grade_annotation(annotation: Annotation) -> dict:
    """Run OCR on an annotation to extract a numeric grade (RF-9.12).

    Returns dict with recognized value, confidence, and whether
    the grade was auto-applied.
    """
    if not annotation.problem:
        raise AnnotationServiceError(
            code="NO_PROBLEM_ASSOCIATED",
            detail="Grade OCR requires a problem association.",
        )

    # Get image data from the annotation.
    if annotation.annotation_type == AnnotationType.STYLUS:
        image_bytes = _render_stylus_to_image(annotation.payload)
    elif annotation.annotation_type == AnnotationType.TEXT:
        # For text, just parse the number directly.
        return _parse_text_grade(annotation)
    elif annotation.annotation_type == AnnotationType.AUDIO:
        raise AnnotationServiceError(
            code="CANNOT_OCR_AUDIO",
            detail="Cannot extract a grade from an audio annotation.",
        )
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
            score = Decimal(str(result.value))
            from apps.grading.services import grade_problem

            grade_problem(
                instance=annotation.instance,
                problem=annotation.problem,
                score=score,
                grader=annotation.author,
                expected_score=annotation.instance.total_score,
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


def ocr_stylus_annotation(annotation: Annotation) -> dict:
    """Run OCR on stylus strokes to convert to text (RF-9.13)."""
    if annotation.annotation_type != AnnotationType.STYLUS:
        raise AnnotationServiceError(code="NOT_STYLUS")

    image_bytes = _render_stylus_to_image(annotation.payload)

    from apps.ingestion.recognizers.ocr import OCRRecognizer

    recognizer = OCRRecognizer(zone_type="OCR_TEXT")
    result = recognizer.recognize(image_bytes)

    return {
        "recognized_text": result.value,
        "confidence": result.confidence,
    }


# ── Private helpers ──────────────────────────────────────────


def _upload_audio(instance, audio_data: bytes) -> str:
    """Upload audio to MinIO. Returns the storage key."""
    import uuid

    from apps.exams.services.storage import upload_to_minio

    key = f"{instance.organization_id}/annotations/{instance.pk}/{uuid.uuid4().hex}.audio"
    upload_to_minio(key=key, data=audio_data, content_type="audio/webm")
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


def _delete_storage(storage_ref: str) -> None:
    """Delete a file from MinIO."""
    try:
        from apps.exams.services.storage import delete_minio_object

        delete_minio_object(storage_ref)
    except Exception as exc:
        logger.warning("Failed to delete MinIO object %s: %s", storage_ref, exc)


def _trigger_grade_ocr(annotation: Annotation) -> None:
    """Trigger OCR extraction on a grade annotation asynchronously."""
    try:
        ocr_grade_annotation(annotation)
    except Exception as exc:
        logger.warning("Grade OCR trigger failed for annotation %s: %s", annotation.pk, exc)


def _render_stylus_to_image(strokes_json: str) -> bytes:
    """Render stylus strokes to a PNG image for OCR.

    In v1.0 this is a minimal implementation using PIL.
    The quality depends on the OCR engine used.
    """
    import io
    import json

    from PIL import Image, ImageDraw

    try:
        strokes = json.loads(strokes_json)
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


def _parse_text_grade(annotation: Annotation) -> dict:
    """Parse a numeric grade from a text annotation."""
    import re
    from decimal import InvalidOperation

    from apps.grading.services import GradingServiceError, grade_problem

    text = annotation.payload.strip()
    match = re.search(r"[-+]?\d*[.,]?\d+", text)

    if match:
        value = match.group().replace(",", ".")
        try:
            score = Decimal(value)
        except InvalidOperation:
            logger.info("OCR-grade: text %r is not a valid decimal.", text)
        else:
            try:
                grade_problem(
                    instance=annotation.instance,
                    problem=annotation.problem,
                    score=score,
                    grader=annotation.author,
                    expected_score=annotation.instance.total_score,
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
