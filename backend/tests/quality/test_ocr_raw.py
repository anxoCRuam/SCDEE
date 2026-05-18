"""Test OCR engines (EasyOCR, Tesseract) against the ground‑truth dataset.

Does NOT involve the student matcher – pure OCR accuracy metrics.
"""

import csv
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.quality.metrics import evaluate
from tests.quality.ocr_utils import (
    engine_smoke_test,
    load_dataset,
    resolve_image_path,
    run_ocr,
)

pytestmark = [pytest.mark.reliability]

_ENGINES = ["easyocr", "tesseract"]
_REPORTS_DIR = Path(__file__).resolve().parent / "dataset" / "reports"


def test_ocr_raw():
    """Run every dataset image through both OCR engines, compute metrics."""
    dataset = load_dataset()
    if not dataset:
        pytest.skip("ground_truth.csv is empty or missing.")

    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    for engine_id in _ENGINES:
        failure = engine_smoke_test(engine_id)
        if failure:
            pytest.skip(f"{engine_id} not available: {failure}")

        rows = []
        timings = []
        for row in dataset:
            img_path = resolve_image_path(row["filename"])
            if not img_path.exists():
                continue
            image_bytes = img_path.read_bytes()
            t0 = time.perf_counter()
            result = run_ocr(image_bytes, engine_id=engine_id, zone_type=row["zone_type"])
            timings.append(time.perf_counter() - t0)
            actual = (result.value or "").strip() if result.value else ""
            metric = evaluate(row["expected_text"], actual, zone_type=row["zone_type"])
            rows.append(
                {
                    "filename": row["filename"],
                    "zone_type": row["zone_type"],
                    "engine": engine_id,
                    "expected": row["expected_text"],
                    "actual": actual,
                    "exact": metric.exact,
                    "within_budget": metric.within_budget,
                    "char_accuracy": f"{metric.char_accuracy:.3f}",
                    "distance": metric.distance,
                    "ocr_confidence": f"{result.confidence:.3f}",
                }
            )

        # Write CSV
        csv_path = _REPORTS_DIR / f"ocr_raw_{engine_id}_{timestamp}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)

        # Print summary
        total = len(rows)
        exact_rate = sum(r["exact"] for r in rows) / total if total else 0
        budget_rate = sum(r["within_budget"] for r in rows) / total if total else 0
        mean_char = sum(float(r["char_accuracy"]) for r in rows) / total if total else 0
        avg_lat = sum(timings) / len(timings) * 1000 if timings else 0

        print(f"\n=== {engine_id} OCR raw ===")
        print(f"  Samples:         {total}")
        print(f"  Exact match:     {exact_rate:.2%}")
        print(f"  Within budget:   {budget_rate:.2%}")
        print(f"  Char accuracy:   {mean_char:.2%}")
        print(f"  Avg latency:     {avg_lat:.0f} ms")
        print(f"  CSV:             {csv_path}")

    # Always pass – this is an informational test
    assert True
