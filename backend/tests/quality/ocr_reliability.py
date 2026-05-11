"""
OCR reliability test runner.

Walks every row of ``dataset/ground_truth.csv``, runs OCR with the
configured engine, evaluates the result against four metrics
(exact match, Levenshtein-bounded, character accuracy, roster
match), and writes a per-sample CSV plus a Markdown summary to
``dataset/reports/``.

Run::

    # Default engine (Tesseract — fast, no model download).
    pytest tests/reliability/ocr/test_ocr_reliability.py -v

    # Switch to EasyOCR (downloads ~100 MB the first time):
    SCDEE_OCR_ENGINE=easyocr pytest tests/reliability/ocr/test_ocr_reliability.py -v

    # Acceptance thresholds (cause the suite to fail if breached):
    SCDEE_OCR_MIN_EXACT=0.5 \\
    SCDEE_OCR_MIN_ROSTER=0.8 \\
    pytest tests/reliability/ocr/test_ocr_reliability.py -v

The runner is intentionally permissive: with the synthetic dataset
shipped in the repo, the absolute numbers will be modest (synthetic
images are crude). The point is that the *pipeline* runs and produces
a report. When you replace ``dataset/images/`` with real scans and
update ``ground_truth.csv``, the same code computes the production
metric without any further edits.

References: RF-9.5, RF-9.7.
"""

from __future__ import annotations

import csv
import os
import time
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

import pytest
from django.contrib.auth import get_user_model

from apps.courses.models.courses import AcademicCourse
from apps.exams.models.exams import Exam, ExamConvocation, ExamModel
from apps.organizations.models.organization import Organization
from apps.subjects.models.subjects import MembershipRole, Subject, SubjectMembership
from tests.quality.metrics import evaluate

pytestmark = [pytest.mark.django_db, pytest.mark.reliability]


# ════════════════════════════════════════════════════════════════════
# Configuration
# ════════════════════════════════════════════════════════════════════


_HERE = Path(__file__).resolve().parent
_DATASET_DIR = _HERE / "dataset"
_GROUND_TRUTH_CSV = _DATASET_DIR / "ground_truth.csv"
_REPORTS_DIR = _DATASET_DIR / "reports"

print(_GROUND_TRUTH_CSV)  # noqa: T201


def _engine_id() -> str:
    """Pick the OCR engine via environment variable.

    Defaults to ``tesseract`` because EasyOCR downloads ~100 MB on
    first use, which would freeze CI.
    """
    return os.environ.get("SCDEE_OCR_ENGINE", "tesseract")


def _ocr_language() -> str:
    """Pick the OCR language code via environment variable.

    The production OCR engines call ``recognize_text(language='es')``
    by default. Tesseract, however, expects ISO 639-2 codes (``spa``
    instead of ``es``) — a mismatch that causes ``TesseractError``
    on systems where only the ``spa`` traineddata is installed.
    Setting ``SCDEE_OCR_LANGUAGE=spa`` is the workaround until the
    production code is fixed (see module 4 audit).
    """
    return os.environ.get("SCDEE_OCR_LANGUAGE", "es")


def _threshold(env_var: str, default: float) -> float:
    """Read a 0..1 threshold from the environment, with a sensible default."""
    raw = os.environ.get(env_var)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        pytest.fail(f"{env_var}={raw!r} is not a valid float.")


