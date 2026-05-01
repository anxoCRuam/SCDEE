"""
Exam-related API views.

Endpoints:
    POST   /subjects/{id}/exams/                    — Create exam (RF-6.1)
    GET    /subjects/{id}/exams/                    — List exams (RF-6.12)
    GET    /exams/{id}/                             — Exam detail (RF-6.13)
    PATCH  /exams/{id}/                             — Update exam (RF-6.2)
    DELETE /exams/{id}/                             — Delete exam (RF-6.3)
    POST   /exams/{id}/models/                     — Create model (RF-6.5)
    PUT    /models/{id}/blank-pdf/                  — Upload blank PDF (RF-6.7)
    POST   /models/{id}/page-profiles/              — Create page profile (RF-6.8)
    POST   /page-profiles/{id}/zones/               — Create zone (RF-6.8)
    DELETE /zones/{id}/                             — Delete zone (RF-6.8)
    POST   /models/{id}/problems/                   — Create problem (RF-6.9)
    POST   /problems/{id}/rubric/                   — Set rubric (RF-6.11)
    PUT    /exams/{id}/convocation/                  — Set convocation (RF-6.4)
    POST   /models/{id}/generate-instrumented-pdf/  — Generate PDF (RF-6.15)
    GET    /models/{id}/instrumented-pdf/            — Download PDF (RF-6.16)

References: RF-6.1 through RF-6.16, RF-16.1
"""

from __future__ import annotations

import logging

from django.http import HttpResponse
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services import (
    EXAM_CREATED,
    EXAM_DELETED,
    EXAM_UPDATED,
    get_client_ip,
    log_event,
)
from apps.exams.models import (
    Exam,
    ExamModel,
    PageProfile,
    Problem,
    RecognitionZone,
)
from apps.exams.serializers import (
    BlankPDFUploadRequestSerializer,
    BlankPDFUploadResponseSerializer,
    ConvocationResponseSerializer,
    CreateExamSerializer,
    CreateModelSerializer,
    CreatePageProfileSerializer,
    CreateProblemSerializer,
    CreateZoneSerializer,
    ExamDetailSerializer,
    ExamResponseSerializer,
    InstrumentedPDFGenerationResponseSerializer,
    ModelResponseSerializer,
    PageProfileResponseSerializer,
    ProblemResponseSerializer,
    RubricCriterionResponseSerializer,
    SetConvocationSerializer,
    SetRubricSerializer,
    UpdateExamSerializer,
    ZoneResponseSerializer,
)
from apps.exams.services.exam_service import (
    ExamServiceError,
    create_exam,
    create_model,
    create_page_profile,
    create_problem,
    create_zone,
    delete_exam,
    delete_zone,
    set_convocation,
    set_rubric,
    update_exam,
    upload_blank_pdf,
)
from apps.subjects.models import Subject

logger = logging.getLogger(__name__)


def _can_manage_exam(user, subject) -> bool:
    """Check if user can manage exams in a subject."""
    if user.is_staff or user.is_superadmin:
        return True
    if subject.coordinator_id == user.pk:
        return True
    # Check can_create_exams permission.
    from apps.subjects.models import SubjectMembership

    membership = SubjectMembership.objects.filter(
        user=user, subject=subject, is_active=True
    ).first()
    if membership:
        perms = membership.get_effective_permissions()
        return perms.get("can_create_exams", False)
    return False


# ── Exam CRUD ────────────────────────────────────────────────


