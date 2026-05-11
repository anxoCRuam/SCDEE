"""
Exam-related API views.

Endpoints:
    POST   /subjects/{id}/exams/                    — Create exam (RF-6.1)
    GET    /subjects/{id}/exams/                    — List exams (RF-6.12)

    GET    /exams/{id}/                             — Exam detail (RF-6.13)
    PATCH  /exams/{id}/                             — Update exam (RF-6.2)
    DELETE /exams/{id}/                             — Delete exam (RF-6.3)

    POST   /exams/{id}/models/                      — Create model (RF-6.5)
    GET    /exams/{id}/models/{id}                  — Model detail
    PUT    /models/{id}/blank-pdf/                  — Upload blank PDF (RF-6.7)
    POST   /models/{id}/qr-pdf/                     — Generate PDF (RF-6.15)

    POST   /models/{id}/page-profiles/              — Create page profile (RF-6.8)
    POST   /page-profiles/{id}/zones/               — Create zone (RF-6.8)
    DELETE /zones/{id}/                             — Delete zone (RF-6.8)

    POST   /models/{id}/problems/                   — Create problem (RF-6.9)
    POST   /problems/{id}/rubric/                   — Set rubric (RF-6.11)

    PUT    /exams/{id}/convocation/                 — Set convocation (RF-6.4)


References: RF-6.1 through RF-6.16, RF-16.1
"""

from __future__ import annotations

import logging
from datetime import datetime