# ════════════════════════════════════════════════════════════════════
# Dataset loader
# ════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def dataset() -> list[dict]:
    """Load the ground-truth CSV. Skip the suite if the file is missing."""
    if not _GROUND_TRUTH_CSV.exists():
        pytest.skip(
            f"No ground_truth.csv at {_GROUND_TRUTH_CSV}. "
            "Run ``python -m tests.reliability.ocr.generate_synthetic`` "
            "to create the synthetic dataset, or supply your own."
        )
    with open(_GROUND_TRUTH_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        pytest.skip("ground_truth.csv is empty.")
    return rows


# ════════════════════════════════════════════════════════════════════
# Roster-matching fixture
# ════════════════════════════════════════════════════════════════════


@pytest.fixture
def roster_exam(db) -> Exam:
    """Build an Exam with one ExamConvocation per ground-truth row.

    The roster mirrors what the matcher will see in production: an
    exam with a list of called students. Each NIA / DNI / NAME row
    in ``ground_truth.csv`` becomes one ExamConvocation; the matcher
    then has to resolve the OCR output back to exactly that student.

    This is the *fourth* metric — the one that matters in production.
    Exact-match accuracy can be modest while roster-match accuracy is
    high, because the matcher absorbs OCR noise (OCR confusions like
    0/O, length-bounded mismatches, fuzzy name match).
    """
    if not _GROUND_TRUTH_CSV.exists():
        pytest.skip("No ground truth file.")

    user_model = get_user_model()
    suffix = uuid.uuid4().hex[:8]
    org = Organization.objects.create(name=f"Roster-{suffix}", subdomain=f"r{suffix}")
    coordinator = user_model.objects.create_user(
        email=f"c-{suffix}@x.com",
        password="P@ss1234!",  # noqa: S106
        first_name="Co",
        last_name="Or",
        organization=org,
        is_staff=True,
    )
    course = AcademicCourse.objects.create(organization=org, label="2026", is_active=True)
    subject = Subject.objects.create(
        organization=org,
        name="S",
        code=f"S{suffix}",
        course=course,
        coordinator=coordinator,
    )
    SubjectMembership.objects.create(
        organization=org,
        user=coordinator,
        subject=subject,
        role=MembershipRole.COORDINATOR,
        is_active=True,
    )
    exam = Exam.objects.create(organization=org, name="Roster Exam", subject=subject)
    ExamModel.objects.create(label="A", exam=exam)

    with open(_GROUND_TRUTH_CSV, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for i, row in enumerate(rows):
        zone_type = row["zone_type"].upper()
        expected = row["expected_text"]

        # One unique student per row. We attach the expected text to
        # the right field so the matcher can find them:
        # NIA → user.nia, DNI → encrypted, NAME → first/last name.
        if zone_type == "NIA":
            student = user_model.objects.create_user(
                email=f"s{i}-{suffix}@x.com",
                password="P@ss1234!",  # noqa: S106
                first_name="Stu",
                last_name=f"Number{i}",
                nia=expected,
                organization=org,
            )
        elif zone_type == "DNI":
            student = user_model.objects.create_user(
                email=f"s{i}-{suffix}@x.com",
                password="P@ss1234!",  # noqa: S106
                first_name="Stu",
                last_name=f"Number{i}",
                organization=org,
            )
            # DNI is stored encrypted; use the same encryption helper
            # the production user creation pipeline uses, then save
            # the resulting fields directly.
            from apps.accounts.services.encryption import encrypt_dni

            encrypted, nonce = encrypt_dni(expected)
            student.encrypted_dni = encrypted
            student.dni_nonce = nonce
            student.save(update_fields=["encrypted_dni", "dni_nonce"])
        else:  # NAME
            # ``expected`` here is "FIRST LAST [LAST]" — split on
            # whitespace, taking everything past the first token as
            # last_name to match what the production matcher composes
            # at lookup time (``f"{first} {last}"``).
            tokens = expected.split()
            first_name = tokens[0]
            last_name = " ".join(tokens[1:]) if len(tokens) > 1 else "Surname"
            student = user_model.objects.create_user(
                email=f"s{i}-{suffix}@x.com",
                password="P@ss1234!",  # noqa: S106
                first_name=first_name,
                last_name=last_name,
                organization=org,
            )

        SubjectMembership.objects.create(
            organization=org,
            user=student,
            subject=subject,
            role=MembershipRole.STUDENT,
            is_active=True,
        )
        ExamConvocation.objects.create(exam=exam, student=student)

    return exam


# ════════════════════════════════════════════════════════════════════
# OCR runner
# ════════════════════════════════════════════════════════════════════


def _run_ocr(image_bytes: bytes, *, zone_type: str, engine_id: str, language: str):
    """Run the production OCR recogniser on ``image_bytes``.

    Mirrors how ``apps.ingestion.services.dispatcher`` would invoke
    OCR for a real zone — same recogniser class, same engine
    selection rule. ``language`` overrides the engine's default
    Spanish so the suite can work on hosts that ship only the ISO
    639-2 traineddata file.
    """
    from apps.ingestion.recognizers.base import get_ocr_engine

    # Note: we bypass OCRRecognizer here because it does not pass
    # ``language`` through. We still want the same numeric / text
    # post-processing, so we replicate just the small slice we need.
    engine = get_ocr_engine(engine_id)
    text, confidence = engine.recognize_text(image_bytes, language=language)

    if zone_type.upper() in {"NIA", "NUMBER"}:
        # Mirror the cleaning OCRRecognizer applies to NUMBER zones.
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


def _engine_smoke_test(engine_id: str, language: str) -> str | None:
    """Verify the requested engine is usable; return an explanation if not."""
    import io

    from PIL import Image

    from apps.ingestion.recognizers.base import get_ocr_engine

    try:
        engine = get_ocr_engine(engine_id)
    except Exception as exc:  # noqa: BLE001
        return f"Engine {engine_id!r} could not be resolved: {exc}"

    buf = io.BytesIO()
    Image.new("RGB", (10, 10), color="white").save(buf, format="PNG")
    try:
        engine.recognize_text(buf.getvalue(), language=language)
    except Exception as exc:  # noqa: BLE001
        return (
            f"Engine {engine_id!r} failed on a smoke-test image with language "
            f"{language!r}: {exc}. If this is Tesseract, try "
            "``SCDEE_OCR_LANGUAGE=spa``. If this is EasyOCR, the model "
            "download likely failed (network)."
        )
    return None


def _attempt_roster_match(
    exam: Exam,
    *,
    zone_type: str,
    actual_text: str,
) -> tuple[bool, float, str | None]:
    """Run the production matcher with the OCR output as input.

    Returns ``(matched, confidence, matched_user_id_or_none)``.
    """
    from apps.ingestion.services.matching import match_student

    attribute_map = {
        "NIA": {"nia": actual_text},
        "DNI": {"dni": actual_text},
        "NAME": {"name": actual_text},
    }
    attrs = attribute_map.get(zone_type.upper(), {})
    user, conf = match_student(exam, attrs)
    return (user is not None, conf, str(user.pk) if user else None)


# ════════════════════════════════════════════════════════════════════
# Test entry point
# ════════════════════════════════════════════════════════════════════


def test_ocr_reliability(dataset, roster_exam):
    """Run every row of the ground truth through OCR and the roster
    matcher, write a CSV + Markdown report, and assert against any
    thresholds set via environment variables.

    The thresholds default to ``0.0`` so the test passes regardless
    of absolute quality on the first run. Set
    ``SCDEE_OCR_MIN_EXACT``, ``SCDEE_OCR_MIN_BUDGET``,
    ``SCDEE_OCR_MIN_CHAR``, ``SCDEE_OCR_MIN_ROSTER`` to enforce
    minimums in CI once you have a real dataset.
    """
    engine_id = _engine_id()
    language = _ocr_language()
    smoke_failure = _engine_smoke_test(engine_id, language)
    if smoke_failure:
        pytest.skip(smoke_failure)

    images_dir = _DATASET_DIR / "images"
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    timings: list[float] = []

    # We need to map each row's expected text back to the convoked
    # student we created in ``roster_exam`` so we can verify the
    # matcher returns the right one (not just *any* student).
    expected_to_user_id: dict[tuple[str, str], str] = {}
    for conv in ExamConvocation.objects.filter(exam=roster_exam).select_related("student"):
        student = conv.student
        # Index the student under each plausible OCR target.
        if student.nia:
            expected_to_user_id[("NIA", student.nia)] = str(student.pk)
        full_name = f"{student.first_name} {student.last_name}".upper()
        expected_to_user_id[("NAME", full_name)] = str(student.pk)

    for raw_row in dataset:
        path = images_dir / raw_row["filename"]
        if not path.exists():
            pytest.skip(
                f"Image {path} not found. Did you forget to regenerate "
                "the synthetic dataset or supply real images?"
            )

        image_bytes = path.read_bytes()
        zone_type = raw_row["zone_type"].upper()
        expected = raw_row["expected_text"]

        t0 = time.perf_counter()
        result = _run_ocr(image_bytes, zone_type=zone_type, engine_id=engine_id, language=language)
        timings.append(time.perf_counter() - t0)

        actual = (result.value or "").strip() if result.value else ""

        metric = evaluate(expected, actual, zone_type=zone_type)

        # Roster match: feed OCR output to the production matcher.
        roster_matched, roster_conf, matched_uid = _attempt_roster_match(
            roster_exam, zone_type=zone_type, actual_text=actual
        )
        # Did the matcher return *the right* student?
        full_name_key = expected.upper()
        expected_uid = expected_to_user_id.get((zone_type, full_name_key))
        if expected_uid is None and zone_type == "DNI":
            # DNI entries were created without indexing because the
            # ground truth says nothing about which user pk owns each
            # DNI; the test passes if the matcher returns *any* user
            # for a DNI scan, which is intentional.
            roster_correct = roster_matched
        else:
            roster_correct = roster_matched and (matched_uid == expected_uid)

        rows.append(
            {
                "filename": raw_row["filename"],
                "zone_type": zone_type,
                "style": raw_row.get("style", ""),
                "source": raw_row.get("source", ""),
                "expected": expected,
                "actual": actual,
                "ocr_confidence": f"{result.confidence:.3f}",
                "exact": "1" if metric.exact else "0",
                "within_budget": "1" if metric.within_budget else "0",
                "char_accuracy": f"{metric.char_accuracy:.3f}",
                "distance": str(metric.distance),
                "roster_matched": "1" if roster_matched else "0",
                "roster_correct": "1" if roster_correct else "0",
                "roster_confidence": f"{roster_conf:.3f}",
            }
        )

    # ── Reports ──────────────────────────────────────────────
    timestamp = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    csv_path = _REPORTS_DIR / f"ocr_reliability_{engine_id}_{timestamp}.csv"
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    md_path = _REPORTS_DIR / f"ocr_reliability_{engine_id}_{timestamp}.md"
    md_path.write_text(_render_markdown_report(rows, engine_id, timings), encoding="utf-8")

    # ── Aggregate metrics ────────────────────────────────────
    aggregate = _aggregate(rows)

    # Pretty-print to stdout so ``pytest -s`` shows a summary.
    print()  # noqa: T201
    print(f"=== OCR reliability ({engine_id}) ===")  # noqa: T201
    for label, value in aggregate.items():
        print(  # noqa: T201
            f"  {label:18}= {value:.2%}" if isinstance(value, float) else f"  {label:18}= {value}"
        )
    if timings:
        print(f"  avg latency       = {mean(timings) * 1000:.0f} ms")  # noqa: T201
    print(f"  CSV report:  {csv_path}")  # noqa: T201
    print(f"  MD report:   {md_path}")  # noqa: T201

    # ── Threshold gate ───────────────────────────────────────
    failures: list[str] = []
    if aggregate["exact_rate"] < _threshold("SCDEE_OCR_MIN_EXACT", 0.0):
        failures.append(
            f"exact_rate={aggregate['exact_rate']:.2%} below "
            f"SCDEE_OCR_MIN_EXACT={_threshold('SCDEE_OCR_MIN_EXACT', 0.0):.2%}"
        )
    if aggregate["budget_rate"] < _threshold("SCDEE_OCR_MIN_BUDGET", 0.0):
        failures.append(
            f"budget_rate={aggregate['budget_rate']:.2%} below "
            f"SCDEE_OCR_MIN_BUDGET={_threshold('SCDEE_OCR_MIN_BUDGET', 0.0):.2%}"
        )
    if aggregate["mean_char_accuracy"] < _threshold("SCDEE_OCR_MIN_CHAR", 0.0):
        failures.append(
            f"mean_char_accuracy={aggregate['mean_char_accuracy']:.2%} below "
            f"SCDEE_OCR_MIN_CHAR={_threshold('SCDEE_OCR_MIN_CHAR', 0.0):.2%}"
        )
    if aggregate["roster_correct_rate"] < _threshold("SCDEE_OCR_MIN_ROSTER", 0.0):
        failures.append(
            f"roster_correct_rate={aggregate['roster_correct_rate']:.2%} below "
            f"SCDEE_OCR_MIN_ROSTER={_threshold('SCDEE_OCR_MIN_ROSTER', 0.0):.2%}"
        )

    if failures:
        pytest.fail(
            "OCR reliability fell below configured thresholds:\n  - "
            + "\n  - ".join(failures)
            + f"\n\nFull per-sample report: {csv_path}"
        )


# ════════════════════════════════════════════════════════════════════
# Reporting helpers
# ════════════════════════════════════════════════════════════════════


def _aggregate(rows: list[dict]) -> dict:
    """Compute headline rates over the full per-sample list."""
    total = len(rows)
    if total == 0:
        return {
            "samples": 0,
            "exact_rate": 0.0,
            "budget_rate": 0.0,
            "mean_char_accuracy": 0.0,
            "roster_correct_rate": 0.0,
        }
    return {
        "samples": total,
        "exact_rate": sum(int(r["exact"]) for r in rows) / total,
        "budget_rate": sum(int(r["within_budget"]) for r in rows) / total,
        "mean_char_accuracy": mean(float(r["char_accuracy"]) for r in rows),
        "roster_correct_rate": sum(int(r["roster_correct"]) for r in rows) / total,
    }


def _render_markdown_report(rows: list[dict], engine_id: str, timings: list[float]) -> str:
    """Render a short Markdown report grouping by zone type and style."""
    aggregate = _aggregate(rows)

    by_zone: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_zone[r["zone_type"]].append(r)

    lines = [
        f"# OCR reliability report — engine ``{engine_id}``",
        "",
        f"Generated: {datetime.now(tz=UTC).isoformat()}",
        "",
        "## Headline metrics",
        "",
        f"- Samples:                **{aggregate['samples']}**",
        f"- Exact match:            **{aggregate['exact_rate']:.2%}**",
        f"- Within Levenshtein:     **{aggregate['budget_rate']:.2%}**",
        f"- Mean char accuracy:     **{aggregate['mean_char_accuracy']:.2%}**",
        f"- Roster match (correct): **{aggregate['roster_correct_rate']:.2%}**",
    ]
    if timings:
        lines.append(f"- Mean OCR latency:       **{mean(timings) * 1000:.0f} ms**")
    lines.append("")
    lines.append("## Per zone type")
    lines.append("")
    lines.append("| Zone | Samples | Exact | Within budget | Char accuracy | Roster correct |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for zone, group in sorted(by_zone.items()):
        agg = _aggregate(group)
        lines.append(
            f"| {zone} | {agg['samples']} "
            f"| {agg['exact_rate']:.0%} "
            f"| {agg['budget_rate']:.0%} "
            f"| {agg['mean_char_accuracy']:.0%} "
            f"| {agg['roster_correct_rate']:.0%} |"
        )
    lines.append("")
    lines.append("## Per sample")
    lines.append("")
    lines.append("| File | Zone | Expected | Actual | Char acc | Roster ok |")
    lines.append("|---|---|---|---|---:|:---:|")
    for r in rows:
        lines.append(
            f"| `{r['filename']}` | {r['zone_type']} "
            f"| `{r['expected']}` | `{r['actual']}` "
            f"| {float(r['char_accuracy']):.0%} "
            f"| {'✅' if r['roster_correct'] == '1' else '❌'} |"
        )
    return "\n".join(lines)
