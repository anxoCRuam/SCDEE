"""
Metrics for the OCR reliability suite.

Four ways to measure how close an OCR result is to the ground truth:

1. **Exact match** — strict, post-normalisation byte equality.
2. **Levenshtein-bounded** — accept if the edit distance fits a
   per-zone-type budget. Reflects what a tolerant downstream consumer
   sees.
3. **Character accuracy** — ``1 − distance / max_len``. Continuous
   metric that distinguishes "almost right" from "completely off",
   useful when reporting aggregate quality.
4. **Roster match** — runs the production
   :func:`apps.ingestion.services.matching.match_student` against the
   OCR output and checks whether the right student is identified. This
   is the one that matters in production, because the matcher is the
   layer that absorbs OCR noise; raw OCR accuracy is only a proxy for
   end-to-end success.

The first three live as pure functions (no DB, no Django) so they can
be reused in any context. The fourth requires a Django model context
(an :class:`Exam` with convocations) and is invoked by the test
runner.
"""

from __future__ import annotations

from dataclasses import dataclass

# ════════════════════════════════════════════════════════════════════
# Per-zone-type defaults
# ════════════════════════════════════════════════════════════════════
#
# Levenshtein budgets are tighter for short, rigid identifiers (NIA,
# DNI) and looser for free-form names. Empirically these match what
# the production matcher tolerates: ``_ocr_tolerant_match`` accepts
# 1 mismatch under length 10 and 2 above; for names it uses the 0.7
# similarity threshold (= ~30% of characters allowed to be wrong).

DEFAULT_LEVENSHTEIN_BUDGET: dict[str, int] = {
    "NIA": 1,
    "DNI": 1,
    "NAME": 4,
    "TEXT": 4,
    "NUMBER": 1,
}


# ════════════════════════════════════════════════════════════════════
# Result wrapper
# ════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class MetricResult:
    """Outcome of evaluating one OCR result against one expected value.

    Attributes:
        expected: The ground-truth string for this sample.
        actual: The string returned by the OCR engine (post-strip).
        exact: ``True`` iff ``actual == expected`` after normalisation.
        within_budget: ``True`` iff the Levenshtein distance is within
            the configured budget for this zone type.
        char_accuracy: Float in ``[0.0, 1.0]``: ``1 − dist / max_len``.
            ``1.0`` for exact matches, ``0.0`` for completely
            unrelated strings.
        distance: Raw edit distance (helpful when debugging false
            positives).
    """

    expected: str
    actual: str
    exact: bool
    within_budget: bool
    char_accuracy: float
    distance: int


# ════════════════════════════════════════════════════════════════════
# Pure metrics
# ════════════════════════════════════════════════════════════════════


def normalise(text: str) -> str:
    """Standard normalisation applied before every comparison.

    Strips outer whitespace, collapses internal runs of whitespace to
    single spaces, and uppercases. Mirrors the production matcher's
    own normalisation in ``_ocr_tolerant_match`` for IDs, and is
    conservative enough not to alter the meaning of a name.
    """
    return " ".join(text.strip().upper().split())


def levenshtein_distance(a: str, b: str) -> int:
    """Compute the Levenshtein edit distance between two strings.

    Iterative two-row implementation: O(len(a) * len(b)) time, O(min)
    space. Same algorithm the production matcher uses, kept here as a
    private copy so reliability metrics do not depend on the import
    layout of the production code (which would couple the suite to
    refactors in ``apps.ingestion.services.matching``).
    """
    if len(a) < len(b):
        return levenshtein_distance(b, a)

    if not b:
        return len(a)

    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            ins = prev[j + 1] + 1
            dele = curr[j] + 1
            sub = prev[j] + (ca != cb)
            curr.append(min(ins, dele, sub))
        prev = curr
    return prev[-1]


def char_accuracy(a: str, b: str) -> float:
    """Return ``1 − distance / max_len`` as a float in ``[0.0, 1.0]``.

    Two empty strings score 1.0 (vacuously equal). One empty and one
    non-empty score 0.0.
    """
    if not a and not b:
        return 1.0
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1.0
    return 1.0 - levenshtein_distance(a, b) / max_len


def evaluate(
    expected: str,
    actual: str,
    *,
    zone_type: str = "TEXT",
    budget: int | None = None,
) -> MetricResult:
    """Run the three pure metrics against one (expected, actual) pair.

    Args:
        expected: Ground-truth string.
        actual: String returned by the OCR engine.
        zone_type: One of ``NIA``, ``DNI``, ``NAME``, ``TEXT``,
            ``NUMBER``. Picks the default Levenshtein budget.
        budget: Override for the Levenshtein budget. ``None`` means
            "use the default for ``zone_type``".

    Returns:
        :class:`MetricResult` with the four scalars filled in.
    """
    expected_n = normalise(expected)
    actual_n = normalise(actual)

    distance = levenshtein_distance(expected_n, actual_n)
    if budget is None:
        budget = DEFAULT_LEVENSHTEIN_BUDGET.get(zone_type.upper(), 4)

    return MetricResult(
        expected=expected_n,
        actual=actual_n,
        exact=expected_n == actual_n,
        within_budget=distance <= budget,
        char_accuracy=char_accuracy(expected_n, actual_n),
        distance=distance,
    )
