"""
Group, member, and permission views (nested under subjects).

Groups:     /api/v1/subjects/{id}/groups/
Members:    /api/v1/subjects/{id}/members/
Permissions: /api/v1/memberships/{id}/permissions/
My subjects: /api/v1/my-subjects/

References: RF-4.5, RF-4.6, RF-4.7, RF-4.8, RF-5.3, RF-5.5, RF-5.6
"""

from __future__ import annotations

import logging

from django.db import IntegrityError
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.audit.services.auditlog import (
    MEMBERSHIP_CHANGED,
    PERMISSIONS_CHANGED,
    get_client_ip,
    log_event,
)
from apps.core.utils.pagination import StandardPagination
from apps.subjects.models.subjects import (
    MembershipRole,
    Subject,
    SubjectGroup,
    SubjectMembership,
    get_default_permissions,
)
from apps.subjects.serializers.permissions import (
    EffectivePermissionsSerializer,
    UpdatePermissionsSerializer,
)
from apps.subjects.serializers.subjects import (
    AssignMemberSerializer,
    CreateGroupSerializer,
    GroupResponseSerializer,
    MembershipResponseSerializer,
    MySubjectSerializer,
    UpdateGroupSerializer,
)
from apps.subjects.services.permissions import has_subject_permission, update_permissions
from apps.subjects.services.subjects import (
    SubjectServiceError,
    assign_member,
    create_group,
    delete_group,
    unassign_member,
)

logger = logging.getLogger(__name__)


def _get_subject_or_404(pk, user):
    """Load a subject, checking tenant access."""
    try:
        return Subject.objects.select_related("course", "coordinator").get(pk=pk)
    except Subject.DoesNotExist:
        return None


# ── Group views ──────────────────────────────────────────────


class GroupListCreateView(APIView):
    """List and create groups within a subject (RF-4.5).

    GET  /api/v1/subjects/{id}/groups/ — List groups
    POST /api/v1/subjects/{id}/groups/ — Create group
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    @extend_schema(
        tags=["Subjects"],
        responses={200: GroupResponseSerializer(many=True)},
        summary="List groups in a subject ('can_manage_groups' required)",
    )
    def get(self, request: Request, subject_pk: str) -> Response:
        subject = _get_subject_or_404(subject_pk, request.user)
        if not subject:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(request.user, subject, "can_manage_groups"):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        groups = SubjectGroup.objects.filter(subject=subject)
        # Annotate member count
        for g in groups:
            g.member_count = SubjectMembership.unfiltered.filter(group=g, is_active=True).count()

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(groups, request)
        if page is not None:
            return paginator.get_paginated_response(GroupResponseSerializer(page, many=True).data)
        return Response(GroupResponseSerializer(groups, many=True).data)  # Fallback

    @extend_schema(
        tags=["Subjects"],
        request=CreateGroupSerializer,
        responses={201: GroupResponseSerializer},
        summary="Create group in a subject ('can_manage_groups' required)",
    )
    def post(self, request: Request, subject_pk: str) -> Response:
        subject = _get_subject_or_404(subject_pk, request.user)
        if not subject:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(request.user, subject, "can_manage_groups"):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        serializer = CreateGroupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            group = create_group(subject=subject, label=serializer.validated_data["label"])
        except SubjectServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        group.member_count = 0
        return Response(GroupResponseSerializer(group).data, status=status.HTTP_201_CREATED)


class GroupDetailView(APIView):
    """Update and delete a group (RF-4.5).

    PATCH  /api/v1/subjects/{sid}/groups/{gid}/ — Rename group
    DELETE /api/v1/subjects/{sid}/groups/{gid}/ — Delete group
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Subjects"],
        request=UpdateGroupSerializer,
        responses={200: GroupResponseSerializer},
        summary="Rename a group ('can_manage_groups' required)",
    )
    def patch(self, request: Request, subject_pk: str, group_pk: str) -> Response:
        subject = _get_subject_or_404(subject_pk, request.user)
        if not subject:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(request.user, subject, "can_manage_groups"):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        try:
            group = SubjectGroup.objects.get(pk=group_pk, subject=subject)
        except SubjectGroup.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        serializer = UpdateGroupSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        group.label = serializer.validated_data["label"]
        try:
            group.save()
        except IntegrityError:
            return Response({"error_code": "GROUP_LABEL_EXISTS"}, status=status.HTTP_409_CONFLICT)

        group.member_count = SubjectMembership.unfiltered.filter(
            group=group, is_active=True
        ).count()
        return Response(GroupResponseSerializer(group).data)

    @extend_schema(
        tags=["Subjects"],
        summary="Delete a group ('can_manage_groups' required)",
        responses={204: None},
    )
    def delete(self, request: Request, subject_pk: str, group_pk: str) -> Response:
        subject = _get_subject_or_404(subject_pk, request.user)
        if not subject:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(request.user, subject, "can_manage_groups"):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        try:
            group = SubjectGroup.objects.get(pk=group_pk, subject=subject)
        except SubjectGroup.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        try:
            delete_group(group)
        except SubjectServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_403_FORBIDDEN)

        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Member views ─────────────────────────────────────────────


