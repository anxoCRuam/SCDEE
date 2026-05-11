"""
Hierarchical search.

Endpoints:
    GET   /search/                       — Hierarchical search (RF-14.3)

References: RF-14
"""

from __future__ import annotations

import logging

from django_filters.rest_framework import DjangoFilterBackend
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import generics

from apps.accounts.models.permissions import IsOrgManager
from apps.core.openapi.errors import ErrorCode, error_response, forbidden
from apps.core.utils.pagination import StandardPagination
from apps.courses.models.courses import AcademicCourse
from apps.courses.serializers.courses import CourseSerializer
from apps.exams.models.exams import Exam
from apps.instances.models.instances import ExamInstance
from apps.organizations.serializers.search import (
    SEARCH_COURSE_LIST_EXAMPLE,
    SEARCH_EXAM_LIST_EXAMPLE,
    SEARCH_INSTANCE_LIST_EXAMPLE,
    SEARCH_SUBJECT_LIST_EXAMPLE,
    ExamListSerializer,
    InstanceListSerializer,
)
from apps.organizations.services.search import (
    CourseFilter,
    ExamFilter,
    InstanceFilter,
    SubjectFilter,
)
from apps.subjects.models.subjects import Subject
from apps.subjects.serializers.subjects import SubjectDetailSerializer

logger = logging.getLogger(__name__)


class HierarchicalSearchView(generics.ListAPIView):
    """Hierarchical navigation: courses → subjects → exams → instances (RF-14.3)."""

    permission_classes = [IsOrgManager]
    pagination_class = StandardPagination
    filter_backends = [DjangoFilterBackend]
    serializer_class = CourseSerializer  # Default, adjusts dynamically
    queryset = AcademicCourse.objects.none()  # For schema generation only

    def get_serializer_class(self):
        model = self._resolve_model()
        if model == "courses":
            return CourseSerializer
        if model == "subjects":
            return SubjectDetailSerializer
        if model == "exams":
            return ExamListSerializer
        return InstanceListSerializer

    def _resolve_model(self) -> str:
        """Determina qué nivel de la jerarquía se está solicitando."""
        if self.request.query_params.get("exam_id"):
            return "instances"
        if self.request.query_params.get("subject_id"):
            return "exams"
        if self.request.query_params.get("course_id"):
            return "subjects"
        return "courses"

    def get_queryset(self):
        user = self.request.user
        org = user.organization
        model_choice = self._resolve_model()

        if model_choice == "courses":
            qs = AcademicCourse.objects.filter(organization=org).order_by(
                "-is_active", "-created_at"
            )
            self.filterset_class = CourseFilter
            return qs

        if model_choice == "subjects":
            course_id = self.request.query_params.get("course_id")
            qs = Subject.objects.filter(organization=org, course_id=course_id).order_by("name")
            self.filterset_class = SubjectFilter
            # Aplicar filtro de búsqueda manualmente desde aquí (ya lo hará el backend)
            return qs

        if model_choice == "exams":
            subject_id = self.request.query_params.get("subject_id")
            qs = Exam.objects.filter(organization=org, subject_id=subject_id).order_by("name")
            self.filterset_class = ExamFilter
            return qs

        # instances
        exam_id = self.request.query_params.get("exam_id")
        qs = (
            ExamInstance.objects.filter(organization=org, exam_id=exam_id)
            .select_related("student", "model")
            .order_by("student__last_name")
        )
        self.filterset_class = InstanceFilter
        return qs

    # OpenAPI customization
    @extend_schema(
        tags=["Organizations"],
        summary="Hierarchical search (RF-14.3)",
        description=(
            "Search engine for Organization Managers to search across courses, subjects, exams "
            "and instances with a single endpoint. "
            "The level of the hierarchy is determined by the presence of query parameters: "
            "`course_id` → list subjects, `subject_id` → list exams, `exam_id` → list instances. "
            "Supports text search and filtering on each level."
        ),
        parameters=[
            OpenApiParameter("course_id", str, description="Course UUID to list subjects"),
            OpenApiParameter("subject_id", str, description="Subject UUID to list exams"),
            OpenApiParameter("exam_id", str, description="Exam UUID to list instances"),
            OpenApiParameter("search", str, description="Text search (label/name/email)"),
            OpenApiParameter("status", str, description="Instance status filter"),
            OpenApiParameter("has_issues", bool, description="Filter instances with issues"),
            OpenApiParameter("page", int, description="Page number"),
            OpenApiParameter("page_size", int, description="Page size"),
        ],
        responses={
            200: OpenApiResponse(
                response=CourseSerializer,
                description="Paginated list of results. The actual item schema depends on the "
                "chosen hierarchy level (courses, subjects, exams, or instances).",
                examples=[
                    SEARCH_COURSE_LIST_EXAMPLE,
                    SEARCH_SUBJECT_LIST_EXAMPLE,
                    SEARCH_EXAM_LIST_EXAMPLE,
                    SEARCH_INSTANCE_LIST_EXAMPLE,
                ],
            ),
            401: error_response([ErrorCode.AUTHENTICATION_REQUIRED], status_code=401),
            403: forbidden("You must be an organization manager to access search."),
            429: error_response([ErrorCode.RATE_LIMIT_EXCEEDED], status_code=429),
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)
