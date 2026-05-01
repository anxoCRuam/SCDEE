"""
Serializers for academic course endpoints.

References: RF-3.1, RF-3.2, RF-3.4
"""

from rest_framework import serializers


class CreateCourseSerializer(serializers.Serializer):
    """Input for POST /api/v1/courses/ (RF-3.1)."""

    label = serializers.CharField(
        required=True,
        max_length=50,
        help_text='Academic period label (e.g. "2025-2026").',
    )
    start_date = serializers.DateField(
        required=False,
        allow_null=True,
        default=None,
        help_text="Informational start date.",
    )
    end_date = serializers.DateField(
        required=False,
        allow_null=True,
        default=None,
        help_text="Informational end date.",
    )


class UpdateCourseSerializer(serializers.Serializer):
    """Input for PATCH /api/v1/courses/{id}/ (RF-3.2).

    All fields are optional (PATCH semantics).
    """

    label = serializers.CharField(required=False, max_length=50)
    start_date = serializers.DateField(required=False, allow_null=True)
    end_date = serializers.DateField(required=False, allow_null=True)


class CourseResponseSerializer(serializers.Serializer):
    """Output for course data (RF-3.4).

    ``subject_count`` is annotated on the queryset by the list/detail
    views (``Count("subject_set")``).
    """

    id = serializers.UUIDField(read_only=True)
    label = serializers.CharField(read_only=True)
    start_date = serializers.DateField(read_only=True)
    end_date = serializers.DateField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    status = serializers.CharField(read_only=True)
    subject_count = serializers.IntegerField(
        read_only=True,
        default=0,
        help_text="Number of subjects in this course (annotated).",
    )
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)
