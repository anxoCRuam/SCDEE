"""
Serializers for annotation endpoints.

References: RF-10.1 through RF-10.7
"""

from rest_framework import serializers

from apps.annotations.models import AnnotationType


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
    """Input for PUT /annotations/{id}/ (RF-10.2)."""

    payload = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Updated content (text or stylus JSON).",
    )


class AnnotationResponseSerializer(serializers.Serializer):
    """Output for annotation endpoints."""

    id = serializers.UUIDField(read_only=True)
    instance_id = serializers.UUIDField(read_only=True)
    annotation_type = serializers.CharField(read_only=True)
    payload = serializers.CharField(read_only=True)
    storage_ref = serializers.CharField(read_only=True)
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
