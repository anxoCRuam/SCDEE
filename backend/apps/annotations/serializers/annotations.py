"""
Serializers for annotation endpoints.

References: RF-10.1 through RF-10.7
"""

from rest_framework import serializers

from apps.annotations.models.annotations import AnnotationType


class CreateAnnotationSerializer(serializers.Serializer):
    """Input for POST /instances/{id}/annotations/ (RF-10.1)."""

    annotation_type = serializers.ChoiceField(choices=AnnotationType.choices, required=True)
    payload = serializers.CharField(
        required=False,
        default="",
        allow_blank=True,
        help_text="Inline content: rich text or JSON strokes. Required for TEXT/STYLUS.",
    )
    page_number = serializers.IntegerField(required=False, default=1, min_value=1)
    x = serializers.FloatField(required=False, default=0.0)
    y = serializers.FloatField(required=False, default=0.0)
    problem_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        default=None,
        help_text="Optional problem association (RF-10.7).",
    )
    is_grade_annotation = serializers.BooleanField(
        required=False,
        default=False,
        help_text="If True, triggers OCR to extract a numeric grade (RF-10.5).",
    )


class UpdateAnnotationSerializer(serializers.Serializer):
    """Input for PUT /annotations/{id}/ (RF-10.2).
    All fields are optional; only the ones present are modified.
    """

    payload = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="New inline content (text or stylus JSON). "
        "If present and no file is uploaded, the text payload is "
        "used as the new content.",
    )
    page_number = serializers.IntegerField(required=False, min_value=1)
    x = serializers.FloatField(required=False)
    y = serializers.FloatField(required=False)
    problem_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        default=None,
        help_text="Change problem association. None = remove.",
    )


class AnnotationResponseSerializer(serializers.Serializer):
    """Output for annotation endpoints."""

    id = serializers.UUIDField(read_only=True)
    instance_id = serializers.UUIDField(read_only=True)
    annotation_type = serializers.CharField(read_only=True)
    storage_ref = serializers.CharField(read_only=True)
    download_url = serializers.SerializerMethodField()
    page_number = serializers.IntegerField(read_only=True)
    x = serializers.FloatField(read_only=True)
    y = serializers.FloatField(read_only=True)
    problem_id = serializers.UUIDField(read_only=True, allow_null=True)
    author_id = serializers.UUIDField(read_only=True)
    author_email = serializers.SerializerMethodField()
    is_grade_annotation = serializers.BooleanField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    def get_author_email(self, obj) -> str:
        return obj.author.email if hasattr(obj, "author") else ""

    def get_download_url(self, obj) -> str | None:
        if obj.storage_ref:
            from apps.exams.services.storage import generate_presigned_url

            return generate_presigned_url(obj.storage_ref)
        return None


class OCRGradeResponseSerializer(serializers.Serializer):
    """Output for OCR grade extraction (RF-9.12)."""

    recognized_value = serializers.CharField(read_only=True, allow_null=True)
    confidence = serializers.FloatField(read_only=True)
    auto_applied = serializers.BooleanField(read_only=True)
    raw_value = serializers.CharField(read_only=True)


class OCRStylusResponseSerializer(serializers.Serializer):
    """Output for OCR on stylus (RF-9.13)."""

    recognized_text = serializers.CharField(read_only=True, allow_null=True)
    confidence = serializers.FloatField(read_only=True)
