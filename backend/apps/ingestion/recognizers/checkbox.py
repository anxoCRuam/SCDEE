"""
Checkbox recognizer — fixed, not configurable per organization.

Determines if a checkbox zone is marked by analyzing pixel density.

References: RF-9.6
"""

from __future__ import annotations

import logging

from apps.ingestion.recognizers.base import BaseRecognizer, RecognitionResult

logger = logging.getLogger(__name__)

# Threshold: if more than this fraction of pixels are dark, it's marked.
_MARK_THRESHOLD = 0.15


class CheckboxRecognizer(BaseRecognizer):
    """Detect whether a checkbox is marked via pixel density analysis."""

    def recognize(self, image_bytes: bytes) -> RecognitionResult:
        """Analyze pixel density to determine if marked."""
        try:
            import io

            from PIL import Image

            image = Image.open(io.BytesIO(image_bytes)).convert("L")  # Grayscale
            pixels = list(image.getdata())
            total = len(pixels)

            if total == 0:
                return RecognitionResult(value=False, confidence=0.0)

            # Count dark pixels (below threshold).
            dark_count = sum(1 for p in pixels if p < 128)
            dark_ratio = dark_count / total

            is_marked = dark_ratio > _MARK_THRESHOLD
            # Confidence is higher the further from the threshold.
            distance = abs(dark_ratio - _MARK_THRESHOLD)
            confidence = min(0.5 + distance * 3, 1.0)

            return RecognitionResult(
                value=is_marked,
                confidence=confidence,
                raw_value=f"dark_ratio={dark_ratio:.3f}",
            )

        except Exception as exc:
            logger.warning("Checkbox recognition failed: %s", exc)
            return RecognitionResult(value=None, confidence=0.0, raw_value=str(exc))
