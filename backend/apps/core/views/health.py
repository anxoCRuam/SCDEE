"""
Health check endpoint for infrastructure monitoring.

Verifies connectivity to all backend dependencies:
    - PostgreSQL: executes a trivial query.
    - Redis: PING command via the Django cache.
    - MinIO: ``head_bucket`` on the default bucket.
    - Celery: pings active workers (3 s timeout).

Returns HTTP 200 if ALL components are healthy, HTTP 503 otherwise.
No authentication required — this is an infrastructure endpoint
used by load balancers and monitoring tools.

References: RNF-5.
"""

from __future__ import annotations

import logging

from drf_spectacular.utils import (
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
)
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.serializers.health import (
    HEALTH_DEEP_EXAMPLE,
    HEALTH_DEGRADED_EXAMPLE,
    HEALTH_OK_EXAMPLE,
    HealthCheckResponseSerializer,
)
from apps.core.services.health import check_celery, check_minio, check_postgresql, check_redis

logger = logging.getLogger(__name__)


class HealthCheckView(APIView):
    """System health check endpoint (RNF-5).

    Returns the status of each infrastructure component and an
    overall system status. Used by container orchestrators and
    monitoring dashboards.

    Design choice — APIView vs ViewSet:
        APIView is correct here. The endpoint is action-based
        (a single GET that aggregates probe results), not a CRUD
        resource, so ViewSet would be over-fitting.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []
    serializer_class = HealthCheckResponseSerializer

    @extend_schema(
        operation_id="health_check",
        tags=["System"],
        summary="Health check (RNF-5)",
        description=(
            "**Probe every infrastructure dependency and return an aggregated status.**\n\n"
            "Used by container orchestrators (Docker Compose health checks, "
            "Kubernetes readiness probes) and monitoring dashboards to decide "
            "whether the API should receive traffic.\n\n"
            "**Process**\n\n"
            "By default the endpoint only probes the components needed "
            "to serve a request: PostgreSQL and Redis. Both are "
            "inexpensive and respond in single-digit milliseconds when "
            "alive. This is what orchestrators should hit.\n\n"
            "Pass ``?deep=true`` to additionally probe MinIO "
            "(``head_bucket``) and Celery workers (broadcast ``ping``).\n\n"
            "**Output**\n\n"
            "* HTTP **200** with ``status: healthy`` if every component replied.\n"
            "* HTTP **503** with ``status: unhealthy`` if any component failed; "
            "the failing component's entry carries an ``error`` field.\n\n"
            "**Authentication**\n\n"
            "None required — this endpoint is intentionally public (RNF-5)."
        ),
        parameters=[
            OpenApiParameter(
                name="deep",
                location=OpenApiParameter.QUERY,
                required=False,
                type=OpenApiTypes.BOOL,
                description=(
                    "When ``true`` (or ``1``, case-insensitive), also "
                    "probes MinIO and Celery. Default ``false``."
                ),
            ),
        ],
        responses={
            200: OpenApiResponse(
                response=HealthCheckResponseSerializer,
                description="All components healthy.",
                examples=[HEALTH_OK_EXAMPLE, HEALTH_DEEP_EXAMPLE],
            ),
            503: OpenApiResponse(
                response=HealthCheckResponseSerializer,
                description="One or more components unhealthy.",
                examples=[HEALTH_DEGRADED_EXAMPLE],
            ),
        },
    )
    def get(self, request: Request) -> Response:
        """Probe components according to the requested mode and aggregate."""
        deep = self._parse_deep_flag(request)

        components: dict[str, dict] = {
            "postgresql": check_postgresql(),
            "redis": check_redis(),
        }
        if deep:
            components["minio"] = check_minio()
            components["celery_workers"] = check_celery()

        all_healthy = all(c["status"] == "healthy" for c in components.values())

        return Response(
            {
                "status": "healthy" if all_healthy else "unhealthy",
                "components": components,
            },
            status=status.HTTP_200_OK if all_healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    # ── Mode parsing ────────────────────────────────────────────

    @staticmethod
    def _parse_deep_flag(request: Request) -> bool:
        """Return ``True`` if the caller asked for the deep probe.

        Accepts ``?deep=true``, ``?deep=1``, ``?deep=yes``
        (case-insensitive). Anything else — including ``?deep=false``,
        ``?deep=0``, missing — selects the fast probe.

        Permissive parsing because the endpoint is also called from
        shell scripts and ``curl`` where boolean conventions vary.
        """
        raw = request.query_params.get("deep", "").strip().lower()
        return raw in {"true", "1", "yes", "on"}