class ExamListCreateView(APIView):
    """Create and list exams within a subject."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Exams"],
        request=CreateExamSerializer,
        responses={201: ExamResponseSerializer},
        summary="Create exam (RF-6.1)",
    )
    def post(self, request: Request, subject_pk: str) -> Response:
        try:
            subject = Subject.objects.select_related("course").get(pk=subject_pk)
        except Subject.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not _can_manage_exam(request.user, subject):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        serializer = CreateExamSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            exam = create_exam(
                organization=request.user.organization,
                subject=subject,
                name=serializer.validated_data["name"],
            )
        except ExamServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        log_event(
            event_type=EXAM_CREATED,
            actor=request.user,
            organization=request.user.organization,
            entity=exam,
            ip_address=get_client_ip(request),
            payload={"name": exam.name, "subject_code": subject.code},
        )

        exam.model_count = exam.models.count()
        exam.convocation_count = 0
        return Response(ExamResponseSerializer(exam).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=["Exams"],
        responses={200: ExamResponseSerializer(many=True)},
        summary="List exams in a subject (RF-6.12)",
    )
    def get(self, request: Request, subject_pk: str) -> Response:
        try:
            Subject.objects.get(pk=subject_pk)
        except Subject.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        exams = Exam.objects.filter(subject_id=subject_pk)
        for e in exams:
            e.model_count = e.models.count()
            e.convocation_count = e.convocations.count()

        return Response(ExamResponseSerializer(exams, many=True).data)


class ExamDetailView(APIView):
    """Retrieve, update, and delete an exam."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Exams"], responses={200: ExamDetailSerializer}, summary="Exam detail (RF-6.13)"
    )
    def get(self, request: Request, exam_pk: str) -> Response:
        try:
            exam = Exam.objects.select_related("subject").get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        exam.convocation_count = exam.convocations.count()
        return Response(ExamDetailSerializer(exam).data)

    @extend_schema(
        tags=["Exams"],
        request=UpdateExamSerializer,
        responses={200: ExamResponseSerializer},
        summary="Update exam (RF-6.2)",
    )
    def patch(self, request: Request, exam_pk: str) -> Response:
        try:
            exam = Exam.objects.select_related("subject__course").get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not _can_manage_exam(request.user, exam.subject):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        serializer = UpdateExamSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            changes = update_exam(exam, data=serializer.validated_data)
        except ExamServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        if changes:
            log_event(
                event_type=EXAM_UPDATED,
                actor=request.user,
                organization=request.user.organization,
                entity=exam,
                ip_address=get_client_ip(request),
                payload={"changes": changes},
            )

        exam.model_count = exam.models.count()
        exam.convocation_count = exam.convocations.count()
        return Response(ExamResponseSerializer(exam).data)

    @extend_schema(
        tags=["Exams"],
        summary="Delete exam (RF-6.3)",
        responses={204: None},
    )
    def delete(self, request: Request, exam_pk: str) -> Response:
        try:
            exam = Exam.objects.select_related("subject__course").get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not _can_manage_exam(request.user, exam.subject):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        try:
            delete_exam(exam)
        except ExamServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        log_event(
            event_type=EXAM_DELETED,
            actor=request.user,
            organization=request.user.organization,
            ip_address=get_client_ip(request),
            payload={"exam_name": exam.name},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Model management ────────────────────────────────────────


class ModelCreateView(APIView):
    """Create a model within an exam."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Models"],
        request=CreateModelSerializer,
        responses={201: ModelResponseSerializer},
        summary="Create model (RF-6.5)",
    )
    def post(self, request: Request, exam_pk: str) -> Response:
        try:
            exam = Exam.objects.select_related("subject__course", "subject").get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not _can_manage_exam(request.user, exam.subject):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        serializer = CreateModelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            model = create_model(exam=exam, **serializer.validated_data)
        except ExamServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        model.problem_count = model.problems.count()
        model.page_profile_count = model.page_profiles.count()
        return Response(ModelResponseSerializer(model).data, status=status.HTTP_201_CREATED)


# ── Blank PDF upload ─────────────────────────────────────────


class BlankPDFUploadView(APIView):
    """Upload blank PDF for a model."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]
    serializer_class = BlankPDFUploadRequestSerializer

    @extend_schema(
        tags=["Models"],
        summary="Upload blank PDF (RF-6.7)",
        request={"multipart/form-data": BlankPDFUploadRequestSerializer},
        responses={200: BlankPDFUploadResponseSerializer},
    )
    def put(self, request: Request, model_pk: str) -> Response:
        try:
            model = ExamModel.objects.select_related(
                "exam__subject__course", "exam__subject", "exam"
            ).get(pk=model_pk)
        except ExamModel.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not _can_manage_exam(request.user, model.exam.subject):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        if "file" not in request.FILES:
            return Response({"error_code": "NO_FILE_PROVIDED"}, status=status.HTTP_400_BAD_REQUEST)

        file_data = request.FILES["file"].read()

        try:
            result = upload_blank_pdf(model, file_data, request.FILES["file"].name)
        except ExamServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_400_BAD_REQUEST)

        return Response(result, status=status.HTTP_200_OK)


# ── PageProfile & Zone management ────────────────────────────


