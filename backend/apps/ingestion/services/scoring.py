"""Pure scoring functions for the student-to-page matching algorithm.

This module operates on plain strings and dataclasses; no Django imports.
Keep it that way — it makes the matching algorithm trivially unit-testable
in isolation and lets the offline evaluator (`tests/quality/evaluate_matching.py`)
reuse exactly the same code path that production runs.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from rapidfuzz import fuzz

# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _strip_accents(s: str) -> str:
    """Remove diacritical marks: 'á' becomes 'a', 'ñ' becomes 'n'."""
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalise_name(s: str) -> str:
    """Lowercase, drop accents and punctuation, collapse whitespace."""
    s = _strip_accents(s).lower()
    s = re.sub(r"[^a-z\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalise_alnum(s: str) -> str:
    """Uppercase + alphanumeric only, no whitespace, no punctuation."""
    return re.sub(r"[^A-Z0-9]", "", _strip_accents(s).upper())


# ---------------------------------------------------------------------------
# OCR confusion classes
# ---------------------------------------------------------------------------
#
# Empirically observed character confusions in EasyOCR on handwritten input.
# Each entry groups characters that the engine frequently swaps for each
# other. Within a class, characters are folded to the class representative
# (the first one) so that substituting one for another costs zero in the
# Levenshtein distance underlying our similarity scoring.
#
# Adding a class trades discrimination for recall: if two confusable
# characters are very rarely both present in the same roster's identifier
# space, the gain on misreads outweighs the rare loss on legitimate
# discrimination. Adjust based on the corpus you observe in production.
#
_OCR_CONFUSION_CLASSES: list[str] = [
    "O0Q",  # round shapes
    "I1lL|",  # vertical strokes
    "Z2",  # zig
    "S5",  # curvy
    "B8",  # double curve
    "G6C",  # open-bottom curve
    "EF",  # comb shapes
    "DP",  # vertical + bump
    "UV",  # bottom curve
    "NM",  # zigzag
    "AH",  # crossbar
    "Y7",  # tail
]


def _build_canonical_map() -> dict[str, str]:
    """Build {char -> class_representative} from `_OCR_CONFUSION_CLASSES`."""
    table: dict[str, str] = {}
    for cls in _OCR_CONFUSION_CLASSES:
        canonical = cls[0]
        for ch in cls:
            table[ch.upper()] = canonical
    return table


_OCR_CANONICAL_MAP = _build_canonical_map()


def ocr_canonical(s: str) -> str:
    """Project a string into its OCR-equivalence canonical form.

    All confusion-class characters are folded to the class representative
    after `normalise_alnum`. Two strings that differ only by common OCR
    mistakes (e.g. '0' vs 'O', 'l' vs '1') will become identical.
    """
    return "".join(_OCR_CANONICAL_MAP.get(c, c) for c in normalise_alnum(s))


# ---------------------------------------------------------------------------
# Similarity primitives
# ---------------------------------------------------------------------------


def _levenshtein_ratio(a: str, b: str) -> float:
    """Normalised Levenshtein similarity in [0, 1]. Backed by rapidfuzz."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return fuzz.ratio(a, b) / 100.0


def similarity_ocr_aware(a: str, b: str) -> float:
    """Similarity that treats common OCR confusions as zero-cost edits.

    This is the recommended scorer for every identifier field (name, NIA,
    DNI). See `_OCR_CONFUSION_CLASSES` for the equivalence groups.
    """
    return _levenshtein_ratio(ocr_canonical(a), ocr_canonical(b))


# ---------------------------------------------------------------------------
# Scoring strategy
# ---------------------------------------------------------------------------

# Per-attribute weights. Order: (name, nia, dni). The default 20/50/30 is
# the winner of the offline grid search (see tests/quality/evaluate_matching.py).
# NIA-heavy weighting is robust because NIA is the shortest, most reliable
# field for OCR on handwriting (digits only, no diacritics, no spaces).
DEFAULT_WEIGHTS: tuple[float, float, float] = (0.20, 0.50, 0.30)


@dataclass(frozen=True)
class ScoringStrategy:
    """How to combine per-attribute similarities into a single match score.

    A strategy picks one similarity function per attribute (name / nia /
    dni) and a weight per attribute. The final score is the weighted sum
    of per-attribute similarities, each computed by taking the MAXIMUM
    similarity across all OCR'd values for that attribute (multi-page lots
    may have several values for the same attribute, one per page).
    """

    name_scorer: Callable[[str, str], float]
    nia_scorer: Callable[[str, str], float]
    dni_scorer: Callable[[str, str], float]
    weights: tuple[float, float, float] = DEFAULT_WEIGHTS

    def score_lot(
        self,
        ocr_values: dict[str, list[str]],
        true_name: str,
        true_nia: str,
        true_dni: str,
    ) -> float:
        """Score how compatible a multi-page lot is with one student.

        Args:
            ocr_values: per-attribute lists of OCR'd strings. Expected keys
                are 'name', 'nia', 'dni'. Missing keys are treated as empty
                lists.
            true_name: the candidate student's full name.
            true_nia: the candidate student's NIA.
            true_dni: the candidate student's decrypted DNI.

        Returns:
            Score in [0, 1]. Higher means more compatible.
        """
        wn, wi, wd = self.weights
        sn = _max_similarity(ocr_values.get("name", []), true_name, self.name_scorer)
        si = _max_similarity(ocr_values.get("nia", []), true_nia, self.nia_scorer)
        sd = _max_similarity(ocr_values.get("dni", []), true_dni, self.dni_scorer)
        return wn * sn + wi * si + wd * sd


def _max_similarity(
    candidates: list[str],
    truth: str,
    scorer: Callable[[str, str], float],
) -> float:
    """Return the maximum similarity across `candidates` against `truth`.

    If either side has no usable value, returns 0.0 (rather than
    propagating a misleading 1.0 from two empty strings).
    """
    if not candidates or not truth:
        return 0.0
    return max(scorer(candidate, truth) for candidate in candidates)


# The strategy used by the production assembler by default.
DEFAULT_STRATEGY = ScoringStrategy(
    name_scorer=similarity_ocr_aware,
    nia_scorer=similarity_ocr_aware,
    dni_scorer=similarity_ocr_aware,
    weights=DEFAULT_WEIGHTS,
)
