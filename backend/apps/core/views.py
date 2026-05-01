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

import boto3
from botocore.exceptions import ClientError
from celery import current_app as celery_app
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    OpenApiTypes,
    extend_schema,
    inline_serializer,
)
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════════
# Response schema for /health/
# ════════════════════════════════════════════════════════════════════
#
# Defined here (rather than in apps.core.openapi) because this is the
# only endpoint that uses it. Splitting into per-component fields and
# an aggregated wrapper keeps the OpenAPI document explicit about
# which keys appear in the response and which can carry an ``error``
# string.


class _ComponentStatusSerializer(serializers.Serializer):
    """One row of the ``components`` map in the health response.

    Inline-only — used for the response schema and the example.
    """

    status = serializers.ChoiceField(
        choices=[("healthy", "healthy"), ("unhealthy", "unhealthy")],
        help_text="Component-level status. ``healthy`` if reachable, ``unhealthy`` otherwise.",
    )
    error = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Error message when ``status`` is ``unhealthy``. Absent on healthy responses.",
    )
    workers = serializers.IntegerField(
        required=False,
        help_text="Number of Celery workers that responded. Only present for ``celery_workers``.",
    )
    note = serializers.CharField(
        required=False,
        help_text="Optional non-fatal note (e.g. ``bucket_not_found`` when MinIO "
        "is reachable but no bucket exists yet).",
    )


_HEALTH_OK_EXAMPLE = OpenApiExample(
    name="HealthyFastExample",
    summary="Default fast probe",
    description=(
        "PostgreSQL + Redis only. This is what orchestrators should "
        "call. Typical latency: 5–20 ms when the service is alive."
    ),
    value={
        "status": "healthy",
        "components": {
            "postgresql": {"status": "healthy"},
            "redis": {"status": "healthy"},
        },
    },
    response_only=True,
    status_codes=["200"],
)

_HEALTH_DEEP_EXAMPLE = OpenApiExample(
    name="HealthyExample",
    summary="All components healthy",
    description="Every infrastructure dependency replied successfully.",
    value={
        "status": "healthy",
        "components": {
            "postgresql": {"status": "healthy"},
            "redis": {"status": "healthy"},
            "minio": {"status": "healthy"},
            "celery_workers": {"status": "healthy", "workers": 2},
        },
    },
    response_only=True,
    status_codes=["200"],
)

_HEALTH_DEGRADED_EXAMPLE = OpenApiExample(
    name="DegradedExample",
    summary="At least one component is down",
    description=(
        "Returned with HTTP 503. The container orchestrator should "
        "consider the API unhealthy and route traffic elsewhere."
    ),
    value={
        "status": "unhealthy",
        "components": {
            "postgresql": {"status": "healthy"},
            "redis": {"status": "healthy"},
            "minio": {"status": "healthy"},
            "celery_workers": {
                "status": "unhealthy",
                "error": "No workers responded",
            },
        },
    },
    response_only=True,
    status_codes=["503"],
)


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
        # Public endpoint: drf-spectacular emits ``security: [{}]``,
        # which the postprocessing hook now correctly recognises as
        # "no authentication required" and skips injecting 401/403.
        # See apps.accounts.spectacular._endpoint_requires_auth.
        responses={
            200: OpenApiResponse(
                response=inline_serializer(
                    name="HealthCheckOk",
                    fields={
                        "status": serializers.ChoiceField(
                            choices=[("healthy", "healthy")],
                            help_text="Always ``healthy`` on a 200 response.",
                        ),
                        "components": serializers.DictField(
                            child=_ComponentStatusSerializer(),
                            help_text=(
                                "Map of component name → status. Keys are "
                                "``postgresql``, ``redis``, ``minio``, ``celery_workers``."
                            ),
                        ),
                    },
                ),
                description="All infrastructure components are reachable.",
                examples=[_HEALTH_OK_EXAMPLE],
            ),
            503: OpenApiResponse(
                response=inline_serializer(
                    name="HealthCheckDegraded",
                    fields={
                        "status": serializers.ChoiceField(
                            choices=[("unhealthy", "unhealthy")],
                            help_text="Always ``unhealthy`` on a 503 response.",
                        ),
                        "components": serializers.DictField(
                            child=_ComponentStatusSerializer(),
                            help_text=(
                                "Map of component name → status. The failing "
                                "component(s) carry an ``error`` string with "
                                "the underlying exception message."
                            ),
                        ),
                    },
                ),
                description=(
                    "At least one infrastructure dependency is down. "
                    "The orchestrator should treat the API as unhealthy."
                ),
                examples=[_HEALTH_DEGRADED_EXAMPLE],
            ),
        },
    )
    def get(self, request: Request) -> Response:
        """Probe components according to the requested mode and aggregate."""
        deep = self._parse_deep_flag(request)

        components: dict[str, dict] = {
            "postgresql": self._check_postgresql(),
            "redis": self._check_redis(),
        }
        if deep:
            components["minio"] = self._check_minio()
            components["celery_workers"] = self._check_celery()

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

    def _check_postgresql(self) -> dict:
        """Verify database connectivity with a trivial query."""
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            return {"status": "healthy"}
        except Exception as exc:
            logger.warning("PostgreSQL health check failed: %s", exc)
            return {"status": "unhealthy", "error": str(exc)}

    def _check_redis(self) -> dict:
        """Verify Redis connectivity via the Django cache backend."""
        try:
            cache.set("_health_check", "ok", timeout=5)
            value = cache.get("_health_check")
            if value == "ok":
                return {"status": "healthy"}
            return {"status": "unhealthy", "error": "Unexpected cache response"}
        except Exception as exc:
            logger.warning("Redis health check failed: %s", exc)
            return {"status": "unhealthy", "error": str(exc)}

    def _check_minio(self) -> dict:
        """Verify MinIO connectivity by checking if the bucket exists."""
        try:
            s3_client = boto3.client(
                "s3",
                endpoint_url=settings.AWS_S3_ENDPOINT_URL,
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                region_name=settings.AWS_S3_REGION_NAME,
            )
            s3_client.head_bucket(Bucket=settings.AWS_STORAGE_BUCKET_NAME)
            return {"status": "healthy"}
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code", "Unknown")
            if error_code == "404":
                # Bucket doesn't exist yet — report but not critical.
                return {"status": "healthy", "note": "bucket_not_found"}
            logger.warning("MinIO health check failed: %s", exc)
            return {"status": "unhealthy", "error": str(exc)}
        except Exception as exc:
            logger.warning("MinIO health check failed: %s", exc)
            return {"status": "unhealthy", "error": str(exc)}

    def _check_celery(self) -> dict:
        """Verify that at least one Celery worker is responding."""
        try:
            # inspect().ping() sends a broadcast and waits for replies.
            # 3 s timeout is generous for a local network.
            inspector = celery_app.control.inspect(timeout=3)
            ping_response = inspector.ping()

            if ping_response:
                worker_count = len(ping_response)
                return {
                    "status": "healthy",
                    "workers": worker_count,
                }
            return {"status": "unhealthy", "error": "No workers responded"}
        except Exception as exc:
            logger.warning("Celery health check failed: %s", exc)
            return {"status": "unhealthy", "error": str(exc)}
