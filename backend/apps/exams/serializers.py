"""
Serializers for exams, models, page profiles, zones, problems,
rubrics, and convocations.

References: RF-6.1 through RF-6.16
"""

from rest_framework import serializers

from apps.exams.models import ZoneType

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


class InstrumentedPDFGenerationResponseSerializer(serializers.Serializer):
    """Output for POST /models/{id}/generate-instrumented-pdf/ (RF-6.15)."""

    instrumented_pdf_ref = serializers.CharField(read_only=True)
    instrumented_pdf_valid = serializers.BooleanField(read_only=True)
    size_bytes = serializers.IntegerField(read_only=True)


# ── Exam ─────────────────────────────────────────────────────


class CreateExamSerializer(serializers.Serializer):
    """Input for POST /subjects/{id}/exams/ (RF-6.1)."""

    name = serializers.CharField(required=True, max_length=255)


class UpdateExamSerializer(serializers.Serializer):
    """Input for PATCH /exams/{id}/ (RF-6.2, RF-6.10)."""

    name = serializers.CharField(required=False, max_length=255)
    student_permissions = serializers.DictField(
        required=False,
        child=serializers.BooleanField(),
        help_text="Student visibility permissions (RF-6.10).",
    )


class ExamResponseSerializer(serializers.Serializer):
    """Output for exam endpoints."""

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    subject_id = serializers.UUIDField(read_only=True)
    student_permissions = serializers.JSONField(read_only=True)
    model_count = serializers.IntegerField(read_only=True, default=0)
    convocation_count = serializers.IntegerField(read_only=True, default=0)
    created_at = serializers.DateTimeField(read_only=True)


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


class ModelResponseSerializer(serializers.Serializer):
    """Output for model endpoints."""

    id = serializers.UUIDField(read_only=True)
    label = serializers.CharField(read_only=True)
    exam_id = serializers.UUIDField(read_only=True)
    blank_pdf_ref = serializers.CharField(read_only=True)
    blank_pdf_pages = serializers.IntegerField(read_only=True)
    instrumented_pdf_valid = serializers.BooleanField(read_only=True)
    problem_count = serializers.IntegerField(read_only=True, default=0)
    page_profile_count = serializers.IntegerField(read_only=True, default=0)
    created_at = serializers.DateTimeField(read_only=True)


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
    zones = serializers.SerializerMethodField()

    def get_zones(self, obj) -> list[dict]:
        if hasattr(obj, "_prefetched_objects_cache") and "zones" in obj._prefetched_objects_cache:
            zones = obj._prefetched_objects_cache["zones"]
        else:
            zones = obj.zones.all()
        return [
            {
                "id": str(z.pk),
                "zone_type": z.zone_type,
                "attribute": z.attribute,
                "x": z.x,
                "y": z.y,
                "width": z.width,
                "height": z.height,
            }
            for z in zones
        ]


# ── RecognitionZone ──────────────────────────────────────────


class CreateZoneSerializer(serializers.Serializer):
    """Input for POST /page-profiles/{id}/zones/ (RF-6.8)."""

    zone_type = serializers.ChoiceField(choices=ZoneType.choices, required=True)
    attribute = serializers.CharField(required=True, max_length=100)
    x = serializers.FloatField(required=True)
    y = serializers.FloatField(required=True)
    width = serializers.FloatField(required=True, min_value=0.1)
    height = serializers.FloatField(required=True, min_value=0.1)


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
    """Output for problem endpoints."""

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    max_score = serializers.DecimalField(
        read_only=True,
        max_digits=6,
        decimal_places=2,
    )
    order = serializers.IntegerField(read_only=True)
    exam_model_id = serializers.UUIDField(read_only=True)
    zone_ids = serializers.SerializerMethodField()
    rubric_criteria = serializers.SerializerMethodField()

    def get_zone_ids(self, obj) -> list[str]:
        return [str(z.pk) for z in obj.zones.all()]

    def get_rubric_criteria(self, obj) -> list[dict]:
        return [
            {
                "id": str(c.pk),
                "description": c.description,
                "score": str(c.score),
                "order": c.order,
            }
            for c in obj.rubric_criteria.all()
        ]


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


# ── Exam detail (nested) ────────────────────────────────────


class ExamDetailSerializer(serializers.Serializer):
    """Full exam detail with nested models, profiles, problems (RF-6.13)."""

    id = serializers.UUIDField(read_only=True)
    name = serializers.CharField(read_only=True)
    subject_id = serializers.UUIDField(read_only=True)
    student_permissions = serializers.JSONField(read_only=True)
    created_at = serializers.DateTimeField(read_only=True)
    models = serializers.SerializerMethodField()
    convocation_count = serializers.IntegerField(read_only=True, default=0)

    def get_models(self, obj) -> list[dict]:
        result = []
        for model in obj.models.prefetch_related(
            "page_profiles__zones", "problems__rubric_criteria", "problems__zones"
        ).all():
            profiles = []
            for p in model.page_profiles.all():
                profiles.append(
                    {
                        "id": str(p.pk),
                        "page_number": p.page_number,
                        "page_width": p.page_width,
                        "page_height": p.page_height,
                        "zones": [
                            {
                                "id": str(z.pk),
                                "zone_type": z.zone_type,
                                "attribute": z.attribute,
                                "x": z.x,
                                "y": z.y,
                                "width": z.width,
                                "height": z.height,
                            }
                            for z in p.zones.all()
                        ],
                    }
                )

            problems = []
            for prob in model.problems.all():
                problems.append(
                    {
                        "id": str(prob.pk),
                        "name": prob.name,
                        "max_score": str(prob.max_score),
                        "order": prob.order,
                        "zone_ids": [str(z.pk) for z in prob.zones.all()],
                        "rubric": [
                            {
                                "id": str(c.pk),
                                "description": c.description,
                                "score": str(c.score),
                            }
                            for c in prob.rubric_criteria.all()
                        ],
                    }
                )

            result.append(
                {
                    "id": str(model.pk),
                    "label": model.label,
                    "blank_pdf_pages": model.blank_pdf_pages,
                    "instrumented_pdf_valid": model.instrumented_pdf_valid,
                    "page_profiles": profiles,
                    "problems": problems,
                }
            )
        return result
