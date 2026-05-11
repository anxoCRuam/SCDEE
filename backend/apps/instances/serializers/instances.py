"""
Serializers for exam instances, pages, and state transitions.

Reusable OpenAPI examples for the ``instances`` app.

Imported on demand from each view's ``@extend_schema`` decorator. Keeping
these out of ``views.py`` keeps the views focused on logic.

References: RF-7.x, RF-8.x, RF-11.x.
"""

from __future__ import annotations

from drf_spectacular.utils import OpenApiExample
from rest_framework import serializers

from apps.instances.models.instances import InstanceStatus
from apps.instances.services.access_control import can_access_instance_data

# ════════════════════════════════════════════════════════════════════
# Instance detail – role-based response examples
# ════════════════════════════════════════════════════════════════════

STUDENT_INSTANCE_BASIC_EXAMPLE = OpenApiExample(
    name="StudentInstanceBasic",
    summary="Student view – published, no active review window",
    description=(
        "Returned when the student's instance is published but there is no review window, "
        "or the window is not yet open / already closed without `can_view_pages_after_review`. "
        "Only basic fields and the total score are included."
    ),
    value={
        "id": "a1b2c3d4-...",
        "exam_name": "Parcial 1",
        "subject_name": "Álgebra",
        "status": "PUBLISHED",
        "total_score": "8.50",
    },
    response_only=True,
    status_codes=["200"],
)

STUDENT_INSTANCE_DETAILED_EXAMPLE = OpenApiExample(
    name="StudentInstanceDetailed",
    summary="Student view – during review window with all permissions active",
    description=(
        "Returned when the instance is `IN_REVIEW`, the review window is `OPEN`, "
        "and the exam's `student_permissions` grant access to pages, annotations, "
        "rubric, and grade breakdown. `can_download` reflects whether the student "
        "is allowed to download the composed PDF."
    ),
    value={
        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "exam_name": "Parcial 1",
        "subject_name": "Álgebra",
        "status": "IN_REVIEW",
        "total_score": "8.50",
        "pages": [
            {
                "id": "pg-uuid-1",
                "instance_id": "inst-uuid",
                "page_number": 1,
                "storage_ref": "org/exams/.../page1.png",
                "status": "RECOGNIZED",
                "issue_type": "",
                "recognized_at": "2026-05-01T10:00:00Z",
            }
        ],
        "rubric": [
            {
                "problem_name": "Problema 1",
                "max_score": "10.00",
                "criteria": [
                    {"description": "Correct algorithm", "score": "7.00"},
                    {"description": "Clean code", "score": "3.00"},
                ],
            }
        ],
        "grades": [
            {
                "problem_name": "Problema 1",
                "score": "8.50",
                "rubric_selections": ["rc1-uuid"],
            }
        ],
        "can_download": False,
    },
    response_only=True,
    status_codes=["200"],
)

STUDENT_INSTANCE_AFTER_REVIEW_EXAMPLE = OpenApiExample(
    name="StudentInstanceAfterReview",
    summary="Student view – after review window closed, can_view_pages_after_review=True",
    description=(
        "Returned when the review window has status `CLOSED` or `COMPLETED` but the exam has "
        "`can_view_pages_after_review` enabled. The data is identical to the detailed view, "
        "but the student can no longer submit review requests."
    ),
    value={
        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "exam_name": "Parcial 1",
        "subject_name": "Álgebra",
        "status": "FINALIZED",
        "total_score": "8.50",
        "pages": [
            {
                "id": "pg-uuid-1",
                "instance_id": "inst-uuid",
                "page_number": 1,
                "storage_ref": "org/exams/.../page1.png",
                "status": "RECOGNIZED",
                "issue_type": "",
                "recognized_at": "2026-05-01T10:00:00Z",
            }
        ],
        "rubric": [
            {
                "problem_name": "Problema 1",
                "max_score": "10.00",
                "criteria": [
                    {"description": "Correct algorithm", "score": "7.00"},
                    {"description": "Clean code", "score": "3.00"},
                ],
            }
        ],
        "grades": [
            {
                "problem_name": "Problema 1",
                "score": "8.50",
                "rubric_selections": ["rc1-uuid"],
            }
        ],
        "can_download": False,
    },
    response_only=True,
    status_codes=["200"],
)

