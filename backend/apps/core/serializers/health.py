"""
Response schema for /health/
"""

from __future__ import annotations

from drf_spectacular.utils import (
    OpenApiExample,
)
from rest_framework import serializers


class ComponentStatusSerializer(serializers.Serializer):
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


class HealthCheckResponseSerializer(serializers.Serializer):
    status = serializers.ChoiceField(
        choices=[("healthy", "healthy"), ("unhealthy", "unhealthy")],
        help_text="Overall system status.",
    )
    components = serializers.DictField(
        child=ComponentStatusSerializer(), help_text="Map of component name → status."
    )


HEALTH_OK_EXAMPLE = OpenApiExample(
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

HEALTH_DEEP_EXAMPLE = OpenApiExample(
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

HEALTH_DEGRADED_EXAMPLE = OpenApiExample(
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
