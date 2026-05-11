"""
Org config

Endpoints:
    GET   /config/                       — Get org config (RF-15.1)
    PATCH /config/                       — Update org config (RF-15.2)

References: RF-15
"""

from __future__ import annotations

import logging

from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models.permissions import IsOrgManager
from apps.audit.services.auditlog import get_client_ip, log_event
from apps.organizations.serializers.organization_config import (
    OrgConfigSerializer,
)

logger = logging.getLogger(__name__)


class OrgConfigView(APIView):
    """Get and update organization configuration."""

    permission_classes = [IsOrgManager]
    serializer_class = OrgConfigSerializer

    @extend_schema(
        tags=["Organizations"],
        summary="Get org config (RF-15.1)",
        responses={200: OrgConfigSerializer},
    )
    def get(self, request: Request) -> Response:
        from apps.organizations.services.organization_config import get_org_config

        config = get_org_config(request.user.organization)
        serializer = OrgConfigSerializer(config)
        return Response(serializer.data)

    @extend_schema(
        tags=["Organizations"],
        summary="Update org config (RF-15.2)",
        request=OrgConfigSerializer,
        responses={200: OrgConfigSerializer},
    )
    def patch(self, request: Request) -> Response:
        from apps.organizations.services.organization_config import (
            get_org_config,
            update_org_config,
        )

        serializer = OrgConfigSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)

        changes = update_org_config(request.user.organization, serializer.validated_data)

        if changes:
            log_event(
                event_type="CONFIG_UPDATED",
                actor=request.user,
                organization=request.user.organization,
                ip_address=get_client_ip(request),
                payload={"changes": changes},
            )

        config = get_org_config(request.user.organization)
        output_serializer = OrgConfigSerializer(config)
        return Response(output_serializer.data)