MANAGER_INSTANCE_EXAMPLE = OpenApiExample(
    name="ManagerInstanceFull",
    summary="Manager view – identical to teacher view",
    description=(
        "Managers receive the same full response as teachers. The difference is that "
        "managers always pass permission checks, whereas teachers may be restricted "
        "by `can_view_all_instances` and `AssignmentRule` rules."
    ),
    value={
        "id": "11111111-2222-3333-4444-555555555555",
        "exam_id": "exam-uuid",
        "student_id": "student-uuid",
        "student_email": "maria.garcia@uam.es",
        "model_id": "model-uuid",
        "model_label": "A",
        "status": "GRADED",
        "has_issues": False,
        "issue_types": [],
        "total_score": "8.50",
        "expected_pages": 2,
        "page_ids": [
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
            "ffffffff-gggg-hhhh-iiii-jjjjjjjjjjjj",
        ],
        "created_at": "2026-05-01T09:00:00Z",
        "updated_at": "2026-05-01T12:00:00Z",
    },
    response_only=True,
    status_codes=["200"],
)

# ════════════════════════════════════════════════════════════════════
# Manual grading (RF-11.1) — optimistic concurrency
# ════════════════════════════════════════════════════════════════════


MANUAL_GRADE_REQUEST_EXAMPLE = OpenApiExample(
    name="ManualGradeRequest",
    summary="Set a problem score with optimistic concurrency",
    description=(
        "``old_version`` is the instance score the corrector last saw. The "
        "service compares it against the stored value and rejects the "
        "update with HTTP 409 ``CONFLICT`` if another corrector touched "
        "the instance in between."
    ),
    value={
        "score": "8.50",
        "old_score": "7.00",
    },
    request_only=True,
)

MANUAL_GRADE_RESPONSE_EXAMPLE = OpenApiExample(
    name="ManualGradeSuccess",
    summary="Updated grade + new instance version",
    value={
        "problem_id": "99999999-8888-7777-6666-555555555555",
        "problem_name": "P1",
        "score": "8.50",
        "graded_email": "teacher@a.com",
        "rubric_selections": [],
    },
    response_only=True,
    status_codes=["200"],
)


# ════════════════════════════════════════════════════════════════════
# State transition (RF-7.5)
# ════════════════════════════════════════════════════════════════════


TRANSITION_REQUEST_EXAMPLE = OpenApiExample(
    name="TransitionRequest",
    summary="Move PENDING_GRADING → GRADED",
    description=(
        "Allowed transitions are checked server-side. Invalid transitions "
        "return HTTP 409 with ``error_code: CONFLICT`` and the offending "
        "target in the audit log."
    ),
    value={"target_status": "GRADED"},
    request_only=True,
)


# ════════════════════════════════════════════════════════════════════
# Bulk publish (RF-7.9)
# ════════════════════════════════════════════════════════════════════


PUBLISH_RESPONSE_EXAMPLE = OpenApiExample(
    name="PublishResult",
    summary="Aggregated publish report",
    description=(
        "Returned even when nothing was published — the counts let the "
        "caller distinguish between ‘nothing to do’ and ‘some skipped "
        "due to issues’."
    ),
    value={
        "published": 287,
        "skipped": 13,
        "skipped_reasons": {
            "HAS_ISSUES": 9,
            "NOT_GRADED": 4,
        },
    },
    response_only=True,
    status_codes=["200"],
)


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
    page_ids = serializers.ListField(
        child=serializers.UUIDField(),
        read_only=True,
        default=list,
    )
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)

    def get_student_email(self, obj) -> str:
        return obj.student.email if obj.student else ""

    def get_model_label(self, obj) -> str:
        return obj.model.label if obj.model else ""


