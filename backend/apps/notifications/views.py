"""
Notification API views.

Endpoints:
    GET   /api/v1/notifications/         — List own notifications (RF-13.4)
    PATCH /api/v1/notifications/read/    — Mark as read (RF-13.3)
    GET   /api/v1/notifications/count/   — Unread count (for badges)

References: RF-13.3, RF-13.4
"""

from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardPagination
from apps.notifications.models import Notification, NotificationStatus
from apps.notifications.serializers import (
    MarkReadResponseSerializer,
    MarkReadSerializer,
    NotificationResponseSerializer,
    UnreadCountResponseSerializer,
)
from apps.notifications.services import mark_all_read, mark_read


class NotificationListView(APIView):
    """List own notifications with filters (RF-13.4)."""

    permission_classes = [IsAuthenticated]
    pagination_class = StandardPagination

    @extend_schema(
        tags=["Notifications"],
        responses={200: NotificationResponseSerializer(many=True)},
        summary="List notifications (RF-13.4)",
        parameters=[
            OpenApiParameter("status", str, enum=["UNREAD", "READ", "ARCHIVED"]),
            OpenApiParameter("notification_type", str),
        ],
    )
    def get(self, request: Request) -> Response:
        queryset = Notification.objects.filter(user=request.user)

        # By default, exclude archived.
        status_filter = request.query_params.get("status")
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        else:
            queryset = queryset.exclude(status=NotificationStatus.ARCHIVED)

        if nt := request.query_params.get("notification_type"):
            queryset = queryset.filter(notification_type=nt)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request)
        if page is not None:
            return paginator.get_paginated_response(
                NotificationResponseSerializer(page, many=True).data
            )

        return Response(NotificationResponseSerializer(queryset, many=True).data)


class MarkReadView(APIView):
    """Mark notifications as read (RF-13.3)."""

    permission_classes = [IsAuthenticated]
    serializer_class = MarkReadSerializer

    @extend_schema(
        tags=["Notifications"],
        request=MarkReadSerializer,
        responses={200: MarkReadResponseSerializer},
        summary="Mark notifications as read (RF-13.3)",
    )
    def patch(self, request: Request) -> Response:
        serializer = MarkReadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        ids = [str(i) for i in serializer.validated_data.get("notification_ids", [])]

        count = mark_read(ids, request.user) if ids else mark_all_read(request.user)

        return Response({"marked_read": count})


class UnreadCountView(APIView):
    """Get unread notification count (for UI badges)."""

    permission_classes = [IsAuthenticated]
    serializer_class = UnreadCountResponseSerializer

    @extend_schema(
        tags=["Notifications"],
        summary="Unread notification count",
        responses={200: UnreadCountResponseSerializer},
    )
    def get(self, request: Request) -> Response:
        count = Notification.objects.filter(
            user=request.user,
            status=NotificationStatus.UNREAD,
        ).count()
        return Response({"unread_count": count})
