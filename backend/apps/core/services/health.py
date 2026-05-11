"""
Services for /health/
"""

from __future__ import annotations

import logging

import boto3
from botocore.exceptions import ClientError
from celery import current_app as celery_app
from django.conf import settings
from django.core.cache import cache
from django.db import connection

logger = logging.getLogger(__name__)


def check_postgresql() -> dict:
    """Verify database connectivity with a trivial query."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        return {"status": "healthy"}
    except Exception as exc:
        logger.warning("PostgreSQL health check failed: %s", exc)
        return {"status": "unhealthy", "error": str(exc)}


def check_redis() -> dict:
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


def check_minio() -> dict:
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


def check_celery() -> dict:
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
