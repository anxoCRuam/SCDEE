"""
Serializers for academic course endpoints.

References: RF-3.1, RF-3.2, RF-3.4
"""

from drf_spectacular.utils import OpenApiExample
from rest_framework import serializers

COURSE_DETAILS_ORG_MANAGER_EXAMPLE = OpenApiExample(
    name="CourseDetailsForManager",
    summary="Full course details returned to organisation managers",
    value={
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "label": "2025-2026",
        "start_date": "2024-09-01",
        "end_date": "2025-07-31",
        "is_active": True,
        "subject_count": 5,
        "created_at": "2025-01-01T00:00:00Z",
        "updated_at": "2025-01-01T00:00:00Z",
    },
    response_only=True,
)

COURSE_DETAILS_NON_MANAGER_EXAMPLE = OpenApiExample(
    name="CourseDetailsForNonManager",
    summary="Reduced course details returned to non‑manager users (active course only)",
    value={
        "id": "550e8400-e29b-41d4-a716-446655440001",
        "label": "2024-2025",
        "start_date": "2024-09-01",
        "end_date": "2025-07-31",
    },
    response_only=True,
)


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


class CourseActiveSerializer(serializers.Serializer):
    """Public output for GET /api/v1/courses/current/"""

    id = serializers.UUIDField(read_only=True)
    label = serializers.CharField(read_only=True)
    start_date = serializers.DateField(read_only=True)
    end_date = serializers.DateField(read_only=True)


class CourseSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    label = serializers.CharField(read_only=True)
    start_date = serializers.DateField(read_only=True)
    end_date = serializers.DateField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)
    subject_count = serializers.IntegerField(
        read_only=True,
        default=0,
        help_text="Number of subjects in this course (annotated).",
    )
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)
