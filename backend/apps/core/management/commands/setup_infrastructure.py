"""
Management command: setup_infrastructure

Performs one-time infrastructure setup that sits outside Django migrations:
1. Creates the MinIO bucket if it doesn't exist.
2. Applies the audit log immutability trigger to PostgreSQL.

Run after the first `python manage.py migrate`:
    python manage.py setup_infrastructure

Idempotent: safe to run multiple times.

References: RNF-14, RF-16.1
"""

import logging

import boto3
from botocore.exceptions import ClientError
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Initialize MinIO bucket and apply audit log DB trigger."

    def handle(self, *args, **options):
        self._setup_minio_bucket()
        self._apply_audit_trigger()
        self.stdout.write(self.style.SUCCESS("Infrastructure setup complete."))

    def _setup_minio_bucket(self) -> None:
        """Create the default MinIO bucket if it doesn't exist."""
        bucket_name = settings.AWS_STORAGE_BUCKET_NAME
        self.stdout.write(f"Checking MinIO bucket '{bucket_name}'...")

        try:
            s3_client = boto3.client(
                "s3",
                endpoint_url=settings.AWS_S3_ENDPOINT_URL,
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                region_name=settings.AWS_S3_REGION_NAME,
            )

            try:
                s3_client.head_bucket(Bucket=bucket_name)
                self.stdout.write(f"  Bucket '{bucket_name}' already exists.")
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code in ("404", "NoSuchBucket"):
                    s3_client.create_bucket(Bucket=bucket_name)
                    self.stdout.write(self.style.SUCCESS(f"  Created bucket '{bucket_name}'."))
                else:
                    raise
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"  MinIO setup failed: {exc}"))

    def _apply_audit_trigger(self) -> None:
        """Apply the immutability trigger to the audit_auditlog table."""
        self.stdout.write("Applying audit log immutability trigger...")

        trigger_sql = """
        -- Create the prevention function (idempotent).
        CREATE OR REPLACE FUNCTION prevent_audit_modification()
        RETURNS TRIGGER AS $$
        BEGIN
            RAISE EXCEPTION 'Audit log entries are immutable (RF-16.1).';
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql;

        -- Drop existing trigger if any (idempotent).
        DROP TRIGGER IF EXISTS no_audit_update ON audit_auditlog;
        DROP TRIGGER IF EXISTS no_audit_delete ON audit_auditlog;

        -- Create triggers to block UPDATE and DELETE.
        CREATE TRIGGER no_audit_update
            BEFORE UPDATE ON audit_auditlog
            FOR EACH ROW
            EXECUTE FUNCTION prevent_audit_modification();

        CREATE TRIGGER no_audit_delete
            BEFORE DELETE ON audit_auditlog
            FOR EACH ROW
            EXECUTE FUNCTION prevent_audit_modification();
        """

        try:
            with connection.cursor() as cursor:
                cursor.execute(trigger_sql)
            self.stdout.write(self.style.SUCCESS("  Audit immutability triggers applied."))
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"  Audit trigger setup failed: {exc}"))
