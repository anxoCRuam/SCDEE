"""Shared helpers for OCR quality tests."""

from __future__ import annotations

import csv
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_DATASET_DIR = _HERE / "dataset"
_GROUND_TRUTH_CSV = _DATASET_DIR / "ground_truth.csv"
_IMAGES_DIR = _DATASET_DIR / "images"


def load_dataset() -> list[dict[str, str]]:
    if not _GROUND_TRUTH_CSV.exists():
        return []
    with open(_GROUND_TRUTH_CSV, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def resolve_image_path(filename: str) -> Path:
    return _IMAGES_DIR / filename


def run_ocr(image_bytes: bytes, *, engine_id: str, zone_type: str, language: str = "es"):
    from apps.ingestion.recognizers.base import get_ocr_engine

    # Tesseract necesita spa, no es
    if engine_id == "tesseract":
        language = "spa"

    engine = get_ocr_engine(engine_id)
    text, confidence = engine.recognize_text(image_bytes, language=language)

    if zone_type.upper() in ("NIA", "NUMBER"):
        import re

        cleaned = re.sub(r"[^\d.,\-]", "", text).replace(",", ".")
        try:
            float(cleaned) if cleaned else None
            value = cleaned
        except ValueError:
            value = None
            confidence *= 0.3
    else:
        value = text.strip()

    from apps.ingestion.recognizers.base import RecognitionResult

    return RecognitionResult(value=value, confidence=confidence, raw_value=text)


def engine_smoke_test(engine_id: str, language: str = "es") -> str | None:
    import io

    from PIL import Image

    from apps.ingestion.recognizers.base import get_ocr_engine

    if engine_id == "tesseract":
        language = "spa"

    try:
        engine = get_ocr_engine(engine_id)
    except Exception as exc:
        return f"Engine {engine_id!r} could not be resolved: {exc}"

    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color="white").save(buf, format="PNG")
    try:
        engine.recognize_text(buf.getvalue(), language=language)
    except Exception as exc:
        return (
            f"Engine {engine_id!r} failed on a smoke-test image with language {language!r}: {exc}."
        )
    return None