class PageProfileCreateView(APIView):
    """Create a page profile for a model."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["PageProfiles"],
        request=CreatePageProfileSerializer,
        responses={201: PageProfileResponseSerializer},
        summary="Create page profile (RF-6.8)",
    )
    def post(self, request: Request, model_pk: str) -> Response:
        try:
            model = ExamModel.objects.select_related("exam__subject").get(pk=model_pk)
        except ExamModel.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        serializer = CreatePageProfileSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            profile = create_page_profile(
                exam_model=model,
                page_number=serializer.validated_data["page_number"],
            )
        except ExamServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        return Response(
            PageProfileResponseSerializer(profile).data, status=status.HTTP_201_CREATED
        )

    @extend_schema(
        tags=["PageProfiles"],
        responses={200: PageProfileResponseSerializer(many=True)},
        summary="List page profiles",
    )
    def get(self, request: Request, model_pk: str) -> Response:
        try:
            ExamModel.objects.get(pk=model_pk)
        except ExamModel.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        profiles = PageProfile.objects.filter(exam_model_id=model_pk).prefetch_related("zones")
        return Response(PageProfileResponseSerializer(profiles, many=True).data)


class ZoneCreateView(APIView):
    """Create a zone within a page profile."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Zones"],
        request=CreateZoneSerializer,
        responses={201: ZoneResponseSerializer},
        summary="Create zone (RF-6.8)",
    )
    def post(self, request: Request, profile_pk: str) -> Response:
        try:
            profile = PageProfile.objects.select_related("exam_model").get(pk=profile_pk)
        except PageProfile.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        serializer = CreateZoneSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            zone = create_zone(page_profile=profile, **serializer.validated_data)
        except ExamServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_400_BAD_REQUEST)

        return Response(ZoneResponseSerializer(zone).data, status=status.HTTP_201_CREATED)


class ZoneDeleteView(APIView):
    """Delete a recognition zone."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Zones"],
        summary="Delete zone (RF-6.8)",
        responses={204: None},
    )
    def delete(self, request: Request, zone_pk: str) -> Response:
        try:
            zone = RecognitionZone.objects.select_related("page_profile__exam_model").get(
                pk=zone_pk
            )
        except RecognitionZone.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        delete_zone(zone)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Problem & Rubric ─────────────────────────────────────────


class ProblemCreateView(APIView):
    """Create problems within a model."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Problems"],
        request=CreateProblemSerializer,
        responses={201: ProblemResponseSerializer},
        summary="Create problem (RF-6.9)",
    )
    def post(self, request: Request, model_pk: str) -> Response:
        try:
            model = ExamModel.objects.get(pk=model_pk)
        except ExamModel.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        serializer = CreateProblemSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        data = serializer.validated_data
        problem = create_problem(
            exam_model=model,
            name=data["name"],
            max_score=data["max_score"],
            order=data.get("order", 0),
            zone_ids=[str(z) for z in data.get("zone_ids", [])],
        )
        return Response(ProblemResponseSerializer(problem).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=["Problems"],
        responses={200: ProblemResponseSerializer(many=True)},
        summary="List problems",
    )
    def get(self, request: Request, model_pk: str) -> Response:
        try:
            ExamModel.objects.get(pk=model_pk)
        except ExamModel.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        problems = Problem.objects.filter(exam_model_id=model_pk).prefetch_related(
            "zones", "rubric_criteria"
        )
        return Response(ProblemResponseSerializer(problems, many=True).data)


class RubricSetView(APIView):
    """Set (replace) the rubric for a problem."""

    permission_classes = [IsAuthenticated]
    serializer_class = SetRubricSerializer

    @extend_schema(
        tags=["Rubrics"],
        request=SetRubricSerializer,
        responses={201: RubricCriterionResponseSerializer(many=True)},
        summary="Set rubric (RF-6.11)",
    )
    def post(self, request: Request, problem_pk: str) -> Response:
        try:
            problem = Problem.objects.get(pk=problem_pk)
        except Problem.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        serializer = SetRubricSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        criteria = set_rubric(
            problem=problem,
            criteria=serializer.validated_data["criteria"],
        )

        result = [
            {
                "id": str(c.pk),
                "description": c.description,
                "score": str(c.score),
                "order": c.order,
            }
            for c in criteria
        ]
        return Response(result, status=status.HTTP_201_CREATED)


# ── Convocation ──────────────────────────────────────────────