class StudentInstanceSerializer(serializers.Serializer):
    """Instance data for a student, respecting exam permissions and review window."""

    id = serializers.UUIDField(read_only=True)
    exam_name = serializers.CharField(source="exam.name", read_only=True)
    subject_name = serializers.CharField(source="exam.subject.name", read_only=True)
    status = serializers.CharField(read_only=True)
    total_score = serializers.DecimalField(
        read_only=True, max_digits=8, decimal_places=2, allow_null=True
    )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if not request:
            return data
        user = request.user

        exam = instance.exam
        perms = exam.student_permissions or {}

        if can_access_instance_data(instance, user, "can_view_pages"):
            data["pages"] = PageResponseSerializer(instance.pages.all(), many=True).data
        if can_access_instance_data(instance, user, "can_view_rubric"):
            try:
                from apps.exams.models.exams import Problem

                problems = Problem.objects.filter(exam_model=instance.model).prefetch_related(
                    "rubric_criteria"
                )
                data["rubric"] = [
                    {
                        "problem_name": p.name,
                        "max_score": str(p.max_score),
                        "criteria": [
                            {
                                "description": c.description,
                                "score": str(c.score),
                            }
                            for c in p.rubric_criteria.all()
                        ],
                    }
                    for p in problems
                ]
            except Exception:
                data["rubric"] = []
        if can_access_instance_data(instance, user, "can_view_grade_breakdown"):
            try:
                from apps.grading.models.grading import Grade

                grades = Grade.objects.filter(instance=instance).select_related("problem")
                data["grades"] = [
                    {
                        "problem_name": g.problem.name,
                        "score": str(g.score),
                        "rubric_selections": g.rubric_selections,
                    }
                    for g in grades
                ]
            except Exception:
                data["grades"] = []

        data["can_download"] = perms.get("can_download_pages", False)

        return data


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
    old_score = serializers.DecimalField(
        required=False,
        default=None,
        max_digits=8,
        decimal_places=2,
        allow_null=True,
        help_text="Last known score. If present, the update only succeeds if the "
        "stored score matches.",
    )


class RubricGradeSerializer(serializers.Serializer):
    """Input for PUT /instances/{id}/problems/{pid}/rubric/ (RF-11.2)."""

    criterion_ids = serializers.ListField(child=serializers.UUIDField(), required=True)
    old_score = serializers.DecimalField(
        required=False,
        default=None,
        max_digits=8,
        decimal_places=2,
        allow_null=True,
        help_text="Last known total score from rubric. If present, the update only succeeds "
        "if the stored score matches.",
    )


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
    updated_at = serializers.DateTimeField(read_only=True)


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


class PendingReviewSerializer(serializers.Serializer):
    review_request_id = serializers.UUIDField(read_only=True)
    instance_id = serializers.UUIDField(read_only=True)
    exam_name = serializers.CharField(read_only=True)
    student_email = serializers.CharField(read_only=True, allow_null=True)
    problem_name = serializers.CharField(read_only=True)
    problem_id = serializers.UUIDField(read_only=True)
    student_message = serializers.CharField(read_only=True)
    resolver_message = serializers.CharField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)


class CorrectorTaskSerializer(serializers.Serializer):
    instance_id = serializers.UUIDField(read_only=True)
    exam_name = serializers.CharField(read_only=True)
    student_email = serializers.CharField(read_only=True, allow_null=True)
    ungraded_problems = serializers.ListField(child=serializers.UUIDField(), read_only=True)
    problems_sent = serializers.ListField(child=serializers.UUIDField(), read_only=True)
    total_problems = serializers.IntegerField(read_only=True)


class MyTasksResponseSerializer(serializers.Serializer):
    grading_tasks = CorrectorTaskSerializer(many=True, read_only=True)
    pending_reviews = PendingReviewSerializer(many=True, read_only=True)


# ── Plain-result helpers ─────────────────────────────────────


class DetailResponseSerializer(serializers.Serializer):
    """Generic ``{"detail": "..."}`` payload for endpoints that report
    a status string (e.g. PageReorderView, PageMoveView)."""

    detail = serializers.CharField(read_only=True)
