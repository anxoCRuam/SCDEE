"""
Serializers for exam instances, pages, and state transitions.
"""

from rest_framework import serializers

from apps.instances.models import InstanceStatus


class InstanceResponseSerializer(serializers.Serializer):
    """Output for instance endpoints."""

    id = serializers.UUIDField(read_only=True)
    exam_id = serializers.UUIDField(read_only=True)
    student_id = serializers.UUIDField(read_only=True, allow_null=True)
    student_email = serializers.SerializerMethodField()
    model_id = serializers.UUIDField(read_only=True, allow_null=True)
    model_label = serializers.SerializerMethodField()
    status = serializers.CharField(read_only=True)
    has_issues = serializers.BooleanField(read_only=True)
    issue_types = serializers.JSONField(read_only=True)
    total_score = serializers.DecimalField(
        read_only=True,
        max_digits=8,
        decimal_places=2,
        allow_null=True,
    )
    expected_pages = serializers.IntegerField(read_only=True)
    page_count = serializers.IntegerField(read_only=True, default=0)
    created_at = serializers.DateTimeField(read_only=True)

    def get_student_email(self, obj) -> str:
        return obj.student.email if obj.student else ""

    def get_model_label(self, obj) -> str:
        return obj.model.label if obj.model else ""


class UpdateInstanceSerializer(serializers.Serializer):
    """Input for PATCH /instances/{id}/ (RF-7.2)."""

    student_id = serializers.UUIDField(required=False)
    model_id = serializers.UUIDField(required=False)


class TransitionSerializer(serializers.Serializer):
    """Input for PATCH /instances/{id}/transition/ (RF-7.5)."""

    target_status = serializers.ChoiceField(choices=InstanceStatus.choices, required=True)


class PageResponseSerializer(serializers.Serializer):
    """Output for page endpoints."""

    id = serializers.UUIDField(read_only=True)
    instance_id = serializers.UUIDField(read_only=True, allow_null=True)
    page_number = serializers.IntegerField(read_only=True)
    storage_ref = serializers.CharField(read_only=True)
    status = serializers.CharField(read_only=True)
    issue_type = serializers.CharField(read_only=True, allow_null=False)
    recognized_at = serializers.DateTimeField(read_only=True, allow_null=True)


class ReorderPagesSerializer(serializers.Serializer):
    """Input for PATCH /instances/{id}/pages/reorder/ (RF-7.14)."""

    page_ids = serializers.ListField(child=serializers.UUIDField(), required=True)


class MovePageSerializer(serializers.Serializer):
    """Input for POST /instances/{id}/pages/{pid}/move/ (RF-7.14)."""

    target_instance_id = serializers.UUIDField(required=True)


class AttachPageSerializer(serializers.Serializer):
    """Input for POST /instances/{id}/pages/attach/ (RF-7.14)."""

    page_id = serializers.UUIDField(required=True)


# ── Grading serializers ──────────────────────────────────────


class ManualGradeSerializer(serializers.Serializer):
    """Input for PUT /instances/{id}/problems/{pid}/grade/ (RF-11.1)."""

    score = serializers.DecimalField(required=True, max_digits=8, decimal_places=2)
    prev_score = serializers.DecimalField(required=True, max_digits=8, decimal_places=2)


class RubricGradeSerializer(serializers.Serializer):
    """Input for PUT /instances/{id}/problems/{pid}/rubric/ (RF-11.2)."""

    criterion_ids = serializers.ListField(child=serializers.UUIDField(), required=True)
    prev_score = serializers.DecimalField(required=True, max_digits=8, decimal_places=2)


class GradeResponseSerializer(serializers.Serializer):
    """Output for grade operations."""

    problem_id = serializers.UUIDField(read_only=True)
    problem_name = serializers.SerializerMethodField()
    score = serializers.DecimalField(read_only=True, max_digits=8, decimal_places=2)
    grader_email = serializers.SerializerMethodField()
    rubric_selections = serializers.JSONField(read_only=True)

    def get_problem_name(self, obj) -> str:
        return obj.problem.name if hasattr(obj, "problem") else ""

    def get_grader_email(self, obj) -> str:
        return obj.grader.email if hasattr(obj, "grader") else ""


# ── Assignment rule serializers ──────────────────────────────


class AssignmentRuleSerializer(serializers.Serializer):
    """Input for POST /exams/{id}/assignment-rules/ (RF-8.1)."""

    groups = serializers.ListField(child=serializers.UUIDField(), required=False, default=list)
    models_filter = serializers.ListField(
        child=serializers.UUIDField(), required=False, default=list
    )
    problems = serializers.ListField(child=serializers.UUIDField(), required=True)
    correctors = serializers.ListField(child=serializers.UUIDField(), required=True)


class AssignmentRuleResponseSerializer(serializers.Serializer):
    """Output for assignment rule endpoints."""

    id = serializers.UUIDField(read_only=True)
    exam_id = serializers.UUIDField(read_only=True)
    groups = serializers.JSONField(read_only=True)
    models_filter = serializers.JSONField(read_only=True)
    problems = serializers.JSONField(read_only=True)
    correctors = serializers.JSONField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


# ── Bulk publish (RF-7.9) ────────────────────────────────────


class PublishErrorSerializer(serializers.Serializer):
    """Single error entry returned by the bulk-publish endpoint."""

    instance_id = serializers.UUIDField(read_only=True)
    reason = serializers.CharField(read_only=True)


class PublishResultSerializer(serializers.Serializer):
    """Output for POST /exams/{id}/publish/ (RF-7.9)."""

    published = serializers.IntegerField(read_only=True)
    skipped = serializers.IntegerField(read_only=True)
    total = serializers.IntegerField(read_only=True)
    errors = PublishErrorSerializer(many=True, read_only=True)


# ── Coverage check (RF-7.8, RF-8.5) ──────────────────────────


class UncoveredProblemSerializer(serializers.Serializer):
    problem_id = serializers.UUIDField(read_only=True)
    problem_name = serializers.CharField(read_only=True)
    model = serializers.CharField(read_only=True)


class CoverageResponseSerializer(serializers.Serializer):
    """Output for GET /exams/{id}/coverage/."""

    complete = serializers.BooleanField(read_only=True)
    total_problems = serializers.IntegerField(read_only=True)
    covered_problems = serializers.IntegerField(read_only=True)
    uncovered_problems = UncoveredProblemSerializer(many=True, read_only=True)
    total_instances = serializers.IntegerField(read_only=True)
    rules_count = serializers.IntegerField(read_only=True)


# ── My-tasks (RF-8.6) ────────────────────────────────────────


class CorrectorTaskSerializer(serializers.Serializer):
    instance_id = serializers.UUIDField(read_only=True)
    exam_name = serializers.CharField(read_only=True)
    student_email = serializers.CharField(read_only=True, allow_null=True)
    ungraded_problems = serializers.ListField(child=serializers.UUIDField(), read_only=True)
    total_problems = serializers.IntegerField(read_only=True)


# ── Plain-result helpers ─────────────────────────────────────


class DetailResponseSerializer(serializers.Serializer):
    """Generic ``{"detail": "..."}`` payload for endpoints that report
    a status string (e.g. PageReorderView, PageMoveView)."""

    detail = serializers.CharField(read_only=True)
