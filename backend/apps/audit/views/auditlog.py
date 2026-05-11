from django_filters.rest_framework import (
    CharFilter,
    DateTimeFilter,
    DjangoFilterBackend,
    FilterSet,
    UUIDFilter,
)
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import generics

from apps.accounts.models.permissions import IsOrgManager
from apps.audit.models.auditlog import AuditLog
from apps.audit.serializers.auditlog import AuditLogSerializer
from apps.core.openapi.errors import ErrorCode, error_response, forbidden
from apps.core.utils.pagination import StandardPagination


class AuditLogFilter(FilterSet):
    event_type = CharFilter(field_name="event_type", lookup_expr="exact")
    actor_id = UUIDFilter(field_name="actor_id")
    entity_type = CharFilter(field_name="entity_type", lookup_expr="exact")
    entity_id = CharFilter(field_name="entity_id", lookup_expr="exact")
    ip_address = CharFilter(field_name="ip_address", lookup_expr="exact")
    timestamp_from = DateTimeFilter(field_name="timestamp", lookup_expr="gte")
    timestamp_to = DateTimeFilter(field_name="timestamp", lookup_expr="lte")

    class Meta:
        model = AuditLog
        fields = []


class AuditLogListView(generics.ListAPIView):
    serializer_class = AuditLogSerializer
    pagination_class = StandardPagination
    permission_classes = [IsOrgManager]
    filter_backends = [DjangoFilterBackend]
    filterset_class = AuditLogFilter
    queryset = AuditLog.objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return AuditLog.objects.none()
        org = self.request.user.organization
        if not org:
            return AuditLog.objects.none()
        return AuditLog.objects.filter(organization=org).order_by("-timestamp")

    @extend_schema(
        operation_id="list_audit_logs",
        tags=["Audit"],
        summary="Retrieve audit logs (RF-16.2)",
        description="Paginated list of audit logs for the authenticated manager's organization. "
        "Allows filtering by event type, actor, affected entity, date range, and IP address.",
        parameters=[
            OpenApiParameter(
                "event_type", OpenApiTypes.STR, description="Event type (exact match)"
            ),
            OpenApiParameter("actor_id", OpenApiTypes.UUID, description="Actor ID"),
            OpenApiParameter("entity_type", OpenApiTypes.STR, description="Entity type"),
            OpenApiParameter("entity_id", OpenApiTypes.STR, description="Entity ID"),
            OpenApiParameter("ip_address", OpenApiTypes.STR, description="Exact IP address"),
            OpenApiParameter(
                "timestamp_from", OpenApiTypes.DATETIME, description="From (ISO 8601)"
            ),
            OpenApiParameter("timestamp_to", OpenApiTypes.DATETIME, description="To (ISO 8601)"),
            OpenApiParameter("page", OpenApiTypes.INT, description="Page number"),
            OpenApiParameter("page_size", OpenApiTypes.INT, description="Page size"),
        ],
        responses={
            200: AuditLogSerializer(many=True),
            401: error_response([ErrorCode.AUTHENTICATION_REQUIRED], status_code=401),
            403: forbidden("You must be an organization manager to access audit logs."),
            429: error_response([ErrorCode.RATE_LIMIT_EXCEEDED], status_code=429),
        },
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)
