"""
Student matching — fuzzy matching of OCR results against convoked students.

Uses Levenshtein distance for name matching and OCR-error-tolerant
comparison for DNI/NIA (handles common confusions: 0/O, 1/l/I, etc.).

References: RF-9.7
"""

from __future__ import annotations

import logging
import re

from django.conf import settings

logger = logging.getLogger(__name__)

# Minimum confidence threshold for accepting a match.
MATCH_CONFIDENCE_THRESHOLD = getattr(settings, "INGESTION_MATCH_THRESHOLD", 0.7)

# Common OCR character confusions for DNI/NIA.
_OCR_CONFUSIONS = {
    "0": "O",
    "O": "0",
    "1": "lI",
    "l": "1I",
    "I": "1l",
    "5": "S",
    "S": "5",
    "8": "B",
    "B": "8",
    "6": "G",
    "G": "6",
}


def match_student(
    exam,
    attributes: dict[str, str],
) -> tuple | None:
    """Match OCR attributes against convoked students.

    Tries matching in order of reliability:
    1. NIA (exact-ish, most reliable).
    2. DNI (exact-ish, with OCR tolerance).
    3. Name (fuzzy Levenshtein).

    Args:
        exam: The Exam instance.
        attributes: Dict of attribute name → OCR-recognized value.

    Returns:
        Tuple of (user, confidence) or (None, 0.0).
    """
    from apps.accounts.services.user_service import get_decrypted_dni
    from apps.exams.models.exams import ExamConvocation

    convocations = ExamConvocation.objects.filter(exam=exam).select_related("student")

    students = [c.student for c in convocations]

    if not students:
        return (None, 0.0)

    # Try NIA match first (most reliable).
    nia = attributes.get("nia", "").strip()
    if nia:
        for student in students:
            if student.nia and _ocr_tolerant_match(nia, student.nia):
                return (student, 0.95)

    # Try DNI match.
    dni = attributes.get("dni", "").strip()
    if dni:
        for student in students:
            decrypted_dni = get_decrypted_dni(student)
            if decrypted_dni and _ocr_tolerant_match(dni, decrypted_dni):
                return (student, 0.90)

    # Try name match (fuzzy).
    name = attributes.get("name", "").strip()
    if name:
        best_match = None
        best_score = 0.0

        for student in students:
            full_name = f"{student.first_name} {student.last_name}"
            score = _name_similarity(name, full_name)
            if score > best_score:
                best_score = score
                best_match = student

        if best_match and best_score >= MATCH_CONFIDENCE_THRESHOLD:
            return (best_match, best_score)

    return (None, 0.0)


def _ocr_tolerant_match(ocr_text: str, expected: str) -> bool:
    """Match strings with tolerance for common OCR errors.

    Normalizes both strings and checks if they match after
    applying OCR confusion substitutions.
    """
    ocr_clean = re.sub(r"\s+", "", ocr_text.upper())
    expected_clean = re.sub(r"\s+", "", expected.upper())

    if ocr_clean == expected_clean:
        return True

    # Try substituting common confusions.
    if len(ocr_clean) != len(expected_clean):
        return False

    mismatches = 0
    for a, b in zip(ocr_clean, expected_clean, strict=True):
        if a != b:
            # Check if this is a known OCR confusion.
            confusions = _OCR_CONFUSIONS.get(a, "")
            if b not in confusions:
                mismatches += 1

    # Allow up to 1 non-confusion mismatch for short strings,
    # 2 for longer strings.
    max_mismatches = 1 if len(expected_clean) < 10 else 2
    return mismatches <= max_mismatches


def _name_similarity(ocr_name: str, expected_name: str) -> float:
    """Compute similarity between two names using Levenshtein distance.

    Returns a score between 0.0 and 1.0.
    """
    a = ocr_name.lower().strip()
    b = expected_name.lower().strip()

    if not a or not b:
        return 0.0

    if a == b:
        return 1.0

    # Simple Levenshtein distance implementation.
    distance = _levenshtein_distance(a, b)
    max_len = max(len(a), len(b))

    return 1.0 - (distance / max_len)


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Compute Levenshtein edit distance between two strings."""
    if len(s1) < len(s2):
        return _levenshtein_distance(s2, s1)

    if len(s2) == 0:
        return len(s1)

    prev_row = list(range(len(s2) + 1))

    for i, c1 in enumerate(s1):
        curr_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = prev_row[j + 1] + 1
            deletions = curr_row[j] + 1
            substitutions = prev_row[j] + (c1 != c2)
            curr_row.append(min(insertions, deletions, substitutions))
        prev_row = curr_row

    return prev_row[-1]
