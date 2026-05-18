"""Pure assignment algorithm: lots ↔ students.

Given a set of lots (each one a group of pages from the same physical
exam) and a roster of candidate students, returns a globally optimal
1-to-1 assignment using the Hungarian algorithm
(`scipy.optimize.linear_sum_assignment`).

The 1-to-1 guarantee is what closes the loop in the matching: greedy
"pick the best student for each lot independently" can assign the same
student to multiple lots, with no principled way to resolve conflicts.
The Hungarian assignment maximises the total score subject to the
constraint that no student receives more than one lot and no lot is
assigned to more than one student. Lots with no candidate above the
configured threshold are returned as unassigned.

No Django imports here — operates on plain dataclasses so that the
offline evaluator (`tests/quality/evaluate_matching.py`) can reuse
exactly the same code path that production runs.
"""

from __future__ import annotations

from collections.abc import Hashable
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linear_sum_assignment

from apps.ingestion.services.scoring import ScoringStrategy

# ---------------------------------------------------------------------------
# Input types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LotCandidate:
    """OCR'd values for one lot (group of pages from a single physical exam).

    Attributes:
        key: Stable identifier for this lot (e.g. a batch UUID or a
            synthetic "single_{page_id}" string for unbatched pages).
            Must be hashable; the assignment result is keyed by it.
        ocr_values: Per-attribute lists of OCR'd strings. Expected keys
            are 'name', 'nia', 'dni'. A multi-page lot will typically
            have one entry per page per attribute.
    """

    key: Hashable
    ocr_values: dict[str, list[str]]


@dataclass(frozen=True)
class StudentCandidate:
    """The ground-truth identifier triplet for one convoked student."""

    student_id: Hashable
    name: str
    nia: str
    dni: str


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LotAssignment:
    """Result of assigning a single lot.

    Attributes:
        student_id: The matched student's id, or None if no candidate
            scored above the configured threshold.
        score: The achieved score (0.0 if unassigned).
    """

    student_id: Hashable | None
    score: float


# ---------------------------------------------------------------------------
# Algorithm
# ---------------------------------------------------------------------------


def assign_lots_to_students(
    lots: list[LotCandidate],
    students: list[StudentCandidate],
    strategy: ScoringStrategy,
    threshold: float = 0.0,
) -> dict[Hashable, LotAssignment]:
    """Compute the globally optimal 1-to-1 assignment of lots to students.

    Uses the Hungarian algorithm to maximise the total score across the
    assignment subject to the 1-to-1 constraint. Lots whose best-matched
    student scores below `threshold` are returned as unassigned
    (student_id=None, score=0.0).

    Args:
        lots: lots to be assigned. Each lot's `key` must be hashable.
        students: roster of candidate students.
        strategy: scoring strategy that computes (lot, student) -> [0, 1].
        threshold: minimum score to accept an assignment. Pairs below
            this score are dropped.

    Returns:
        Mapping from `lot.key` to its `LotAssignment`. Every lot in
        `lots` appears as a key in the returned dict.
    """
    if not lots:
        return {}
    if not students:
        return {lot.key: LotAssignment(student_id=None, score=0.0) for lot in lots}

    cost_matrix = _build_cost_matrix(lots, students, strategy)
    row_ind, col_ind = linear_sum_assignment(cost_matrix)

    n_lots, n_students = len(lots), len(students)
    out: dict[Hashable, LotAssignment] = {
        lot.key: LotAssignment(student_id=None, score=0.0) for lot in lots
    }
    for i, j in zip(row_ind, col_ind, strict=False):
        if i >= n_lots or j >= n_students:
            # Padding row/column from the square matrix: ignore.
            continue
        score = float(-cost_matrix[i, j])
        if score >= threshold:
            out[lots[i].key] = LotAssignment(
                student_id=students[j].student_id,
                score=score,
            )
    return out


def _build_cost_matrix(
    lots: list[LotCandidate],
    students: list[StudentCandidate],
    strategy: ScoringStrategy,
) -> np.ndarray:
    """Build a square cost matrix for `linear_sum_assignment`.

    The Hungarian algorithm minimises cost, so we negate the scores.
    The matrix is padded to a square with zeros: padding cells (cost=0)
    are only chosen when no real cell is available (real costs are in
    [-1, 0]), which is exactly the rectangular-case behaviour we want.
    """
    n = max(len(lots), len(students))
    cost = np.zeros((n, n))
    for i, lot in enumerate(lots):
        for j, student in enumerate(students):
            score = strategy.score_lot(
                ocr_values=lot.ocr_values,
                true_name=student.name,
                true_nia=student.nia,
                true_dni=student.dni,
            )
            cost[i, j] = -score
    return cost
