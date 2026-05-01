"""
Abstract interfaces for recognizers and OCR engines.

BaseRecognizer: contract for all zone recognizers (QR, OCR, checkbox).
BaseOCREngine: contract for pluggable OCR engines (EasyOCR, Tesseract, etc.).

The dispatcher calls recognizer.recognize(image_bytes) for each zone.
OCR recognizers delegate to the active OCR engine via get_ocr_engine().

References: RF-9.9
"""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(frozen=True)
class RecognitionResult:
    """Result of a zone recognition operation.

    Attributes:
        value: The recognized value (string, number, bool, or dict for QR).
        confidence: Confidence score between 0.0 and 1.0.
        raw_value: Original output before post-processing.
    """

    value: str | dict | bool | None
    confidence: float
    raw_value: str = ""


class BaseRecognizer(abc.ABC):
    """Abstract recognizer for a specific zone type.

    Every zone type (QR, OCR_TEXT, OCR_NUMBER, CHECKBOX) has
    a corresponding recognizer that implements this interface.
    """

    @abc.abstractmethod
    def recognize(self, image_bytes: bytes) -> RecognitionResult:
        """Recognize content from a cropped zone image.

        Args:
            image_bytes: PNG/JPEG bytes of the cropped zone region.

        Returns:
            RecognitionResult with value and confidence.
        """


class BaseOCREngine(abc.ABC):
    """Abstract OCR engine plugin.

    Multiple engines can be registered (EasyOCR, Tesseract, etc.).
    The organization configuration selects which engine to use.
    """

    @abc.abstractmethod
    def recognize_text(self, image_bytes: bytes, language: str = "es") -> tuple[str, float]:
        """Run OCR on an image and return (text, confidence).

        Args:
            image_bytes: Image bytes to process.
            language: ISO language code for the OCR engine.

        Returns:
            Tuple of (recognized_text, confidence_score).
        """

    @property
    @abc.abstractmethod
    def engine_id(self) -> str:
        """Unique identifier for this engine (e.g. 'easyocr', 'tesseract')."""


# ── Engine registry ──────────────────────────────────────────

_ocr_engines: dict[str, BaseOCREngine] = {}


def register_ocr_engine(engine: BaseOCREngine) -> None:
    """Register an OCR engine plugin."""
    _ocr_engines[engine.engine_id] = engine


def get_ocr_engine(engine_id: str | None = None) -> BaseOCREngine:
    """Get an OCR engine by ID, or the default.

    If engine_id is None, returns the first registered engine.
    """
    if engine_id and engine_id in _ocr_engines:
        return _ocr_engines[engine_id]

    if _ocr_engines:
        return next(iter(_ocr_engines.values()))

    raise RuntimeError(
        "No OCR engine registered. Install easyocr or tesseract and "
        "register an engine via register_ocr_engine()."
    )


def list_ocr_engines() -> list[dict[str, str]]:
    """List all registered OCR engines."""
    return [{"engine_id": e.engine_id, "type": type(e).__name__} for e in _ocr_engines.values()]
