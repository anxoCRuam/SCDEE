"""
Serializers for exams, models, page profiles, zones, problems,
rubrics, and convocations.

References: RF-6.1 through RF-6.16
"""

from drf_spectacular.utils import OpenApiExample, extend_schema_field
from rest_framework import serializers

from apps.exams.models.exams import ZoneType

EXAM_FULL_DETAILS_EXAMPLE = OpenApiExample(
    name="ExamFullDetails",
    summary="Manager/coordinator view: complete exam structure",
    value={
        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "name": "Parcial 1",
        "date": "2025-05-10",
        "subject_name": "Álgebra",
        "subject_code": "ALG101",
        "models_problems": [
            {
                "model_id": "m1-uuid",
                "label": "A",
                "problems": [
                    {
                        "id": "p1-uuid",
                        "name": "Problema 1",
                        "max_score": "10.00",
                        "order": 1,
                        "exam_model_id": "m1-uuid",
                        "zone_ids": ["z1-uuid", "z2-uuid"],
                        "rubric_criteria": [
                            {
                                "id": "rc1-uuid",
                                "description": "Correct algorithm",
                                "score": "7.00",
                                "order": 0,
                            },
                            {
                                "id": "rc2-uuid",
                                "description": "Clean code",
                                "score": "3.00",
                                "order": 1,
                            },
                        ],
                    }
                ],
            },
            {
                "model_id": "m2-uuid",
                "label": "B",
                "problems": [],
            },
        ],
        "convocation": [
            {
                "id": "student-uuid",
                "email": "student@uam.es",
                "first_name": "María",
                "last_name": "García",
            }
        ],
        "models": ["m1-uuid", "m2-uuid"],
        "student_permissions": {
            "can_view_pages": True,
            "can_view_pages_after_review": False,
            "can_download_pages": False,
            "can_view_annotations": True,
            "can_view_rubric": True,
            "can_view_grade_breakdown": True,
        },
    },
    response_only=True,
    status_codes=["200"],
)
EXAM_TEACHER_DETAILS_EXAMPLE = OpenApiExample(
    name="ExamTeacherDetails",
    summary="Teacher view: basic info + problems with rubrics + convocation list",
    value={
        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "name": "Parcial 1",
        "date": "2025-05-10",
        "subject_name": "Álgebra",
        "subject_code": "ALG101",
        "models_problems": [
            {
                "model_id": "m1-uuid",
                "label": "A",
                "problems": [
                    {
                        "id": "p1-uuid",
                        "name": "Problema 1",
                        "max_score": "10.00",
                        "order": 1,
                        "exam_model_id": "m1-uuid",
                        "zone_ids": ["z1-uuid", "z2-uuid"],
                        "rubric_criteria": [
                            {
                                "id": "rc1-uuid",
                                "description": "Correct algorithm",
                                "score": "7.00",
                                "order": 0,
                            },
                            {
                                "id": "rc2-uuid",
                                "description": "Clean code",
                                "score": "3.00",
                                "order": 1,
                            },
                        ],
                    }
                ],
            },
            {
                "model_id": "m2-uuid",
                "label": "B",
                "problems": [],
            },
        ],
        "convocation": [
            {
                "id": "student-uuid",
                "email": "maria.garcia@uam.es",
                "first_name": "María",
                "last_name": "García",
            }
        ],
    },
    response_only=True,
    status_codes=["200"],
)
EXAM_BASIC_DETAILS_EXAMPLE = OpenApiExample(
    name="ExamBasicDetails",
    summary="Basic exam information (student view)",
    value={
        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "name": "Parcial 1",
        "date": "2025-05-10",
        "subject_name": "Álgebra",
        "subject_code": "ALG101",
    },
    response_only=True,
    status_codes=["200"],
)

# ── Generic helpers for upload / async-task endpoints ────────


class BlankPDFUploadRequestSerializer(serializers.Serializer):
    """Input for PUT /models/{id}/blank-pdf/ (RF-6.7)."""

    file = serializers.FileField(required=True, help_text="PDF file (single document).")


class PageDimensionSerializer(serializers.Serializer):
    page = serializers.IntegerField(read_only=True)
    width = serializers.FloatField(read_only=True)
    height = serializers.FloatField(read_only=True)


