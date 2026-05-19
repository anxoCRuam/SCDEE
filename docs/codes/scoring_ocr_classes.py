_OCR_CONFUSION_CLASSES: list[str] = [
    "O0Q",  # formas redondeadas
    "I1lL|",  # trazos verticales
    "Z2",
    "S5",
    "B8",
    "G6C",
    "EF",
    "DP",
    "UV",
    "NM",
    "AH",
    "Y7",
]

_OCR_CANONICAL_MAP: dict[str, str] = {
    ch.upper(): cls[0] for cls in _OCR_CONFUSION_CLASSES for ch in cls
}


def ocr_canonical(s: str) -> str:
    """Forma canonica OCR-aware. Dos cadenas que solo difieren por
    confusiones comunes (p.ej. '0' vs 'O') colapsan a la misma forma."""
    return "".join(_OCR_CANONICAL_MAP.get(c, c) for c in normalise_alnum(s))


def similarity_ocr_aware(a: str, b: str) -> float:
    """Levenshtein normalizada en [0,1] sobre la forma canonica OCR."""
    return fuzz.ratio(ocr_canonical(a), ocr_canonical(b)) / 100.0