class ConvocationView(APIView):
    """Set the exam's convocation list."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Convocations"],
        request=SetConvocationSerializer,
        responses={200: ConvocationResponseSerializer},
        summary="Set convocation (RF-6.4)",
    )
    def put(self, request: Request, exam_pk: str) -> Response:
        try:
            exam = Exam.objects.select_related("subject").get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not _can_manage_exam(request.user, exam.subject):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        serializer = SetConvocationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        result = set_convocation(
            exam=exam,
            student_ids=[str(s) for s in serializer.validated_data["student_ids"]],
        )
        return Response(ConvocationResponseSerializer(result).data)


# ── Instrumented PDF ─────────────────────────────────────────


class GenerateInstrumentedPDFView(APIView):
    """Generate the instrumented PDF for a model."""

    permission_classes = [IsAuthenticated]
    serializer_class = InstrumentedPDFGenerationResponseSerializer

    @extend_schema(
        tags=["Instrumented PDF"],
        summary="Generate instrumented PDF (RF-6.15)",
        request=None,
        responses={201: InstrumentedPDFGenerationResponseSerializer},
    )
    def post(self, request: Request, model_pk: str) -> Response:
        try:
            model = ExamModel.objects.select_related("exam__subject", "exam").get(pk=model_pk)
        except ExamModel.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not _can_manage_exam(request.user, model.exam.subject):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        from apps.exams.services.instrumented_pdf import (
            InstrumentedPDFError,
            generate_instrumented_pdf,
            store_instrumented_pdf,
        )

        try:
            pdf_bytes = generate_instrumented_pdf(model)
            key = store_instrumented_pdf(model, pdf_bytes)
        except InstrumentedPDFError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        return Response(
            {
                "instrumented_pdf_ref": key,
                "instrumented_pdf_valid": True,
                "size_bytes": len(pdf_bytes),
            },
            status=status.HTTP_201_CREATED,
        )


class DownloadInstrumentedPDFView(APIView):
    """Download the instrumented PDF for a model."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Instrumented PDF"],
        summary="Download instrumented PDF (RF-6.16)",
        responses={200: None},
    )
    def get(self, request: Request, model_pk: str) -> Response:
        try:
            model = ExamModel.objects.select_related("exam__subject").get(pk=model_pk)
        except ExamModel.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not model.instrumented_pdf_valid:
            return Response(
                {
                    "error_code": "INSTRUMENTED_PDF_INVALID",
                    "detail": "The instrumented PDF is outdated. Regenerate it first.",
                },
                status=status.HTTP_409_CONFLICT,
            )

        if not model.instrumented_pdf_ref:
            return Response(
                {"error_code": "NO_INSTRUMENTED_PDF"},
                status=status.HTTP_404_NOT_FOUND,
            )

        from apps.exams.services.storage import generate_presigned_url

        url = generate_presigned_url(model.instrumented_pdf_ref)
        return Response({"download_url": url, "label": model.label})


# ── Grade export (RF-14.1, RF-14.2) ─────────────────────────
"""
Phase 10 views: grade export, org config, hierarchical search.

Endpoints:
    GET   /exams/{id}/export-grades/     — CSV grade export (RF-14.1)
    GET   /exams/{id}/grades/            — JSON grade data (RF-14.2)
    GET   /config/                       — Get org config (RF-15.1)
    PATCH /config/                       — Update org config (RF-15.2)
    GET   /search/                       — Hierarchical search (RF-14.3)

References: RF-14.1 through RF-14.5, RF-15.1, RF-15.2, RF-15.5
"""


class GradeExportCSVView(APIView):
    """Export grades to CSV (RF-14.1)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Export"],
        summary="Export grades CSV (RF-14.1)",
        responses={200: None},
    )
    def get(self, request: Request, exam_pk: str) -> HttpResponse:
        from apps.exams.models import Exam
        from apps.exams.services.grade_export import export_grades_csv
        from apps.organizations.services import get_org_config

        try:
            exam = Exam.objects.get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        # Get configured columns.
        config = get_org_config(request.user.organization)
        columns = config.grade_export_columns or None

        content = export_grades_csv(exam, columns=columns)

        log_event(
            event_type="DATA_EXPORTED",
            actor=request.user,
            organization=request.user.organization,
            ip_address=get_client_ip(request),
            payload={"entity_type": "Grades", "format": "csv", "exam": exam.name},
        )

        response = HttpResponse(content, content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="grades_{exam.pk}.csv"'
        return response


class GradeExportJSONView(APIView):
    """Export full grade data as JSON (RF-14.2)."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Export"],
        summary="Export grades JSON (RF-14.2)",
        responses={200: None},
    )
    def get(self, request: Request, exam_pk: str) -> Response:
        from apps.exams.models import Exam
        from apps.exams.services.grade_export import export_grades_json

        try:
            exam = Exam.objects.get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        data = export_grades_json(exam)

        log_event(
            event_type="DATA_EXPORTED",
            actor=request.user,
            organization=request.user.organization,
            ip_address=get_client_ip(request),
            payload={"entity_type": "Grades", "format": "json", "exam": exam.name},
        )

        return Response(data)