from django.http import HttpResponse
from drf_spectacular.utils import OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services.auditlog import (
    EXAM_CREATED,
    EXAM_DELETED,
    EXAM_UPDATED,
    get_client_ip,
    log_event,
)
from apps.exams.models.exams import (
    Exam,
    ExamConvocation,
    ExamModel,
    PageProfile,
    Problem,
    RecognitionZone,
)
from apps.exams.serializers.exams import (
    EXAM_BASIC_DETAILS_EXAMPLE,
    EXAM_FULL_DETAILS_EXAMPLE,
    EXAM_TEACHER_DETAILS_EXAMPLE,
    BlankPDFUploadRequestSerializer,
    BlankPDFUploadResponseSerializer,
    ConvocationResponseSerializer,
    CreateExamSerializer,
    CreateModelSerializer,
    CreatePageProfileSerializer,
    CreateProblemSerializer,
    CreateZoneSerializer,
    ExamBasicSerializer,
    ExamFullSerializer,
    ExamTeacherSerializer,
    ModelDetailSerializer,
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
from apps.subjects.models.permissions import MembershipRole
from apps.subjects.models.subjects import Subject, SubjectMembership
from apps.subjects.services.permissions import has_subject_permission

logger = logging.getLogger(__name__)


# ── Exam CRUD ────────────────────────────────────────────────


class ExamListCreateView(APIView):
    """Create and list exams within a subject."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Exams"],
        request=CreateExamSerializer,
        responses={201: ExamFullSerializer},
        summary="Create exam (RF-6.1) 'can_create_exams' required",
    )
    def post(self, request: Request, subject_pk: str) -> Response:
        try:
            subject = Subject.objects.select_related("course").get(pk=subject_pk)
        except Subject.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(request.user, subject, "can_create_exams"):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        serializer = CreateExamSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            exam = create_exam(
                organization=request.user.organization,
                subject=subject,
                name=serializer.validated_data["name"],
                date=serializer.validated_data.get("date"),
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

        return Response(ExamFullSerializer(exam).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        tags=["Exams"],
        summary="List exams in a subject (RF-6.12)",
        responses={
            200: OpenApiResponse(
                response=ExamFullSerializer(many=True),
                examples=[
                    EXAM_FULL_DETAILS_EXAMPLE,
                    EXAM_TEACHER_DETAILS_EXAMPLE,
                    EXAM_BASIC_DETAILS_EXAMPLE,
                ],
            ),
        },
    )
    def get(self, request: Request, subject_pk: str) -> Response:
        try:
            subject = Subject.objects.get(pk=subject_pk)
        except Subject.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        exams = (
            Exam.objects.filter(subject_id=subject_pk)
            .select_related("subject")
            .prefetch_related(
                "models__problems__rubric_criteria",
                "convocations__student",
            )
            .order_by("-date", "name")
        )
        membership = SubjectMembership.objects.filter(
            user=request.user, subject=subject, is_active=True
        ).first()

        serialized = []
        for exam in exams:
            if has_subject_permission(request.user, exam.subject, "can_create_exams"):
                serializer = ExamFullSerializer(exam, context={"request": request})
            elif membership and membership.role in (
                MembershipRole.TEACHER,
                MembershipRole.COORDINATOR,
            ):
                serializer = ExamTeacherSerializer(exam, context={"request": request})
            else:
                serializer = ExamBasicSerializer(exam, context={"request": request})
            serialized.append(serializer.data)

        return Response(serialized)


class ExamDetailView(APIView):
    """Retrieve, update, and delete an exam."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Exams"],
        summary="Exam detail (RF-6.13)",
        responses={
            200: OpenApiResponse(
                response=ExamFullSerializer,
                examples=[
                    EXAM_FULL_DETAILS_EXAMPLE,
                    EXAM_TEACHER_DETAILS_EXAMPLE,
                    EXAM_BASIC_DETAILS_EXAMPLE,
                ],
            ),
        },
    )
    def get(self, request: Request, exam_pk: str) -> Response:
        try:
            exam = (
                Exam.objects.select_related("subject")
                .prefetch_related(
                    "models__problems__rubric_criteria",
                    "convocations__student",
                )
                .get(pk=exam_pk)
            )
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        user = request.user
        membership = SubjectMembership.objects.filter(
            user=user, subject=exam.subject, is_active=True
        ).first()

        if has_subject_permission(user, exam.subject, "can_create_exams"):
            serializer = ExamFullSerializer(exam, context={"request": request})
        elif membership and membership.role in [
            MembershipRole.COORDINATOR,
            MembershipRole.TEACHER,
        ]:
            serializer = ExamTeacherSerializer(exam, context={"request": request})
        else:
            if not ExamConvocation.objects.filter(exam=exam, student=user).exists():
                return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)
            serializer = ExamBasicSerializer(exam, context={"request": request})

        return Response(serializer.data)

    @extend_schema(
        tags=["Exams"],
        request=UpdateExamSerializer,
        responses={200: ExamFullSerializer},
        summary="Update exam (RF-6.2) 'can_create_exams' required",
    )
    def patch(self, request: Request, exam_pk: str) -> Response:
        try:
            exam = Exam.objects.select_related("subject__course").get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(request.user, exam.subject, "can_create_exams"):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        serializer = UpdateExamSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            changes = update_exam(exam, data=serializer.validated_data)
        except ExamServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        if changes:
            for _, change in changes.items():
                if isinstance(change.get("old"), datetime):
                    change["old"] = change["old"].isoformat()
                if isinstance(change.get("new"), datetime):
                    change["new"] = change["new"].isoformat()
            log_event(
                event_type=EXAM_UPDATED,
                actor=request.user,
                organization=request.user.organization,
                entity=exam,
                ip_address=get_client_ip(request),
                payload={"changes": changes},
            )

        return Response(ExamFullSerializer(exam).data)

    @extend_schema(
        tags=["Exams"],
        summary="Delete exam (RF-6.3) 'can_create_exams' required",
        responses={204: None},
    )
    def delete(self, request: Request, exam_pk: str) -> Response:
        try:
            exam = Exam.objects.select_related("subject__course").get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(request.user, exam.subject, "can_create_exams"):
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
        tags=["Exams"],
        request=CreateModelSerializer,
        responses={201: ModelDetailSerializer},
        summary="Create model (RF-6.5) 'can_create_exams' required",
    )
    def post(self, request: Request, exam_pk: str) -> Response:
        try:
            exam = Exam.objects.select_related("subject__course", "subject").get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(request.user, exam.subject, "can_create_exams"):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        serializer = CreateModelSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            model = create_model(exam=exam, **serializer.validated_data)
        except ExamServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        return Response(ModelDetailSerializer(model).data, status=status.HTTP_201_CREATED)


