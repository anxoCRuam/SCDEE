"""
Health check endpoint for infrastructure monitoring.

Verifies connectivity to all backend dependencies:
    - PostgreSQL: executes a trivial query.
    - Redis: PING command.
    - MinIO: head_bucket call on the default bucket.
    - Celery: ping active workers.

Returns HTTP 200 if ALL components are healthy, HTTP 503 otherwise.
No authentication required — this is an infrastructure endpoint
used by load balancers and monitoring tools.

References: RNF-5
"""

import logging

import boto3
from botocore.exceptions import ClientError
from celery import current_app as celery_app
from django.conf import settings
from django.core.cache import cache
from django.db import connection
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


class HealthCheckView(APIView):
    """System health check endpoint.

    Returns the status of each infrastructure component and an
    overall system status. Used by container orchestrators and
    monitoring dashboards.

    Endpoint: GET /health/
    Auth: None required
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    def get(self, request: Request) -> Response:
        """Check all components and return aggregated health status."""
        components = {
            "postgresql": self._check_postgresql(),
            "redis": self._check_redis(),
            "minio": self._check_minio(),
            "celery_workers": self._check_celery(),
        }

        all_healthy = all(c["status"] == "healthy" for c in components.values())

        return Response(
            {
                "status": "healthy" if all_healthy else "unhealthy",
                "components": components,
            },
            status=status.HTTP_200_OK if all_healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        )

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
                # Bucket doesn't exist yet — report but not critical
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
            # timeout=3s is generous for a local network.
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