class BlankPDFUploadResponseSerializer(serializers.Serializer):
    """Output for the blank-PDF upload endpoint (RF-6.7, RF-6.14)."""

    blank_pdf_ref = serializers.CharField(read_only=True)
    pages = serializers.IntegerField(read_only=True)
    page_dimensions = PageDimensionSerializer(many=True, read_only=True)
    size_bytes = serializers.IntegerField(read_only=True)


class ConvocationStudentSerializer(serializers.Serializer):
    """Basic student info for convocation listing."""

    id = serializers.UUIDField(read_only=True, source="student.id")
    email = serializers.EmailField(read_only=True, source="student.email")
    first_name = serializers.CharField(read_only=True, source="student.first_name")
    last_name = serializers.CharField(read_only=True, source="student.last_name")


# ── Exam ─────────────────────────────────────────────────────


class CreateExamSerializer(serializers.Serializer):
    """Input for POST /subjects/{id}/exams/ (RF-6.1)."""

    name = serializers.CharField(required=True, max_length=255)
    date = serializers.DateTimeField(required=False)


class UpdateExamSerializer(serializers.Serializer):
    """Input for PATCH /exams/{id}/ (RF-6.2, RF-6.10)."""

    name = serializers.CharField(required=False, max_length=255)
    date = serializers.DateTimeField(required=False)
    student_permissions = serializers.DictField(
        required=False,
        child=serializers.BooleanField(),
        help_text="Student visibility permissions (RF-6.10).",
    )


class ExamBasicSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    date = serializers.DateTimeField(read_only=True)
    subject_id = serializers.UUIDField(read_only=True)
    subject_name = serializers.CharField(read_only=True, source="subject.name")
    subject_code = serializers.CharField(read_only=True, source="subject.code")


class ExamTeacherSerializer(ExamBasicSerializer):
    """Teacher view: basic info + problems grouped by model with rubrics + convocation list."""

    models_problems = serializers.SerializerMethodField(
        help_text="List of models with their problems and rubrics"
    )
    convocation = serializers.SerializerMethodField()

    @extend_schema_field(
        serializers.ListField(
            child=serializers.DictField(
                child=serializers.CharField()  # Simplificado, pero podemos ser más precisos
            )
        )
    )
    def get_models_problems(self, obj):
        # obj.models está prefeteado con 'problems__rubric_criteria'
        result = []
        for model in obj.models.all():
            result.append(
                {
                    "model_id": str(model.id),
                    "label": model.label,
                    "problems": ProblemResponseSerializer(model.problems.all(), many=True).data,
                }
            )
        return result

    @extend_schema_field(ConvocationStudentSerializer(many=True))
    def get_convocation(self, obj):
        # obj.convocations está prefeteado con 'student'
        return ConvocationStudentSerializer(obj.convocations.all(), many=True).data


class ExamFullSerializer(ExamTeacherSerializer):
    """Full exam detail for managers/coordinators"""

    student_permissions = serializers.JSONField(read_only=True)


# ── RecognitionZone ──────────────────────────────────────────


class CreateZoneSerializer(serializers.Serializer):
    """Input for POST /page-profiles/{id}/zones/ (RF-6.8)."""

    zone_type = serializers.ChoiceField(choices=ZoneType.choices, required=True)
    attribute = serializers.CharField(required=True, max_length=100)
    x = serializers.FloatField(required=True)
    y = serializers.FloatField(required=True)
    width = serializers.FloatField(required=True, min_value=0.1)
    height = serializers.FloatField(required=True, min_value=0.1)
    page_numbers = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        help_text="Optional list of page numbers to create the same zone on. "
        "If provided, the URL's profile_pk is ignored (but must still be valid).",
    )


class ZoneResponseSerializer(serializers.Serializer):
    """Output for zone endpoints."""

    id = serializers.UUIDField(read_only=True)
    zone_type = serializers.CharField(read_only=True)
    attribute = serializers.CharField(read_only=True)
    x = serializers.FloatField(read_only=True)
    y = serializers.FloatField(read_only=True)
    width = serializers.FloatField(read_only=True)
    height = serializers.FloatField(read_only=True)
    page_profile_id = serializers.UUIDField(read_only=True)