class MemberListCreateView(APIView):
    """List and assign members to a subject (RF-4.6).

    GET  /api/v1/subjects/{id}/members/ — List members
    POST /api/v1/subjects/{id}/members/ — Assign member
    """

    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    @extend_schema(
        tags=["Subjects"],
        responses={200: MembershipResponseSerializer(many=True)},
        summary="List subject members ('can_manage_members' required)",
    )
    def get(self, request: Request, subject_pk: str) -> Response:
        subject = _get_subject_or_404(subject_pk, request.user)
        if not subject:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(request.user, subject, "can_manage_members"):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        memberships = (
            SubjectMembership.objects.filter(subject=subject, is_active=True)
            .select_related("user", "group")
            .order_by("role", "user__last_name")
        )

        role = request.query_params.get("role")
        if role:
            memberships = memberships.filter(role=role.upper())

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(memberships, request)
        if page is not None:
            return paginator.get_paginated_response(
                MembershipResponseSerializer(page, many=True).data
            )
        return Response(MembershipResponseSerializer(memberships, many=True).data)

    @extend_schema(
        tags=["Subjects"],
        request=AssignMemberSerializer,
        responses={201: MembershipResponseSerializer},
        summary="Assign user to subject ('can_manage_members' required)",
    )
    def post(self, request: Request, subject_pk: str) -> Response:
        subject = _get_subject_or_404(subject_pk, request.user)
        if not subject:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(request.user, subject, "can_manage_members"):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        serializer = AssignMemberSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            membership = assign_member(
                organization=request.user.organization,
                subject=subject,
                user_id=str(serializer.validated_data["user_id"]),
                role=serializer.validated_data["role"],
                group_id=str(serializer.validated_data["group_id"])
                if serializer.validated_data.get("group_id")
                else None,
            )
        except SubjectServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        log_event(
            event_type=MEMBERSHIP_CHANGED,
            actor=request.user,
            organization=request.user.organization,
            entity=membership,
            ip_address=get_client_ip(request),
            payload={
                "action": "assigned",
                "user_email": membership.user.email,
                "role": membership.role,
            },
        )

        return Response(
            MembershipResponseSerializer(membership).data,
            status=status.HTTP_201_CREATED,
        )


