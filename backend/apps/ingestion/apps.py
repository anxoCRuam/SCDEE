from django.apps import AppConfig


class IngestionConfig(AppConfig):
    """SFTP watcher, PDF ingestion, and OCR engine plugins (RF-9)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.ingestion"
    label = "ingestion"
    verbose_name = "Document Ingestion & OCR"

    def ready(self) -> None:
        """Register OCR engine plugins so they are available at runtime (RF-9.9).

        Registration is cheap: it only stores instances in the in-memory
        registry. The actual heavy library import (easyocr, pytesseract)
        happens lazily on the first call to ``recognize_text``.
        """
        from apps.ingestion.recognizers.base import register_ocr_engine
        from apps.ingestion.recognizers.ocr import EasyOCREngine, TesseractEngine

        register_ocr_engine(EasyOCREngine())
        register_ocr_engine(TesseractEngine())
