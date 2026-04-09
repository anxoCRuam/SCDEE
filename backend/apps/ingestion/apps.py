from django.apps import AppConfig


class IngestionConfig(AppConfig):
    """SFTP watcher, PDF ingestion, and OCR engine plugins (RF-9)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ingestion"
    label = "ingestion"
    verbose_name = "Document Ingestion & OCR"
