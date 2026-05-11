"""
Subject management views.

CRUD for subjects by managers and coordinators. List includes
an optional course_id filter for archived course navigation (RF-3.5).

Permissions:
    - Create/Delete: IsOrgManager only.
    - Update: IsOrgManager OR subject coordinator.
    - Retrieve: IsOrgManager (all subjects) or authenticated user
      (sees subjects where they have an active membership).

References: RF-4.1, RF-4.3, RF-4.4, RF-4.8, RF-4.9
"""

from __future__ import annotations

import logging

from drf_spectacular.utils import OpenApiResponse, extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from apps.accounts.models.permissions import IsOrgManager
from apps.audit.services.auditlog import (
    SUBJECT_CREATED,
    SUBJECT_UPDATED,
    get_client_ip,
    log_event,
)
from apps.core.utils.pagination import StandardPagination
from apps.subjects.models.permissions import MembershipRole
from apps.subjects.models.subjects import Subject, SubjectMembership
from apps.subjects.serializers.subjects import (
    SUBJECT_DETAILS_NON_MANAGER_EXAMPLE,
    SUBJECT_DETAILS_ORG_MANAGER_EXAMPLE,
    CreateSubjectSerializer,
    MySubjectSerializer,
    SubjectDetailSerializer,
    SubjectResponseSerializer,
    UpdateSubjectSerializer,
)
from apps.subjects.services.subjects import (
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
    retrieve=extend_schema(
        tags=["Subjects"],
        summary="Subject details (RF-4.9), extended details "
        "if teacher of above, reduced details for students of the subject",
    ),
    partial_update=extend_schema(
        tags=["Subjects"], summary="Update subject (RF-4.3). Manager or coordinator."
    ),
    destroy=extend_schema(tags=["Subjects"], summary="Delete subject (RF-4.4). Manager only."),
)
class SubjectViewSet(ViewSet):
    """Subject CRUD by managers and coordinators."""

    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    def get_permissions(self):
        """Managers for create/delete, authenticated for retrieve/update."""
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
        summary="Subject detail (RF-4.9)",
        operation_id="subjects_detail",
        responses={
            200: OpenApiResponse(
                response=SubjectDetailSerializer,
                examples=[
                    SUBJECT_DETAILS_ORG_MANAGER_EXAMPLE,
                    SUBJECT_DETAILS_NON_MANAGER_EXAMPLE,
                ],
            ),
        },
    )
    def retrieve(self, request: Request, pk: str = None) -> Response:
        try:
            subject = (
                Subject.objects.select_related("coordinator", "course")
                .prefetch_related("groups", "memberships__user", "memberships__group")
                .get(pk=pk)
            )
        except Subject.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        user = request.user

        # Si es staff/superadmin → acceso completo
        if user.is_staff or user.is_superadmin:
            return Response(SubjectDetailSerializer(subject).data)

        # Buscar membresía activa del usuario en esta asignatura
        membership = (
            SubjectMembership.objects.filter(user=user, subject=subject, is_active=True)
            .select_related("group")
            .first()
        )

        if membership is None:
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        # Coordinador o profesor → detalle completo
        if membership.role in (MembershipRole.COORDINATOR, MembershipRole.TEACHER):
            return Response(SubjectDetailSerializer(subject).data)

        # Estudiante → información reducida (como en my-subjects)
        return Response(MySubjectSerializer(membership).data)

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
                event_type=SUBJECT_UPDATED,
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