class ModelDetailView(APIView):
    """Retrieve a model's page profiles with their zones."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Exams"],
        responses={200: ModelDetailSerializer},
        summary="Get model detail – list page profiles with zones 'can_create_exams' required",
    )
    def get(self, request: Request, model_pk: str) -> Response:
        try:
            model = (
                ExamModel.objects.select_related("exam__subject")
                .prefetch_related("page_profiles__zones")
                .get(pk=model_pk)
            )
        except ExamModel.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(request.user, model.exam.subject, "can_create_exams"):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        return Response(ModelDetailSerializer(model).data)


# ── Blank PDF upload ─────────────────────────────────────────


class BlankPDFUploadView(APIView):
    """Upload blank PDF for a model."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser]
    serializer_class = BlankPDFUploadRequestSerializer

    @extend_schema(
        tags=["Exams"],
        summary="Upload blank PDF (RF-6.7) 'can_create_exams' required",
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

        if not has_subject_permission(request.user, model.exam.subject, "can_create_exams"):
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
        tags=["Exams"],
        request=CreatePageProfileSerializer,
        responses={201: PageProfileResponseSerializer},
        summary="Create page profile (RF-6.8) 'can_create_exams' required",
    )
    def post(self, request: Request, model_pk: str) -> Response:
        try:
            model = ExamModel.objects.select_related("exam__subject").get(pk=model_pk)
        except ExamModel.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(request.user, model.exam.subject, "can_create_exams"):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

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
        tags=["Exams"],
        responses={200: PageProfileResponseSerializer(many=True)},
        summary="List page profiles 'can_create_exams' required",
    )
    def get(self, request: Request, model_pk: str) -> Response:
        try:
            model = ExamModel.objects.get(pk=model_pk)
        except ExamModel.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(request.user, model.exam.subject, "can_create_exams"):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        profiles = PageProfile.objects.filter(exam_model_id=model_pk).prefetch_related("zones")
        return Response(PageProfileResponseSerializer(profiles, many=True).data)


class ZoneCreateView(APIView):
    """Create a zone within a page profile."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Exams"],
        request=CreateZoneSerializer,
        responses={201: ZoneResponseSerializer},
        summary="Create zone (RF-6.8) 'can_create_exams' required",
    )
    def post(self, request: Request, profile_pk: str) -> Response:
        try:
            profile = PageProfile.objects.select_related("exam_model").get(pk=profile_pk)
        except PageProfile.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(
            request.user, profile.exam_model.exam.subject, "can_create_exams"
        ):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        serializer = CreateZoneSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            zones = create_zone(page_profile=profile, **serializer.validated_data)
        except ExamServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            ZoneResponseSerializer(zones, many=True).data, status=status.HTTP_201_CREATED
        )


class ZoneDeleteView(APIView):
    """Delete a recognition zone."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Exams"],
        summary="Delete zone (RF-6.8) 'can_create_exams' required",
        responses={204: None},
    )
    def delete(self, request: Request, zone_pk: str) -> Response:
        try:
            zone = RecognitionZone.objects.select_related("page_profile__exam_model").get(
                pk=zone_pk
            )
        except RecognitionZone.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(
            request.user, zone.page_profile.exam_model.exam.subject, "can_create_exams"
        ):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        delete_zone(zone)
        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Problem & Rubric ─────────────────────────────────────────


