"""
OCR recognizer for text and numeric zones.

Uses the pluggable OCR engine system. For OCR_NUMBER zones,
validates that the result is numeric.

Also includes the EasyOCR engine plugin as the default engine.

References: RF-9.5, RF-9.7, RF-9.8
"""

from __future__ import annotations

import logging
import re

from apps.ingestion.recognizers.base import (
    BaseOCREngine,
    BaseRecognizer,
    RecognitionResult,
    get_ocr_engine,
)

logger = logging.getLogger(__name__)


class OCRRecognizer(BaseRecognizer):
    """Recognizer for OCR_TEXT and OCR_NUMBER zones."""

    def __init__(self, zone_type: str = "OCR_TEXT", engine_id: str | None = None):
        self.zone_type = zone_type
        self.engine_id = engine_id

    def recognize(self, image_bytes: bytes) -> RecognitionResult:
        """Run OCR on the zone image."""
        try:
            engine = get_ocr_engine(self.engine_id)
            text, confidence = engine.recognize_text(image_bytes)
        except Exception as exc:
            logger.warning("OCR recognition failed: %s", exc)
            return RecognitionResult(value=None, confidence=0.0, raw_value=str(exc))

        # Post-process based on zone type.
        if self.zone_type == "OCR_NUMBER":
            return self._process_number(text, confidence)

        return RecognitionResult(value=text.strip(), confidence=confidence, raw_value=text)

    def _process_number(self, text: str, confidence: float) -> RecognitionResult:
        """Validate and clean numeric OCR results."""
        # Remove non-numeric characters except decimal separators.
        cleaned = re.sub(r"[^\d.,\-]", "", text)
        # Normalize decimal separator.
        cleaned = cleaned.replace(",", ".")

        try:
            float(cleaned)
            return RecognitionResult(value=cleaned, confidence=confidence, raw_value=text)
        except ValueError:
            return RecognitionResult(
                value=None,
                confidence=confidence * 0.3,  # Low confidence for non-numeric.
                raw_value=text,
            )


class EasyOCREngine(BaseOCREngine):
    """OCR engine using EasyOCR (default plugin).

    EasyOCR is pure Python, GPU-optional, and supports Spanish.
    Lazy-loads the reader on first use to avoid startup cost.
    """

    _reader = None

    @property
    def engine_id(self) -> str:
        return "easyocr"

    def recognize_text(self, image_bytes: bytes, language: str = "es") -> tuple[str, float]:
        """Run EasyOCR on image bytes."""
        reader = self._get_reader(language)
        import io

        import numpy as np
        from PIL import Image

        image = Image.open(io.BytesIO(image_bytes))
        image_array = np.array(image)

        results = reader.readtext(image_array)

        if not results:
            return ("", 0.0)

        # Combine all detected text blocks.
        texts = []
        confidences = []
        for bbox, text, conf in results:  # noqa: B007
            texts.append(text)
            confidences.append(conf)

        combined_text = " ".join(texts)
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        return (combined_text, avg_confidence)

    def _get_reader(self, language: str = "es"):
        """Lazy-load the EasyOCR reader."""
        if EasyOCREngine._reader is None:
            try:
                import easyocr

                EasyOCREngine._reader = easyocr.Reader(
                    [language, "en"],
                    gpu=False,  # CPU mode for container compatibility.
                )
            except ImportError:
                raise RuntimeError(
                    "EasyOCR is not installed. Install with: pip install easyocr"
                ) from None
        return EasyOCREngine._reader


class TesseractEngine(BaseOCREngine):
    """OCR engine using Tesseract (alternative plugin).

    Requires tesseract binary installed in the system.
    """

    @property
    def engine_id(self) -> str:
        return "tesseract"

    def recognize_text(self, image_bytes: bytes, language: str = "es") -> tuple[str, float]:
        """Run Tesseract on image bytes."""
        try:
            import io

            import pytesseract
            from PIL import Image

            image = Image.open(io.BytesIO(image_bytes))
            # Use --oem 3 (LSTM) and --psm 6 (single block).
            data = pytesseract.image_to_data(
                image, lang=language, output_type=pytesseract.Output.DICT
            )

            texts = []
            confidences = []
            for i, conf in enumerate(data["conf"]):
                if int(conf) > 0:
                    texts.append(data["text"][i])
                    confidences.append(int(conf) / 100.0)

            text = " ".join(t for t in texts if t.strip())
            avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

            return (text, avg_conf)

        except ImportError:
            raise RuntimeError(
                "pytesseract is not installed. Install with: pip install pytesseract"
            ) from None
