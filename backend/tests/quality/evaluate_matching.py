"""Offline evaluator for student-to-page matching strategies.

This script bypasses Django entirely. It reads OCR results from the dataset CSV
(`expected` and `actual` columns), reconstructs a synthetic roster + page set,
and runs each (scoring strategy x assigner x threshold) combination, reporting
accuracy and precision.

The same CSV is what `tests/quality/test_ocr_matching.py` would feed through the
full pipeline. By isolating the algorithm we can iterate on scoring/assignment
without running OCR or spinning up Django.
"""

from __future__ import annotations

import csv
import random
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rapidfuzz import fuzz
from scipy.optimize import linear_sum_assignment

REPORTS_DIR = Path(__file__).resolve().parent / "dataset" / "reports"
_REPORT_NAME_RE = re.compile(r"^ocr_raw_(?P<engine>[a-z0-9_]+)_(?P<ts>\d{8}_\d{6})\.csv$")


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RosterStudent:
    """Ground-truth triplet for a convoked student."""

    student_id: str
    name: str
    nia: str
    dni: str


@dataclass(frozen=True)
class OcrPage:
    """A single exam page with its OCR'd fields and the true owner id."""

    true_student_id: str
    ocr_name: str
    ocr_nia: str
    ocr_dni: str


# ---------------------------------------------------------------------------
# Report discovery & CSV loading
# ---------------------------------------------------------------------------


def discover_latest_reports(reports_dir: Path = REPORTS_DIR) -> dict[str, Path]:
    """Return {engine_id: latest_csv_path} for every engine found in reports_dir.

    Matches files named `ocr_raw_{engine}_{YYYYmmdd_HHMMSS}.csv`. Within each
    engine, the most recent timestamp wins.
    """
    if not reports_dir.exists():
        return {}
    latest: dict[str, tuple[str, Path]] = {}
    for path in reports_dir.glob("ocr_raw_*.csv"):
        m = _REPORT_NAME_RE.match(path.name)
        if not m:
            continue
        engine, ts = m.group("engine"), m.group("ts")
        if engine not in latest or ts > latest[engine][0]:
            latest[engine] = (ts, path)
    return {engine: path for engine, (_, path) in latest.items()}


