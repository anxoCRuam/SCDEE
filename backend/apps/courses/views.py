"""
Academic course API views.

ViewSet for course management by organization managers.

Endpoints:
    POST   /api/v1/courses/         — Create course + auto-transition (RF-3.1, RF-3.3)
    GET    /api/v1/courses/         — List courses (RF-3.4)
    GET    /api/v1/courses/{id}/    — Retrieve course details
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
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from apps.accounts.permissions import IsOrgManager
from apps.audit.services import (
    COURSE_CREATED,
    COURSE_TRANSITION,
    COURSE_UPDATED,
    get_client_ip,
    log_event,
)
from apps.courses.models import AcademicCourse
from apps.courses.serializers import (
    CourseResponseSerializer,
    CreateCourseSerializer,
    UpdateCourseSerializer,
)
from apps.courses.services import CourseServiceError, create_course, update_course

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
    list=extend_schema(
        tags=["Courses"],
        summary="List courses",
        description="List all courses (active + archived) for the organization (RF-3.4).",
    ),
    retrieve=extend_schema(
        tags=["Courses"],
        summary="Retrieve course details",
    ),
    partial_update=extend_schema(
        tags=["Courses"],
        summary="Update course",
        description="Update an active courses fields. Archived courses cant be modified (RF-3.5).",
    ),
)
class CourseViewSet(ViewSet):
    """Academic course management for organization managers."""

    permission_classes = [IsOrgManager]

    @extend_schema(request=CreateCourseSerializer, responses={201: CourseResponseSerializer})
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

        # Annotate subject_count for response (0 for a brand new course).
        new_course.subject_count = 0

        return Response(
            CourseResponseSerializer(new_course).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Courses"],
        summary="List courses (RF-3.4)",
        operation_id="courses_list",
        responses={200: CourseResponseSerializer(many=True)},
    )
    def list(self, request: Request) -> Response:
        """List all courses for the organization (RF-3.4).

        Returns active course first, then archived in reverse
        chronological order. Includes subject count annotation.

        Supports optional filter: ?is_active=true/false
        """
        queryset = AcademicCourse.objects.all()

        # Optional filter by active status.
        is_active_param = request.query_params.get("is_active")
        if is_active_param is not None:
            if is_active_param.lower() in ("true", "1"):
                queryset = queryset.filter(is_active=True)
            elif is_active_param.lower() in ("false", "0"):
                queryset = queryset.filter(is_active=False)

        # Annotate subject count (will work once Subject model exists).
        try:
            queryset = queryset.annotate(subject_count=Count("subject"))
        except Exception:
            # Subject model doesn't exist yet — add a default.
            for course in queryset:
                course.subject_count = 0

        queryset = queryset.order_by("-is_active", "-created_at")

        serializer = CourseResponseSerializer(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(
        tags=["Courses"],
        summary="Retrieve course",
        operation_id="courses_retrieve",
        responses={200: CourseResponseSerializer},
    )
    def retrieve(self, request: Request, pk: str = None) -> Response:
        """Retrieve a single course's details."""
        try:
            course = AcademicCourse.objects.get(pk=pk)
        except AcademicCourse.DoesNotExist:
            return Response(
                {"error_code": "NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Annotate subject count.
        try:
            course.subject_count = course.subject_set.count()
        except Exception:
            course.subject_count = 0

        return Response(CourseResponseSerializer(course).data)

    @extend_schema(request=UpdateCourseSerializer, responses={200: CourseResponseSerializer})
    def partial_update(self, request: Request, pk: str = None) -> Response:
        """Update a course's fields (RF-3.2).

        Only active courses can be modified. Attempting to modify
        an archived course returns 403 (RF-3.5).
        """
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

        # Re-annotate for response.
        try:
            course.subject_count = course.subject_set.count()
        except Exception:
            course.subject_count = 0

        return Response(CourseResponseSerializer(course).data)
