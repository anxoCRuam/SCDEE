"""Measure OCR + matching accuracy on the dataset.

For each available OCR engine, runs the engine over the dataset images
and feeds the recognised values directly into the matching algorithm
(`scoring.py` + `matching.py`). Reports accuracy as the fraction of
pages assigned to the correct convoked student.

Compared to `test_ocr_raw.py` (which measures OCR alone), this test
measures the value added by the matching layer: an OCR field doesn't
need to be exact for the assignment to be correct, because the matcher
discriminates against the rest of the roster.

This test deliberately bypasses Django:
    - Roster and lots are built from the CSV and OCR output directly
      as `StudentCandidate` and `LotCandidate` dataclasses.
    - The matching API is called as a pure function.
    - No ExamPage, ExamInstance, or convocation records are created.

The DB-level persistence (creating ExamInstance, attaching pages,
raising incidences) is exercised by the production code path; if you
want explicit coverage of it, write a separate fast test that feeds
synthetic OCR data into `assemble_exam` — no real images needed.

Related tests:
    - test_ocr_raw.py        : OCR alone, per-engine accuracy.
    - evaluate_matching.py   : matching alone, per-strategy grid.
    - this test              : OCR + matching, per-engine accuracy.
"""

from __future__ import annotations

import csv
import re
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

import pytest

from apps.ingestion.services.matching import (
    LotCandidate,
    StudentCandidate,
    assign_lots_to_students,
)
from apps.ingestion.services.scoring import DEFAULT_STRATEGY
from tests.quality.ocr_utils import (
    engine_smoke_test,
    load_dataset,
    resolve_image_path,
    run_ocr,
)

pytestmark = [pytest.mark.reliability]

_ENGINES = ["easyocr", "tesseract"]
_THRESHOLD = 0.05
_MIN_ACCURACY = 0.80
_REPORTS_DIR = Path(__file__).resolve().parent / "dataset" / "reports"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def test_ocr_plus_matching_accuracy():
    """OCR + matching must achieve at least `_MIN_ACCURACY`."""
    rows = load_dataset()
    if not rows:
        pytest.skip("ground_truth.csv is empty or missing.")

    student_data = _group_rows_by_student(rows)
    if not student_data:
        pytest.skip("No student in ground_truth.csv has the three required zones.")

    roster = _build_roster(student_data)
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")

    for engine_id in _ENGINES:
        failure = engine_smoke_test(engine_id)
        if failure:
            pytest.skip(f"{engine_id} not available: {failure}")

        lots = _build_lots_from_ocr(student_data, engine_id)
        if not lots:
            pytest.skip(f"{engine_id} produced no usable OCR output.")

        t0 = time.perf_counter()
        assignment = assign_lots_to_students(
            lots=lots,
            students=roster,
            strategy=DEFAULT_STRATEGY,
            threshold=_THRESHOLD,
        )
        matching_time_ms = (time.perf_counter() - t0) * 1000

        # Evaluate accuracy
        correct = 0
        results = []
        for lot_key, result in assignment.items():
            is_correct = result.student_id == lot_key
            if is_correct:
                correct += 1
            # Gather detailed info for report
            original_lot = next(lot for lot in lots if lot.key == lot_key)
            results.append(
                {
                    "student_id": lot_key,
                    "ocr_name": " ".join(original_lot.ocr_values.get("name", [])),
                    "ocr_nia": " ".join(original_lot.ocr_values.get("nia", [])),
                    "ocr_dni": " ".join(original_lot.ocr_values.get("dni", [])),
                    "assigned_student_id": result.student_id,
                    "correct": is_correct,
                    "score": result.score,
                    "matching_time_ms": matching_time_ms,
                }
            )

        accuracy = correct / len(lots)

        print(f"\n=== {engine_id} OCR + matching ===")
        print(f"  Accuracy: {correct}/{len(lots)} = {accuracy:.1%}")
        print(f"  Matching latency: {matching_time_ms:.2f} ms")
        print(f"  Threshold: {_THRESHOLD}, weights: {DEFAULT_STRATEGY.weights}")

        # Write detailed CSV report
        csv_path = _REPORTS_DIR / f"ocr_matching_{engine_id}_{timestamp}.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "student_id",
                    "ocr_name",
                    "ocr_nia",
                    "ocr_dni",
                    "assigned_student_id",
                    "correct",
                    "score",
                    "matching_time_ms",
                ],
            )
            writer.writeheader()
            writer.writerows(results)

        print(f"  Detailed report saved to: {csv_path}")

        # CI gate
        assert accuracy >= _MIN_ACCURACY, (
            f"{engine_id} matching accuracy {accuracy:.1%} below minimum "
            f"threshold {_MIN_ACCURACY:.0%}"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _group_rows_by_student(rows: list[dict]) -> dict[str, dict[str, dict]]:
    """Group ground-truth rows by synthetic student id.

    Each filename is shaped `<prefix>_<kind><n>.<ext>`. Rows sharing the
    same `(prefix, n)` belong to the same student. Only students with
    all three required zones (name, nia, dni) are kept.
    """
    pattern = re.compile(r"^(.*?)_(name|nia|dni)(\d+)\.")
    grouped: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        m = pattern.match(row["filename"])
        if not m:
            continue
        sid = f"{m.group(1)}#{m.group(3)}"
        grouped[sid][m.group(2)] = row
    return {
        sid: fields
        for sid, fields in grouped.items()
        if {"name", "nia", "dni"} <= set(fields.keys())
    }


def _build_roster(student_data: dict[str, dict[str, dict]]) -> list[StudentCandidate]:
    """Build the convoked-students roster from the `expected_text` column."""
    return [
        StudentCandidate(
            student_id=sid,
            name=fields["name"]["expected_text"],
            nia=fields["nia"]["expected_text"],
            dni=fields["dni"]["expected_text"],
        )
        for sid, fields in student_data.items()
    ]


def _build_lots_from_ocr(
    student_data: dict[str, dict[str, dict]],
    engine_id: str,
) -> list[LotCandidate]:
    """Run OCR over every (student, zone) and build one lot per student.

    Each lot's `key` is the synthetic student id (`sid`), matching the
    id of the roster entry it should ideally match. The assertion in
    the test relies on this: `result.student_id == lot_key` means the
    matcher chose the correct student.
    """
    lots: list[LotCandidate] = []
    for sid, fields in student_data.items():
        ocr_values: dict[str, list[str]] = {"name": [], "nia": [], "dni": []}
        for zone_type in ("NAME", "DNI", "NIA"):
            row = fields[zone_type.lower()]
            img_path = resolve_image_path(row["filename"])
            if not img_path.exists():
                continue
            image_bytes = img_path.read_bytes()
            result = run_ocr(image_bytes, engine_id=engine_id, zone_type=zone_type)
            actual = (result.value or "").strip() if result.value else ""
            if actual:
                attr = "name" if zone_type == "NAME" else zone_type.lower()
                ocr_values[attr].append(actual)
        if any(ocr_values.values()):
            lots.append(LotCandidate(key=sid, ocr_values=ocr_values))
    return lots
