"""
Serializers for reviews and review requests.
"""

from rest_framework import serializers


class CreateReviewSerializer(serializers.Serializer):
    """Input for POST /exams/{id}/review/ (RF-12.1)."""

    start_date = serializers.DateTimeField(required=True)
    end_date = serializers.DateTimeField(required=True)
    notify_students = serializers.BooleanField(required=False, default=False)


class ReviewResponseSerializer(serializers.Serializer):
    """Output for review endpoints."""

    id = serializers.UUIDField(read_only=True)
    exam_id = serializers.UUIDField(read_only=True)
    start_date = serializers.DateTimeField(read_only=True)
    end_date = serializers.DateTimeField(read_only=True)
    status = serializers.CharField(read_only=True)
    request_count = serializers.IntegerField(read_only=True, default=0)
    created_at = serializers.DateTimeField(read_only=True)


class SubmitReviewRequestSerializer(serializers.Serializer):
    """Input for POST /instances/{id}/review-requests/ (RF-12.4)."""

    problems = serializers.ListField(
        child=serializers.DictField(),
        required=True,
        help_text='List of {"problem_id": "uuid", "message": "optional text"}.',
    )


class ReviewRequestResponseSerializer(serializers.Serializer):
    """Output for review request endpoints."""

    id = serializers.UUIDField(read_only=True)
    instance_id = serializers.UUIDField(read_only=True)
    problem_id = serializers.UUIDField(read_only=True)
    problem_name = serializers.SerializerMethodField()
    student_message = serializers.CharField(read_only=True)
    resolved = serializers.BooleanField(read_only=True)
    resolved_at = serializers.DateTimeField(read_only=True, allow_null=True)
    resolver_message = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)

    def get_problem_name(self, obj) -> str:
        return obj.problem.name if hasattr(obj, "problem") else ""


class ResolveRequestSerializer(serializers.Serializer):
    """Input for PATCH /review-requests/{id}/resolve/ (RF-12.7)."""

    resolver_message = serializers.CharField(required=False, default="", allow_blank=True)
