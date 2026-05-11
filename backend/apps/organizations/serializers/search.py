"""
Serializers for organization config management, search engine and export.

References: RF-14
"""

from drf_spectacular.utils import OpenApiExample
from rest_framework import serializers

from apps.exams.serializers.exams import ExamBasicSerializer


class ExamListSerializer(ExamBasicSerializer):
    model_count = serializers.SerializerMethodField()
    instance_count = serializers.SerializerMethodField()

    def get_model_count(self, obj) -> int:
        return obj.models.count()

    def get_instance_count(self, obj) -> int:
        return obj.instances.count()


class InstanceListSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    student_email = serializers.EmailField(
        source="student.email", read_only=True, allow_null=True, default=None
    )
    model_label = serializers.CharField(
        source="model.label", read_only=True, allow_null=True, default=None
    )
    status = serializers.CharField(read_only=True)
    has_issues = serializers.BooleanField(read_only=True)
    total_score = serializers.DecimalField(
        max_digits=6, decimal_places=2, read_only=True, allow_null=True
    )
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)


SEARCH_COURSE_LIST_EXAMPLE = OpenApiExample(
    "Courses example",
    summary="Response when listing courses",
    value={
        "count": 2,
        "page": 1,
        "page_size": 25,
        "total_pages": 1,
        "next": "string",
        "previous": "string",
        "results": [
            {
                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "label": "string",
                "is_active": True,
                "subject_count": 0,
                "created_at": "2023-10-01T00:00:00Z",
                "updated_at": "2023-10-01T00:00:00Z",
            }
        ],
    },
    response_only=True,
)

SEARCH_SUBJECT_LIST_EXAMPLE = OpenApiExample(
    "Subjects example",
    summary="Response when listing subjects",
    value={
        "count": 2,
        "page": 1,
        "page_size": 25,
        "total_pages": 1,
        "next": "string",
        "previous": "string",
        "results": [
            {
                "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
                "name": "Matemáticas I",
                "code": "MAT1",
                "semester": "1er cuatrimestre",
                "course_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                "coordinator_id": "123e4567-e89b-12d3-a456-426614174000",
                "coordinator_email": "coordinador@universidad.es",
                "created_at": "2026-05-04T10:30:00Z",
                "updated_at": "2026-05-04T10:30:00Z",
                "groups": [{"id": "g1-uuid", "label": "G1"}, {"id": "g2-uuid", "label": "G2"}],
                "member_counts": {"coordinators": 1, "teachers": 3, "students": 45},
            }
        ],
    },
    response_only=True,
)

SEARCH_EXAM_LIST_EXAMPLE = OpenApiExample(
    "Exams example",
    summary="Response when listing exams",
    value={
        "count": 2,
        "page": 1,
        "page_size": 25,
        "total_pages": 1,
        "next": "string",
        "previous": "string",
        "results": [
            {
                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "name": "string",
                "date": "2025-05-10",
                "model_count": 0,
                "instance_count": 0,
                "created_at": "2026-05-04T10:30:00Z",
                "updated_at": "2026-05-04T10:30:00Z",
            }
        ],
    },
    response_only=True,
)

SEARCH_INSTANCE_LIST_EXAMPLE = OpenApiExample(
    "Instances example",
    summary="Response when listing instances",
    value={
        "count": 2,
        "page": 1,
        "page_size": 25,
        "total_pages": 1,
        "next": "string",
        "previous": "string",
        "results": [
            {
                "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
                "student_email": "user@example.com",
                "model_label": "string",
                "status": "string",
                "has_issues": True,
                "total_score": "37",
                "created_at": "2026-05-04T10:30:00Z",
                "updated_at": "2026-05-04T10:30:00Z",
            }
        ],
    },
    response_only=True,
)