class ProblemCreateView(APIView):
    """Create problems within a model."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Exams"],
        request=CreateProblemSerializer,
        responses={201: ProblemResponseSerializer},
        summary="Create problem (RF-6.9) 'can_create_rubric' required",
    )
    def post(self, request: Request, model_pk: str) -> Response:
        try:
            model = ExamModel.objects.get(pk=model_pk)
        except ExamModel.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(request.user, model.exam.subject, "can_create_rubric"):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

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


class RubricSetView(APIView):
    """Set (replace) the rubric for a problem."""

    permission_classes = [IsAuthenticated]
    serializer_class = SetRubricSerializer

    @extend_schema(
        tags=["Exams"],
        request=SetRubricSerializer,
        responses={201: RubricCriterionResponseSerializer(many=True)},
        summary="Set rubric (RF-6.11) 'can_create_rubric' required",
    )
    def post(self, request: Request, problem_pk: str) -> Response:
        try:
            problem = Problem.objects.get(pk=problem_pk)
        except Problem.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(
            request.user, problem.exam_model.exam.subject, "can_create_rubric"
        ):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        serializer = SetRubricSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        criteria = set_rubric(
            problem=problem,
            criteria=serializer.validated_data["criteria"],
        )

        # Usar el serializador de respuesta en lugar del dict manual
        response_serializer = RubricCriterionResponseSerializer(criteria, many=True)
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)


# ── Convocation ──────────────────────────────────────────────


class ConvocationView(APIView):
    """Set the exam's convocation list."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Exams"],
        request=SetConvocationSerializer,
        responses={200: ConvocationResponseSerializer},
        summary="Set convocation (RF-6.4) 'can_manage_members' required",
    )
    def put(self, request: Request, exam_pk: str) -> Response:
        try:
            exam = Exam.objects.select_related("subject").get(pk=exam_pk)
        except Exam.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(request.user, exam.subject, "can_manage_members"):
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

    @extend_schema(
        tags=["Exams"],
        summary="Generate instrumented PDF (RF-6.15) 'can_create_exams' required",
        request=None,
        responses={(200, "application/pdf"): OpenApiTypes.BINARY},
    )
    def post(self, request: Request, model_pk: str) -> Response:
        try:
            model = ExamModel.objects.select_related("exam__subject", "exam").get(pk=model_pk)
        except ExamModel.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(request.user, model.exam.subject, "can_create_exams"):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        from apps.exams.services.instrumented_pdf import (
            InstrumentedPDFError,
            generate_instrumented_pdf,
        )

        try:
            pdf_bytes = generate_instrumented_pdf(model)
        except InstrumentedPDFError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="instrumented_{model.label}.pdf"'
        return response


class MyExamsView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Exams"],
        summary="List my exams in the active course",
        responses={
            200: OpenApiResponse(
                response=ExamFullSerializer(many=True),
                examples=[
                    EXAM_FULL_DETAILS_EXAMPLE,
                    EXAM_TEACHER_DETAILS_EXAMPLE,
                    EXAM_BASIC_DETAILS_EXAMPLE,
                ],
            ),
        },
    )
    def get(self, request):
        from apps.courses.models.courses import AcademicCourse

        active_course = AcademicCourse.objects.filter(
            organization=request.user.organization, is_active=True
        ).first()
        if not active_course:
            return Response([])

        memberships = SubjectMembership.objects.filter(
            user=request.user, is_active=True, subject__course=active_course
        ).select_related("subject")

        exam_ids = set()
        for m in memberships:
            exam_ids.update(Exam.objects.filter(subject=m.subject).values_list("pk", flat=True))

        exams = (
            Exam.objects.filter(pk__in=exam_ids)
            .select_related("subject")
            .prefetch_related(
                "models__problems__rubric_criteria",
                "convocations__student",
            )
            .order_by("-date", "name")
        )

        # Serializar según el rol
        serialized = []
        for exam in exams:
            # Determinar el rol más alto del usuario en ese subject
            membership = memberships.filter(subject=exam.subject).first()
            if not membership:
                continue
            role = membership.role
            if has_subject_permission(request.user, exam.subject, "can_create_exams"):
                serializer = ExamFullSerializer(exam, context={"request": request})
            elif role == MembershipRole.TEACHER or role == MembershipRole.COORDINATOR:
                serializer = ExamTeacherSerializer(exam, context={"request": request})
            else:
                serializer = ExamBasicSerializer(exam, context={"request": request})
            serialized.append(serializer.data)

        return Response(serialized)
