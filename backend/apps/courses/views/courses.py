"""
Academic course API views.

ViewSet for course management by organization managers.

Endpoints:
    POST   /api/v1/courses/         — Create course + auto-transition (RF-3.1, RF-3.3)
    GET    /api/v1/courses/{id}/    — Retrieve course details
    GET    /api/v1/courses/current/    — Retrieve current course details
    PATCH  /api/v1/courses/{id}/    — Update course (RF-3.2, only active)

Why ViewSet?
    Courses are a resource with standard CRUD operations. The transition
    logic is invoked automatically during creation, not as a separate
    endpoint — it's an internal side effect per RF-3.1.

Security:
    All endpoints require IsOrgManager. TenantManager ensures managers
    only see courses in their own organization. Writes on archived
    courses are rejected with 403 (RF-3.5).

References: RF-3.1, RF-3.2, RF-3.3, RF-3.4, RF-3.5, RF-16.1
"""

from __future__ import annotations

import logging

from django.db.models import Count
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from apps.accounts.models.permissions import IsOrgManager
from apps.audit.services.auditlog import (
    COURSE_CREATED,
    COURSE_TRANSITION,
    COURSE_UPDATED,
    get_client_ip,
    log_event,
)
from apps.courses.models.courses import AcademicCourse
from apps.courses.serializers.courses import (
    COURSE_DETAILS_NON_MANAGER_EXAMPLE,
    COURSE_DETAILS_ORG_MANAGER_EXAMPLE,
    CourseActiveSerializer,
    CourseSerializer,
    CreateCourseSerializer,
    UpdateCourseSerializer,
)
from apps.courses.services.courses import CourseServiceError, create_course, update_course

logger = logging.getLogger(__name__)


