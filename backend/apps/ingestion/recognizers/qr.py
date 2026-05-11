"""
QR code recognizer — fixed, not configurable per organization.

Decodes QR codes from zone images, extracts the JSON payload
(org_id, exam_id, model_id, page_number, checksum) and validates it.

References: RF-9.3
"""

from __future__ import annotations

import hashlib
import json
import logging

from apps.ingestion.recognizers.base import BaseRecognizer, RecognitionResult

logger = logging.getLogger(__name__)


class QRRecognizer(BaseRecognizer):
    """Decode QR codes from cropped zone images."""

    def recognize(self, image_bytes: bytes) -> RecognitionResult:
        """Decode QR from image bytes.

        Returns RecognitionResult where value is a dict with
        org_id, exam_id, model_id, page_number, and valid flag.
        """
        try:
            import io

            from PIL import Image
            from pyzbar.pyzbar import decode as pyzbar_decode

            image = Image.open(io.BytesIO(image_bytes))
            decoded_objects = pyzbar_decode(image)

            if not decoded_objects:
                return RecognitionResult(value=None, confidence=0.0, raw_value="")

            # Take the first decoded QR.
            raw_data = decoded_objects[0].data.decode("utf-8")
            payload = json.loads(raw_data)

            # Validate checksum.
            expected_checksum = self._compute_checksum(
                org_id=payload.get("o", ""),
                exam_id=payload.get("e", ""),
                model_id=payload.get("m", ""),
                page_number=payload.get("p", 0),
            )

            is_valid = payload.get("c", "") == expected_checksum

            result = {
                "org_id": payload.get("o", ""),
                "exam_id": payload.get("e", ""),
                "model_id": payload.get("m", ""),
                "page_number": payload.get("p", 0),
                "checksum": payload.get("c", ""),
                "valid": is_valid,
            }

            confidence = 1.0 if is_valid else 0.5
            return RecognitionResult(value=result, confidence=confidence, raw_value=raw_data)

        except Exception as exc:
            logger.warning("QR recognition failed: %s", exc)
            return RecognitionResult(value=None, confidence=0.0, raw_value=str(exc))

    @staticmethod
    def _compute_checksum(org_id: str, exam_id: str, model_id: str, page_number: int) -> str:
        """Compute expected checksum (same algorithm as instrumented_pdf.py)."""
        raw = f"{org_id}:{exam_id}:{model_id}:{page_number}"
        return hashlib.sha256(raw.encode()).hexdigest()[:8]
