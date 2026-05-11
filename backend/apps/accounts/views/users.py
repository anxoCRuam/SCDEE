"""
User management API views.

ViewSet for user CRUD operations by organization managers (RF-2.2-2.8).

Why ViewSet instead of APIView?
    Users are a resource with standard CRUD operations (create, list,
    retrieve, update). ViewSet maps naturally to these operations and
    provides consistent URL routing via DRF's router conventions.
    Custom actions (reset-password) are added as @action decorators.

    Contrast with auth views (login, logout, refresh) which are actions,
    not CRUD — those use APIView.

Endpoints:
    POST   /api/v1/users/                    — Create user (RF-2.2)
    GET    /api/v1/users/                    — List users (RF-2.12) [Bloque D]
    GET    /api/v1/users/{id}/               — Retrieve user
    PATCH  /api/v1/users/{id}/               — Update user (RF-2.4, RF-2.5, RF-2.6)
    POST   /api/v1/users/{id}/reset-password/ — Reset password (RF-2.8)

Security:
    All endpoints require IsOrgManager permission. The TenantManager
    ensures managers only see users in their own organization.

References: RF-2.2, RF-2.4, RF-2.5, RF-2.6, RF-2.8, RF-16.1
"""

from __future__ import annotations

import logging

from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet

from apps.accounts.models.permissions import IsOrgManager
from apps.accounts.serializers.users import (
    CreateUserSerializer,
    UpdateUserSerializer,
    UserResponseSerializer,
)
from apps.accounts.services.user_service import (
    UserFilter,
    UserServiceError,
    create_user,
    reset_user_password,
    update_user,
)
from apps.accounts.tasks import send_password_reset_email, send_welcome_email
from apps.audit.services.auditlog import (
    PASSWORD_RESET,
    USER_CREATED,
    USER_DEACTIVATED,
    USER_UPDATED,
    get_client_ip,
    log_event,
)
from apps.core.utils.pagination import StandardPagination

logger = logging.getLogger(__name__)


def _get_user_model():
    from django.contrib.auth import get_user_model

    return get_user_model()