def load_pages_and_roster(csv_path: Path) -> tuple[list[OcrPage], list[RosterStudent]]:
    """Build the roster (from `expected`) and the page set (from `actual`)
    out of one OCR report CSV.

    The CSV is expected to be a per-engine report with at least these columns:
    `filename`, `zone_type`, `expected`, `actual`. Other columns are ignored.
    Filenames follow the convention `{prefix}_{name|nia|dni}{n}.{ext}` where
    `{prefix}_{n}` identifies a unique student (e.g. `alejandro_s_name3` and
    `alejandro_s_nia3` belong to student `alejandro_s#3`).
    """
    with open(csv_path, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    grouped: dict[str, dict[str, tuple[str, str]]] = {}
    for r in rows:
        m = re.match(r"^(.*?)_(name|nia|dni)(\d+)\.", r["filename"])
        if not m:
            continue
        prefix, kind, idx = m.group(1), m.group(2), m.group(3)
        sid = f"{prefix}#{idx}"
        grouped.setdefault(sid, {})[kind] = (r["expected"], r["actual"])

    pages: list[OcrPage] = []
    roster: list[RosterStudent] = []
    for sid, fields in grouped.items():
        if {"name", "nia", "dni"} - set(fields.keys()):
            continue
        roster.append(
            RosterStudent(
                student_id=sid,
                name=fields["name"][0],
                nia=fields["nia"][0],
                dni=fields["dni"][0],
            )
        )
        pages.append(
            OcrPage(
                true_student_id=sid,
                ocr_name=fields["name"][1],
                ocr_nia=fields["nia"][1],
                ocr_dni=fields["dni"][1],
            )
        )
    return pages, roster


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def normalise_name(s: str) -> str:
    """Lowercase, remove accents, drop punctuation, collapse whitespace."""
    s = _strip_accents(s).lower()
    s = re.sub(r"[^a-z\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalise_alnum(s: str) -> str:
    """Uppercase + alphanumeric only (no whitespace, no punctuation)."""
    return re.sub(r"[^A-Z0-9]", "", _strip_accents(s).upper())


# OCR character confusion classes empirically observed in the dataset.
# Characters within a class are treated as equivalent for similarity scoring.
_OCR_CLASSES: list[str] = [
    "O0Q",  # round shapes
    "I1lL|",  # vertical strokes
    "Z2",  # similar zig
    "S5",  # curvy
    "B8",  # double curve
    "G6C",  # open curve with bottom
    "EF",  # comb shapes
    "DP",  # vertical + bump
    "UV",  # bottom curve
    "NM",  # zigzag
    "AH",  # crossbar
    "Y7",  # tail
]


def _build_canonical_map() -> dict[str, str]:
    table: dict[str, str] = {}
    for cls in _OCR_CLASSES:
        canonical = cls[0]
        for ch in cls:
            table[ch.upper()] = canonical
    return table


_CANONICAL_MAP = _build_canonical_map()


def ocr_canonical(s: str) -> str:
    """Map a string into its OCR-equivalence-class canonical form."""
    return "".join(_CANONICAL_MAP.get(c, c) for c in normalise_alnum(s))


# ---------------------------------------------------------------------------
# Similarity primitives
# ---------------------------------------------------------------------------


def _ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return fuzz.ratio(a, b) / 100.0


def sim_levenshtein(a: str, b: str) -> float:
    """Plain normalised Levenshtein on uppercased alphanumerics."""
    return _ratio(normalise_alnum(a), normalise_alnum(b))


def sim_ocr_aware(a: str, b: str) -> float:
    """Levenshtein after applying the OCR confusion class canonicalisation."""
    return _ratio(ocr_canonical(a), ocr_canonical(b))


def sim_name_token_sort(a: str, b: str) -> float:
    """Token sort ratio on accent-stripped, lowercased names.

    Robust to reorderings like 'Pereyra, Pablo' vs 'Pablo Pereyra'.
    """
    return fuzz.token_sort_ratio(normalise_name(a), normalise_name(b)) / 100.0


def sim_name_ocr_token(a: str, b: str) -> float:
    """Token-sort similarity applied to OCR-canonicalised name tokens.

    Best of both worlds for names: handles reordering AND OCR confusion.
    """
    toks_a = sorted(normalise_name(a).split())
    toks_b = sorted(normalise_name(b).split())
    if not toks_a or not toks_b:
        return 0.0

    def canon(toks):
        return " ".join("".join(_CANONICAL_MAP.get(c, c) for c in t.upper()) for t in toks)

    return _ratio(canon(toks_a), canon(toks_b))


def length_match_bonus(a: str, b: str) -> float:
    """[0,1] score based on how close the lengths are after normalisation."""
    la = len(normalise_alnum(a))
    lb = len(normalise_alnum(b))
    if la == 0 or lb == 0:
        return 0.0
    return min(la, lb) / max(la, lb)


# ---------------------------------------------------------------------------
# Scoring strategy: how to combine the three fields into a single score
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScoringStrategy:
    label: str
    name_scorer: Callable[[str, str], float]
    nia_scorer: Callable[[str, str], float]
    dni_scorer: Callable[[str, str], float]
    weights: tuple[float, float, float]  # (w_name, w_nia, w_dni)
    length_bonus: bool = False

    def score(self, page: OcrPage, student: RosterStudent) -> float:
        wn, wi, wd = self.weights
        sn = self.name_scorer(page.ocr_name, student.name)
        si = self.nia_scorer(page.ocr_nia, student.nia)
        sd = self.dni_scorer(page.ocr_dni, student.dni)
        if self.length_bonus:
            si = 0.85 * si + 0.15 * length_match_bonus(page.ocr_nia, student.nia)
            sd = 0.85 * sd + 0.15 * length_match_bonus(page.ocr_dni, student.dni)
        return wn * sn + wi * si + wd * sd


STRATEGIES: list[ScoringStrategy] = [
    # Baseline: replicates assembler.py's current scoring (Levenshtein, 20/50/30)
    ScoringStrategy(
        "baseline (lev, 20/50/30)",
        sim_levenshtein,
        sim_levenshtein,
        sim_levenshtein,
        (0.2, 0.5, 0.3),
    ),
    # OCR-aware with current weights
    ScoringStrategy(
        "ocr-aware (20/50/30)",
        sim_ocr_aware,
        sim_ocr_aware,
        sim_ocr_aware,
        (0.2, 0.5, 0.3),
    ),
    # OCR-aware, balanced weights
    ScoringStrategy(
        "ocr-aware (40/40/20)",
        sim_ocr_aware,
        sim_ocr_aware,
        sim_ocr_aware,
        (0.4, 0.4, 0.2),
    ),
    # Add token-sort for names
    ScoringStrategy(
        "ocr+tokname (40/40/20)",
        sim_name_ocr_token,
        sim_ocr_aware,
        sim_ocr_aware,
        (0.4, 0.4, 0.2),
    ),
    # Token-sort + length bonus on id fields
    ScoringStrategy(
        "ocr+tokname+len (40/40/20)",
        sim_name_ocr_token,
        sim_ocr_aware,
        sim_ocr_aware,
        (0.4, 0.4, 0.2),
        length_bonus=True,
    ),
    # DNI-heavy (DNI is the longest, most distinctive field per the data)
    ScoringStrategy(
        "ocr+tokname (30/30/40)",
        sim_name_ocr_token,
        sim_ocr_aware,
        sim_ocr_aware,
        (0.3, 0.3, 0.4),
    ),
    # Even
    ScoringStrategy(
        "ocr+tokname (33/33/33)",
        sim_name_ocr_token,
        sim_ocr_aware,
        sim_ocr_aware,
        (0.34, 0.33, 0.33),
    ),
    # Name-heavy
    ScoringStrategy(
        "ocr+tokname (50/30/20)",
        sim_name_ocr_token,
        sim_ocr_aware,
        sim_ocr_aware,
        (0.5, 0.3, 0.2),
    ),
    # Name-heavy + length bonus
    ScoringStrategy(
        "ocr+tokname+len (50/30/20)",
        sim_name_ocr_token,
        sim_ocr_aware,
        sim_ocr_aware,
        (0.5, 0.3, 0.2),
        length_bonus=True,
    ),
]


# ---------------------------------------------------------------------------
# Assignment algorithms
# ---------------------------------------------------------------------------


def assign_greedy(
    pages: list[OcrPage],
    roster: list[RosterStudent],
    strategy: ScoringStrategy,
    threshold: float = 0.0,
) -> dict[int, str | None]:
    """Each page picks its own best candidate. No exclusivity guarantee."""
    out: dict[int, str | None] = {}
    for i, page in enumerate(pages):
        best_sid, best_s = None, -1.0
        for stu in roster:
            s = strategy.score(page, stu)
            if s > best_s:
                best_s, best_sid = s, stu.student_id
        out[i] = best_sid if best_s >= threshold else None
    return out


def assign_hungarian(
    pages: list[OcrPage],
    roster: list[RosterStudent],
    strategy: ScoringStrategy,
    threshold: float = 0.0,
) -> dict[int, str | None]:
    """Globally optimal 1-to-1 assignment via the Hungarian algorithm."""
    n_p, n_s = len(pages), len(roster)
    n = max(n_p, n_s)
    # Pad to square; padding rows/cols have 0 score so they get assigned last.
    cost = np.zeros((n, n))
    for i, p in enumerate(pages):
        for j, s in enumerate(roster):
            cost[i, j] = -strategy.score(p, s)
    row_ind, col_ind = linear_sum_assignment(cost)
    out: dict[int, str | None] = dict.fromkeys(range(n_p))
    for i, j in zip(row_ind, col_ind, strict=False):
        if i < n_p and j < n_s:
            sc = -cost[i, j]
            if sc >= threshold:
                out[i] = roster[j].student_id
    return out


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@dataclass
class Result:
    correct: int
    assigned: int
    total: int

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def precision(self) -> float:
        """Of those we assigned, how many were correct."""
        return self.correct / self.assigned if self.assigned else 0.0


def evaluate(
    pages: list[OcrPage],
    roster: list[RosterStudent],
    strategy: ScoringStrategy,
    assigner: Callable,
    threshold: float = 0.0,
) -> Result:
    assignment = assigner(pages, roster, strategy, threshold)
    correct = sum(1 for i, p in enumerate(pages) if assignment.get(i) == p.true_student_id)
    assigned = sum(1 for v in assignment.values() if v is not None)
    return Result(correct=correct, assigned=assigned, total=len(pages))


# ---------------------------------------------------------------------------
# Scenarios (clean / decoys / page-loss)
# ---------------------------------------------------------------------------


def _decoy_students(n: int, seed: int = 42) -> list[RosterStudent]:
    rng = random.Random(seed)
    out = []
    for k in range(n):
        out.append(
            RosterStudent(
                student_id=f"DECOY#{k}",
                name=f"Decoy{k} RandomPerson NoFakeMatch",
                nia=str(rng.randint(100_000, 999_999_999)),
                dni=f"{rng.randint(10_000_000, 99_999_999)}Z",
            )
        )
    return out


def _degrade_pages(pages: list[OcrPage], wipe_prob: float, seed: int = 7) -> list[OcrPage]:
    """Randomly wipe (set to empty) one or more fields per page.

    Simulates catastrophic OCR failures: with `wipe_prob` per field, the field
    is replaced with an empty string, forcing the matcher to rely on the
    remaining fields.
    """
    rng = random.Random(seed)
    out: list[OcrPage] = []
    for p in pages:
        n = p.ocr_name if rng.random() >= wipe_prob else ""
        i = p.ocr_nia if rng.random() >= wipe_prob else ""
        d = p.ocr_dni if rng.random() >= wipe_prob else ""
        out.append(OcrPage(p.true_student_id, n, i, d))
    return out


def _run_grid_for_assigner(pages, roster, assigner_name, assigner) -> list[tuple]:
    """Return all (strategy, threshold) results for a given assigner."""
    out = []
    for strat in STRATEGIES:
        for thr in (0.0, 0.05, 0.15, 0.30):
            r = evaluate(pages, roster, strat, assigner, thr)
            out.append((strat.label, assigner_name, thr, r.accuracy, r.precision, r.assigned))
    return out


def run_grid(pages, roster, label: str) -> None:
    print(f"=== {label}  (pages={len(pages)}, roster={len(roster)}) ===")

    greedy_rows = _run_grid_for_assigner(pages, roster, "greedy", assign_greedy)
    hung_rows = _run_grid_for_assigner(pages, roster, "hungarian", assign_hungarian)

    header = (
        f"{'strategy':<32s}  {'assigner':<10s}  {'thr':>5s}  "
        f"{'acc':>7s}  {'prec':>7s}  {'asg':>4s}"
    )
    print(header)
    print("-" * len(header))

    # Best (acc, prec) per (strategy, assigner) — keep highest acc, break ties by precision
    def best_per_strategy(rows):
        best: dict[tuple[str, str], tuple] = {}
        for row in rows:
            key = (row[0], row[1])
            if key not in best or (row[3], row[4]) > (best[key][3], best[key][4]):
                best[key] = row
        return list(best.values())

    rows = best_per_strategy(greedy_rows) + best_per_strategy(hung_rows)
    # sort: strategy order (preserved), then hungarian last
    rows.sort(key=lambda x: (STRATEGY_INDEX[x[0]], x[1] != "greedy"))
    for label2, assn, thr, acc, prec, asg in rows:
        marker = (
            " <-- BEST"
            if acc == max(r[3] for r in rows) and prec == max(r[4] for r in rows if r[3] == acc)
            else ""
        )
        print(
            f"  {label2:<30s}  {assn:<10s}  {thr:>5.2f}  "
            f"{acc:>6.1%}  {prec:>6.1%}  {asg:>4d}{marker}"
        )
    print()


STRATEGY_INDEX = {s.label: i for i, s in enumerate(STRATEGIES)}


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Evaluate student-to-page matching strategies on OCR reports.",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="Specific report CSV to evaluate. Overrides auto-discovery.",
    )
    parser.add_argument(
        "--engine",
        default=None,
        help="Engine id (e.g. 'easyocr'). Picks its latest report.",
    )
    parser.add_argument(
        "--reports-dir",
        type=Path,
        default=REPORTS_DIR,
        help=f"Directory holding `ocr_raw_*.csv` reports. Default: {REPORTS_DIR}",
    )
    args = parser.parse_args()

    # Resolve which CSV(s) to consume.
    sources: dict[str, Path] = {}
    if args.csv is not None:
        sources[args.csv.stem] = args.csv
    else:
        discovered = discover_latest_reports(args.reports_dir)
        if args.engine is not None:
            if args.engine not in discovered:
                raise SystemExit(
                    f"No report found for engine '{args.engine}' in {args.reports_dir}. "
                    f"Available: {sorted(discovered)}",
                )
            sources[args.engine] = discovered[args.engine]
        elif discovered:
            sources = discovered
        else:
            raise SystemExit(
                f"No OCR reports found in {args.reports_dir}. "
                f"Run `pytest tests/quality/test_ocr_raw.py` first to generate them.",
            )

    for engine, csv_path in sources.items():
        print("\n" + "#" * 78)
        print(f"# Engine: {engine}    Source: {csv_path}")
        print("#" * 78)
        pages, roster = load_pages_and_roster(csv_path)
        print(f"\nLoaded {len(pages)} pages and {len(roster)} roster entries\n")

        run_grid(pages, roster, label="A: clean 1:1")
        run_grid(pages, roster + _decoy_students(30), label="B: roster +30 absentees")
        run_grid(pages, roster + _decoy_students(100), label="C: roster +100 absentees")

        pages_d20 = _degrade_pages(pages, wipe_prob=0.20)
        run_grid(pages_d20, roster, label="D: 20% per-field OCR wipe")
        run_grid(
            pages_d20, roster + _decoy_students(30), label="E: 20% per-field wipe + 30 absentees"
        )

        pages_d40 = _degrade_pages(pages, wipe_prob=0.40)
        run_grid(pages_d40, roster, label="F: 40% per-field OCR wipe (severe)")


if __name__ == "__main__":
    main()