class MemberDetailView(APIView):
    """Remove a member from a subject (RF-4.7).

    DELETE /api/v1/subjects/{sid}/members/{mid}/
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Subjects"],
        summary="Remove member from subject ('can_manage_members' required)",
        responses={204: None},
    )
    def delete(self, request: Request, subject_pk: str, membership_pk: str) -> Response:
        subject = _get_subject_or_404(subject_pk, request.user)
        if not subject:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        if not has_subject_permission(request.user, subject, "can_manage_members"):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        try:
            membership = SubjectMembership.objects.select_related("user").get(
                pk=membership_pk, subject=subject
            )
        except SubjectMembership.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        try:
            soft_deleted = unassign_member(membership)
        except SubjectServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_409_CONFLICT)

        log_event(
            event_type=MEMBERSHIP_CHANGED,
            actor=request.user,
            organization=request.user.organization,
            entity=membership,
            ip_address=get_client_ip(request),
            payload={
                "action": "soft_deleted" if soft_deleted else "deleted",
                "user_email": membership.user.email,
            },
        )

        return Response(status=status.HTTP_204_NO_CONTENT)


# ── Permission views ─────────────────────────────────────────


class MembershipPermissionsView(APIView):
    """View and modify membership permissions (RF-5.3, RF-5.5).

    GET   /api/v1/memberships/{id}/permissions/ — Effective permissions
    PATCH /api/v1/memberships/{id}/permissions/ — Update overrides
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Subjects"],
        responses={200: EffectivePermissionsSerializer},
        summary="Get effective permissions (RF-5.5) Absence means false",
    )
    def get(self, request: Request, membership_pk: str) -> Response:
        try:
            membership = SubjectMembership.objects.select_related("user", "subject").get(
                pk=membership_pk
            )
        except SubjectMembership.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        effective = membership.get_effective_permissions()
        is_staff = membership.user.is_staff

        # Manager bypass: all permissions are True.
        if is_staff:
            effective = dict.fromkeys(effective, True)

        # Filtrar solo los permisos verdaderos
        true_perms = {k: v for k, v in effective.items() if v}

        return Response(
            EffectivePermissionsSerializer(
                {
                    "permissions": true_perms,
                    "role": membership.role,
                    "has_overrides": bool(membership.permission_overrides),
                    "is_staff_bypass": is_staff,
                }
            ).data
        )

    @extend_schema(
        tags=["Subjects"],
        request=UpdatePermissionsSerializer,
        responses={200: EffectivePermissionsSerializer},
        summary="Update permission overrides (RF-5.3) ('can_delegate_perms' required)",
    )
    def patch(self, request: Request, membership_pk: str) -> Response:
        try:
            membership = SubjectMembership.objects.select_related(
                "user", "subject", "subject__coordinator"
            ).get(pk=membership_pk)
        except SubjectMembership.DoesNotExist:
            return Response({"error_code": "NOT_FOUND"}, status=status.HTTP_404_NOT_FOUND)

        # Only coordinator or manager can modify permissions.
        subject = membership.subject
        if not has_subject_permission(request.user, subject, "can_delegate_perms"):
            return Response({"error_code": "PERMISSION_DENIED"}, status=status.HTTP_403_FORBIDDEN)

        serializer = UpdatePermissionsSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Get grantor's effective permissions for delegation check (RF-5.6).
        if request.user.is_staff:
            grantor_perms = dict.fromkeys(
                get_default_permissions(MembershipRole.COORDINATOR), True
            )
        else:
            grantor_membership = SubjectMembership.objects.filter(
                user=request.user, subject=subject, is_active=True
            ).first()
            grantor_perms = (
                grantor_membership.get_effective_permissions() if grantor_membership else {}
            )

        try:
            new_effective = update_permissions(
                membership=membership,
                overrides=serializer.validated_data["permissions"],
                grantor_permissions=grantor_perms,
            )
        except SubjectServiceError as exc:
            return Response({"error_code": exc.code}, status=status.HTTP_403_FORBIDDEN)

        log_event(
            event_type=PERMISSIONS_CHANGED,
            actor=request.user,
            organization=request.user.organization,
            entity=membership,
            ip_address=get_client_ip(request),
            payload={
                "user_email": membership.user.email,
                "overrides": membership.permission_overrides,
            },
        )

        return Response(
            EffectivePermissionsSerializer(
                {
                    "permissions": new_effective,
                    "role": membership.role,
                    "has_overrides": bool(membership.permission_overrides),
                    "is_staff_bypass": False,
                }
            ).data
        )


# ── My subjects view ─────────────────────────────────────────


class MySubjectsView(APIView):
    """List the authenticated user's subjects in the active course (RF-4.8).

    GET /api/v1/my-subjects/
    """

    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Subjects"],
        responses={200: MySubjectSerializer(many=True)},
        summary="(MAIN ENDPOINT) List user subjects in the active course",
    )
    def get(self, request: Request) -> Response:
        from apps.courses.models.courses import AcademicCourse

        # Find the active course for the user's organization.
        active_course = AcademicCourse.objects.filter(is_active=True).first()
        if active_course is None:
            return Response([])

        memberships = (
            SubjectMembership.objects.filter(
                user=request.user,
                is_active=True,
                subject__course=active_course,
            )
            .select_related("subject", "group")
            .order_by("subject__name")
        )

        return Response(MySubjectSerializer(memberships, many=True).data)