@extend_schema_view(
    create=extend_schema(
        tags=["Users"],
        summary="Create a new user",
        description="Create a user in the manager's organization (RF-2.2). Manager only.",
    ),
    list=extend_schema(
        tags=["Users"],
        summary="List users with filters",
        description="List users in the manager's organization with search and filters (RF-2.12)."
        " Manager only.",
    ),
    retrieve=extend_schema(
        tags=["Users"],
        summary="Retrieve a user",
    ),
    partial_update=extend_schema(
        tags=["Users"],
        summary="Update a user",
        description="Update user fields. PATCH semantics: only sent fields change (RF-2.4)."
        " Manager only.",
    ),
)
class UserViewSet(ViewSet):
    """User management by organization managers.

    All queries are automatically scoped to the manager's organization
    by the TenantManager on the User model.
    """

    permission_classes = [IsOrgManager]
    pagination_class = StandardPagination

    @extend_schema(responses={200: UserResponseSerializer(many=True)})
    def list(self, request: Request) -> Response:
        """List users in the manager's organization with filters (RF-2.12).

        Supports query parameters:
            - search: partial match on first_name, last_name, or email
            - is_active: boolean filter (true/false)
            - is_staff: boolean filter (true/false)
            - nia: exact match (case-insensitive)
            - email: partial match
            - page: page number (default 1)
            - page_size: items per page (default 25, max 100)

        The queryset is automatically scoped to the manager's organization
        by TenantManager, so cross-org data is never exposed.
        """
        user_model = _get_user_model()
        queryset = user_model.objects.all().order_by("last_name", "first_name")

        # Apply filters.
        filterset = UserFilter(request.query_params, queryset=queryset)
        if not filterset.is_valid():
            return Response(
                {"error_code": "INVALID_FILTER", "errors": filterset.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        queryset = filterset.qs

        # Paginate.
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)

        if page is not None:
            serializer = UserResponseSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = UserResponseSerializer(queryset, many=True)
        return Response(serializer.data)

    @extend_schema(request=CreateUserSerializer, responses={201: UserResponseSerializer})
    def create(self, request: Request) -> Response:
        """Create a new user in the manager's organization (RF-2.2)."""
        serializer = CreateUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            user, raw_password = create_user(
                organization=request.user.organization,
                **serializer.validated_data,
            )
        except UserServiceError as exc:
            error_response = {"error_code": exc.code}
            if exc.field:
                error_response["errors"] = {exc.field: [exc.code]}
            return Response(error_response, status=status.HTTP_409_CONFLICT)

        # Audit log.
        log_event(
            event_type=USER_CREATED,
            actor=request.user,
            organization=request.user.organization,
            entity=user,
            ip_address=get_client_ip(request),
            payload={"email": user.email, "is_staff": user.is_staff},
        )

        # Enqueue welcome email with generated password.
        send_welcome_email.delay(user.email, raw_password, user.first_name)

        return Response(
            UserResponseSerializer(user).data,
            status=status.HTTP_201_CREATED,
        )

    @extend_schema(
        tags=["Users"],
        summary="Retrieve user",
        responses={200: None},
    )
    def retrieve(self, request: Request, pk: str = None) -> Response:
        """Retrieve a single user's details."""
        user_model = _get_user_model()
        try:
            user = user_model.objects.get(pk=pk)
        except user_model.DoesNotExist:
            return Response(
                {"error_code": "NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(UserResponseSerializer(user).data)

    @extend_schema(request=UpdateUserSerializer, responses={200: UserResponseSerializer})
    def partial_update(self, request: Request, pk: str = None) -> Response:
        """Update a user's fields (PATCH semantics, RF-2.4/2.5/2.6)."""
        user_model = _get_user_model()
        try:
            user = user_model.objects.get(pk=pk)
        except user_model.DoesNotExist:
            return Response(
                {"error_code": "NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )

        serializer = UpdateUserSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        # Prevent manager from deactivating themselves.
        if serializer.validated_data.get("is_active") is False and user.pk == request.user.pk:
            return Response(
                {"error_code": "CANNOT_DEACTIVATE_SELF"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Prevent manager from removing their own is_staff.
        if serializer.validated_data.get("is_staff") is False and user.pk == request.user.pk:
            return Response(
                {"error_code": "CANNOT_DEMOTE_SELF"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            changes = update_user(user, data=serializer.validated_data)
        except UserServiceError as exc:
            error_response = {"error_code": exc.code}
            if exc.field:
                error_response["errors"] = {exc.field: [exc.code]}
            return Response(error_response, status=status.HTTP_409_CONFLICT)

        if changes:
            # Determine the right event type.
            event_type = USER_UPDATED
            if "is_active" in changes and changes["is_active"]["new"] is False:
                event_type = USER_DEACTIVATED

            log_event(
                event_type=event_type,
                actor=request.user,
                organization=request.user.organization,
                entity=user,
                ip_address=get_client_ip(request),
                payload={"changes": changes},
            )

        return Response(UserResponseSerializer(user).data)

    @extend_schema(
        request=None,
        responses={200: None},
        tags=["Users"],
        summary="Reset user password",
        description="Generate a new password and send it by email (RF-2.8).  Manager only.",
    )
    @action(detail=True, methods=["post"], url_path="reset-password")
    def reset_password(self, request: Request, pk: str = None) -> Response:
        """Force-reset a user's password (RF-2.8).

        Generates a new secure password, invalidates all existing
        tokens, and sends the new password by email.
        """
        user_model = _get_user_model()
        try:
            user = user_model.objects.get(pk=pk)
        except user_model.DoesNotExist:
            return Response(
                {"error_code": "NOT_FOUND"},
                status=status.HTTP_404_NOT_FOUND,
            )

        raw_password = reset_user_password(user)

        log_event(
            event_type=PASSWORD_RESET,
            actor=request.user,
            organization=request.user.organization,
            entity=user,
            ip_address=get_client_ip(request),
        )

        # Enqueue password reset email.
        send_password_reset_email.delay(user.email, raw_password, user.first_name)

        return Response(
            {"detail": "Password reset successfully. New password sent by email."},
            status=status.HTTP_200_OK,
        )