# ── PageProfile ──────────────────────────────────────────────


class CreatePageProfileSerializer(serializers.Serializer):
    """Input for POST /models/{id}/page-profiles/ (RF-6.8)."""

    page_number = serializers.IntegerField(required=True, min_value=1)


class PageProfileResponseSerializer(serializers.Serializer):
    """Output for page profile endpoints."""

    id = serializers.UUIDField(read_only=True)
    page_number = serializers.IntegerField(read_only=True)
    page_width = serializers.FloatField(read_only=True)
    page_height = serializers.FloatField(read_only=True)
    zones = ZoneResponseSerializer(many=True, read_only=True)  # prefetch


# ── ExamModel ────────────────────────────────────────────────


class CreateModelSerializer(serializers.Serializer):
    """Input for POST /exams/{id}/models/ (RF-6.5)."""

    label = serializers.CharField(required=True, max_length=10)
    copy_from_id = serializers.UUIDField(
        required=False,
        allow_null=True,
        default=None,
        help_text="Optional model ID to copy structure from.",
    )


class ModelDetailSerializer(serializers.Serializer):
    """Full model detail: all fields + nested page profiles with zones."""

    id = serializers.UUIDField(read_only=True)
    label = serializers.CharField(read_only=True)
    exam_id = serializers.UUIDField(read_only=True)
    blank_pdf_ref = serializers.CharField(read_only=True)
    blank_pdf_pages = serializers.IntegerField(read_only=True)
    blank_pdf_size = serializers.IntegerField(read_only=True)
    page_dimensions = serializers.JSONField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    updated_at = serializers.DateTimeField(read_only=True)
    page_profiles = PageProfileResponseSerializer(many=True, read_only=True)


# ── RubricCriterion ──────────────────────────────────────────


class RubricCriterionInputSerializer(serializers.Serializer):
    """Single criterion in a rubric definition."""

    description = serializers.CharField(required=True)
    score = serializers.DecimalField(
        required=True,
        max_digits=6,
        decimal_places=2,
    )


class SetRubricSerializer(serializers.Serializer):
    """Input for POST /problems/{id}/rubric/ (RF-6.11)."""

    criteria = RubricCriterionInputSerializer(many=True, required=True)


class RubricCriterionResponseSerializer(serializers.Serializer):
    """Output for the rubric-set endpoint."""

    id = serializers.UUIDField(read_only=True)
    description = serializers.CharField(read_only=True)
    score = serializers.CharField(read_only=True)  # Decimal as string.
    order = serializers.IntegerField(read_only=True)


# ── Problem ──────────────────────────────────────────────────


class CreateProblemSerializer(serializers.Serializer):
    """Input for POST /models/{id}/problems/ (RF-6.9)."""

    name = serializers.CharField(required=True, max_length=255)
    max_score = serializers.DecimalField(
        required=True,
        max_digits=6,
        decimal_places=2,
    )
    order = serializers.IntegerField(required=False, default=0)
    zone_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        default=list,
        help_text="Optional list of RecognitionZone IDs for answer regions.",
    )


class ProblemResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    max_score = serializers.DecimalField(read_only=True, max_digits=6, decimal_places=2)
    order = serializers.IntegerField(read_only=True)
    exam_model_id = serializers.UUIDField(read_only=True)
    zone_ids = serializers.ListField(
        child=serializers.UUIDField(),
        read_only=True,
        source="zones.values_list",
    )
    rubric_criteria = RubricCriterionResponseSerializer(many=True, read_only=True)


# ── Convocation ──────────────────────────────────────────────


class SetConvocationSerializer(serializers.Serializer):
    """Input for PUT /exams/{id}/convocation/ (RF-6.4)."""

    student_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=True,
        help_text="List of student user IDs to call for this exam.",
    )


class ConvocationResponseSerializer(serializers.Serializer):
    """Output for convocation endpoint."""

    total = serializers.IntegerField(read_only=True)
    valid = serializers.IntegerField(read_only=True)
    invalid = serializers.ListField(read_only=True)