@extend_schema_view(
    create=extend_schema(
        tags=["Courses"],
        summary="Create academic course",
        description=(
            "Creates a new active course. If a previous active course exists, "
            "it is automatically archived along with its data (RF-3.3)."
        ),
    ),
    retrieve=extend_schema(
        tags=["Courses"],
        summary="Retrieve course details",
        description=(
            "Returns course details. If the course is the currently active course, "
            "any authenticated user can access (returns a reduced field set). "
            "If the course is archived, only organisation managers can access "
            "(returns full details)."
        ),
    ),
    partial_update=extend_schema(
        tags=["Courses"],
        summary="Update course",
        description="Update an active courses fields. Archived courses cant be modified (RF-3.5).",
    ),
)
class CourseViewSet(ViewSet):
    """Academic course management for organization managers."""

    @extend_schema(request=CreateCourseSerializer, responses={201: CourseSerializer})
    def create(self, request: Request) -> Response:
        """Create a new academic course (RF-3.1).

        If a previous active course exists, it is automatically
        archived and the transition process (RF-3.3) is executed:
        - Previous course → ARCHIVED
        - Non-manager users → deactivated
        - SubjectMemberships → deactivated
        - Notifications scoped to the course → archived
        - Non-finalized exam instances → ARCHIVED
        """
        self.permission_classes = [IsOrgManager]
        self.check_permissions(request)
        serializer = CreateCourseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ip_address = get_client_ip(request)

        try:
            new_course, transition_report = create_course(
                organization=request.user.organization,
                label=serializer.validated_data["label"],
                start_date=serializer.validated_data.get("start_date"),
                end_date=serializer.validated_data.get("end_date"),
            )
        except CourseServiceError as exc:
            return Response(
                {"error_code": exc.code},
                status=status.HTTP_409_CONFLICT,
            )

        # Audit: course creation.
        log_event(
            event_type=COURSE_CREATED,
            actor=request.user,
            organization=request.user.organization,
            entity=new_course,
            ip_address=ip_address,
            payload={"label": new_course.label},
        )

        # Audit: transition (if it happened).
        if transition_report:
            log_event(
                event_type=COURSE_TRANSITION,
                actor=request.user,
                organization=request.user.organization,
                entity=new_course,
                ip_address=ip_address,
                payload=transition_report,
            )

        new_course = AcademicCourse.objects.annotate(subject_count=Count("subject_set")).get(
            pk=new_course.pk
        )

        return Response(
            CourseSerializer(new_course).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Courses"],
        summary="Retrieve current course details",
        description=(
            "Returns course details. If the course is the currently active course, "
            "any authenticated user can access (returns a reduced field set). "
            "If the course is archived, only organisation managers can access "
            "(returns full details)."
        ),
        responses={
            200: OpenApiResponse(
                response=CourseSerializer,
                examples=[
                    COURSE_DETAILS_ORG_MANAGER_EXAMPLE,
                    COURSE_DETAILS_NON_MANAGER_EXAMPLE,
                ],
            ),
        },
    )
    def current(self, request: Request) -> Response:
        """Get the currently active course for the user's organization (RF-3.4)."""
        user = request.user
        if not user.is_authenticated:
            return Response({"error_code": "AUTHENTICATION_REQUIRED"}, status=401)
        org = user.organization
        if not org:
            return Response({"error_code": "NOT_FOUND"}, status=404)

        try:
            active = AcademicCourse.objects.get(organization=org, is_active=True)
        except AcademicCourse.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=404)

        permission = IsOrgManager()
        if not permission.has_permission(request, self):
            serializer = CourseActiveSerializer(active)
        else:
            course = active.annotate(subject_count=Count("subject_set"))
            serializer = CourseSerializer(course)
        return Response(serializer.data)

    @extend_schema(
        responses={
            200: OpenApiResponse(
                response=CourseSerializer,
                examples=[
                    COURSE_DETAILS_ORG_MANAGER_EXAMPLE,
                    COURSE_DETAILS_NON_MANAGER_EXAMPLE,
                ],
            ),
        },
    )
    def retrieve(self, request: Request, pk: str = None) -> Response:
        """Retrieve a course by its UUID."""
        # Validar autenticación
        if not request.user.is_authenticated:
            return Response({"error_code": "AUTHENTICATION_REQUIRED"}, status=401)

        # Obtener el curso (con control de organización vía TenantManager)
        course = get_object_or_404(AcademicCourse, pk=pk)

        # Determinar si es el curso activo de la organización del usuario
        is_active_course = (
            request.user.organization is not None
            and course.organization == request.user.organization
            and course.is_active
        )

        permission = IsOrgManager()
        if not permission.has_permission(request, self) and is_active_course:
            # Cualquier usuario autenticado de la misma org puede verlo (campos reducidos)
            serializer = CourseActiveSerializer(course)
            return Response(serializer.data)

        # Anotar subject_count para respuesta completa
        course = AcademicCourse.objects.annotate(subject_count=Count("subject_set")).get(pk=pk)
        serializer = CourseSerializer(course)
        return Response(serializer.data)

    @extend_schema(request=UpdateCourseSerializer, responses={200: CourseSerializer})
    def partial_update(self, request: Request, pk: str = None) -> Response:
        """Update a course's fields (RF-3.2).

        Only active courses can be modified. Attempting to modify
        an archived course returns 403 (RF-3.5).
        """
        self.permission_classes = [IsOrgManager]
        self.check_permissions(request)

        try:
            course = AcademicCourse.objects.get(pk=pk)
        except AcademicCourse.DoesNotExist:
            return Response(
                {"error_code": "NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Block writes on archived courses (RF-3.5).
        if not course.is_active:
            return Response(
                {"error_code": "COURSE_ARCHIVED"},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = UpdateCourseSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            changes = update_course(course, data=serializer.validated_data)
        except CourseServiceError as exc:
            return Response(
                {"error_code": exc.code},
                status=status.HTTP_409_CONFLICT,
            )

        if changes:
            log_event(
                event_type=COURSE_UPDATED,
                actor=request.user,
                organization=request.user.organization,
                entity=course,
                ip_address=get_client_ip(request),
                payload={"changes": changes},
            )

        course = AcademicCourse.objects.annotate(subject_count=Count("subject_set")).get(pk=pk)

        return Response(CourseSerializer(course).data)
