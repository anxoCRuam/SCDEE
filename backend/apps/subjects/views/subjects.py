"""
Subject management views.

CRUD for subjects by managers and coordinators. List includes
an optional course_id filter for archived course navigation (RF-3.5).

Permissions:
    - Create/Delete: IsOrgManager only.
    - Update: IsOrgManager OR subject coordinator.
    - List/Retrieve: IsOrgManager (all subjects) or authenticated user
      (sees subjects where they have an active membership).

References: RF-4.1, RF-4.3, RF-4.4, RF-4.8, RF-4.9
"""

from __future__ import annotations

import logging

from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from apps.accounts.permissions import IsOrgManager
from apps.audit.services import (
    SUBJECT_CREATED,
    get_client_ip,
    log_event,
)
from apps.core.pagination import StandardPagination
from apps.subjects.models import Subject, SubjectMembership
from apps.subjects.serializers.serializers import (
    CreateSubjectSerializer,
    SubjectDetailSerializer,
    SubjectResponseSerializer,
    UpdateSubjectSerializer,
)
from apps.subjects.services.subject_service import (
    SubjectServiceError,
    create_subject,
    delete_subject,
    update_subject,
)

logger = logging.getLogger(__name__)


@extend_schema_view(
    create=extend_schema(
        tags=["Subjects"],
        summary="Create subject",
        description="Create a subject in the active course (RF-4.1). Manager only.",
    ),
    list=extend_schema(
        tags=["Subjects"],
        summary="List subjects",
        description="List subjects. Managers see all; others see only their memberships.",
        parameters=[
            OpenApiParameter("course_id", str, description="Filter by course ID (RF-3.5)."),
        ],
    ),
    retrieve=extend_schema(tags=["Subjects"], summary="Subject details (RF-4.9)"),
    partial_update=extend_schema(tags=["Subjects"], summary="Update subject (RF-4.3)"),
    destroy=extend_schema(tags=["Subjects"], summary="Delete subject (RF-4.4)"),
)
class SubjectViewSet(ViewSet):
    """Subject CRUD by managers and coordinators."""

    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def get_permissions(self):
        """Managers for create/delete, authenticated for list/retrieve/update."""
        if self.action in ("create", "destroy"):
            return [IsOrgManager()]
        return super().get_permissions()

    @extend_schema(request=CreateSubjectSerializer, responses={201: SubjectResponseSerializer})
    def create(self, request: Request) -> Response:
        """Create a subject in the active course (RF-4.1)."""
        serializer = CreateSubjectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            subject = create_subject(
                organization=request.user.organization,
                **serializer.validated_data,
            )
        except SubjectServiceError as exc:
            error_resp = {"error_code": exc.code}
            if exc.field:
                error_resp["errors"] = {exc.field: [exc.code]}
            return Response(error_resp, status=status.HTTP_409_CONFLICT)

        log_event(
            event_type=SUBJECT_CREATED,
            actor=request.user,
            organization=request.user.organization,
            entity=subject,
            ip_address=get_client_ip(request),
            payload={"name": subject.name, "code": subject.code},
        )

        return Response(
            SubjectResponseSerializer(subject).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Subjects"],
        summary="List subjects",
        operation_id="subjects_list",
        responses={200: None},
    )
    def list(self, request: Request) -> Response:
        """List subjects. Managers see all; others see their memberships.

        Supports ?course_id= for archived course navigation (RF-3.5).
        """
        user = request.user

        if user.is_staff or user.is_superadmin:
            queryset = Subject.objects.select_related("coordinator", "course").all()
        else:
            # Non-managers: only subjects where they have active membership.
            member_subject_ids = SubjectMembership.objects.filter(
                user=user, is_active=True
            ).values_list("subject_id", flat=True)
            queryset = Subject.objects.filter(pk__in=member_subject_ids).select_related(
                "coordinator", "course"
            )

        # Optional course filter.
        course_id = request.query_params.get("course_id")
        if course_id:
            queryset = queryset.filter(course_id=course_id)

        queryset = queryset.order_by("name")

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        if page is not None:
            return paginator.get_paginated_response(
                SubjectResponseSerializer(page, many=True).data
            )

        return Response(SubjectResponseSerializer(queryset, many=True).data)

    @extend_schema(
        tags=["Subjects"],
        summary="Subject detail (RF-4.9)",
        operation_id="subjects_detail",
        responses={200: None},
    )
    def retrieve(self, request: Request, pk: str = None) -> Response:
        """Retrieve subject with role-based detail level (RF-4.9).

        Managers/coordinators/teachers: full detail (members, groups).
        Students: basic info + their own group.
        """
        try:
            subject = (
                Subject.objects.select_related("coordinator", "course")
                .prefetch_related("groups", "memberships__user", "memberships__group")
                .get(pk=pk)
            )
        except Subject.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        return Response(SubjectDetailSerializer(subject).data)

    @extend_schema(request=UpdateSubjectSerializer, responses={200: SubjectResponseSerializer})
    def partial_update(self, request: Request, pk: str = None) -> Response:
        """Update subject fields (RF-4.3). Managers or coordinator only."""
        try:
            subject = Subject.objects.select_related("coordinator", "course").get(pk=pk)
        except Subject.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        # Permission: manager or coordinator of this subject.
        if not request.user.is_staff and request.user != subject.coordinator:
            return Response(
                {"error_code": "PERMISSION_DENIED"},
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = UpdateSubjectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            changes = update_subject(
                subject,
                data=serializer.validated_data,
                organization=request.user.organization,
            )
        except SubjectServiceError as exc:
            st = (
                status.HTTP_403_FORBIDDEN
                if exc.code == "COURSE_ARCHIVED"
                else status.HTTP_409_CONFLICT
            )
            return Response({"error_code": exc.code}, status=st)

        if changes:
            log_event(
                event_type=SUBJECT_CREATED,
                actor=request.user,
                organization=request.user.organization,
                entity=subject,
                ip_address=get_client_ip(request),
                payload={"changes": changes},
            )

        return Response(SubjectResponseSerializer(subject).data)

    @extend_schema(
        tags=["Subjects"],
        summary="Delete subject (RF-4.4)",
        responses={204: None},
    )
    def destroy(self, request: Request, pk: str = None) -> Response:
        """Delete subject if no exams (RF-4.4). Manager only."""
        try:
            subject = Subject.objects.select_related("course").get(pk=pk)
        except Subject.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        try:
            delete_subject(subject)
        except SubjectServiceError as exc:
            st = (
                status.HTTP_403_FORBIDDEN
                if exc.code == "COURSE_ARCHIVED"
                else status.HTTP_409_CONFLICT
            )
            return Response({"error_code": exc.code}, status=st)

        return Response(status=status.HTTP_204_NO_CONTENT)
