"""
MinIO storage helpers for exam PDF files.

Thin wrapper over boto3 for uploading, downloading, and deleting
objects in MinIO. Keeps storage logic isolated from business logic
so it can be mocked in tests.

References: RF-6.14
"""

from __future__ import annotations

import io
import logging

import boto3
from botocore.exceptions import ClientError
from django.conf import settings

logger = logging.getLogger(__name__)


def _get_s3_client():
    """Create a boto3 S3 client configured for MinIO."""
    return boto3.client(
        "s3",
        endpoint_url=settings.AWS_S3_ENDPOINT_URL,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        region_name=settings.AWS_S3_REGION_NAME,
    )


def upload_to_minio(*, key: str, data: bytes, content_type: str = "application/pdf") -> None:
    """Upload bytes to MinIO."""
    client = _get_s3_client()
    bucket = settings.AWS_STORAGE_BUCKET_NAME

    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=io.BytesIO(data),
        ContentLength=len(data),
        ContentType=content_type,
    )
    logger.debug("Uploaded %d bytes to MinIO: %s/%s", len(data), bucket, key)


def download_from_minio(key: str) -> bytes:
    """Download an object from MinIO and return its bytes."""
    client = _get_s3_client()
    bucket = settings.AWS_STORAGE_BUCKET_NAME

    response = client.get_object(Bucket=bucket, Key=key)
    data = response["Body"].read()
    logger.debug("Downloaded %d bytes from MinIO: %s/%s", len(data), bucket, key)
    return data


def delete_minio_object(key: str) -> None:
    """Delete an object from MinIO. Silently ignores missing objects."""
    try:
        client = _get_s3_client()
        bucket = settings.AWS_STORAGE_BUCKET_NAME
        client.delete_object(Bucket=bucket, Key=key)
        logger.debug("Deleted MinIO object: %s/%s", bucket, key)
    except ClientError as exc:
        logger.warning("Failed to delete MinIO object %s: %s", key, exc)


def generate_presigned_url(key: str, expiration: int | None = None) -> str:
    """Generate a temporary download URL for a MinIO object."""
    client = _get_s3_client()
    bucket = settings.AWS_STORAGE_BUCKET_NAME

    if expiration is None:
        expiration = settings.AWS_PRESIGNED_URL_EXPIRY

    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expiration,
    )
