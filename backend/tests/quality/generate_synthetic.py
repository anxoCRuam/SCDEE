"""
Synthetic OCR fixture generator.

Produces a set of placeholder images for the reliability suite so the
tests run out of the box with no manual dataset assembly. Each image
is generated with PIL, deterministically (fixed seed) so the same run
yields the same bytes — bit-identical regenerations make CI noise
much lower than truly random fixtures would.

The synthetic images **do not** approximate the difficulty of real
hand-written content. They exercise the *plumbing* of the test suite
(image loading, OCR engine selection, metric evaluation, roster
matching). For the actual reliability number that ends up in the
TFG defence, replace the contents of ``dataset/images/`` with real
scans and overwrite ``dataset/ground_truth.csv`` accordingly. See
``dataset/README.md`` for guidance.

Run::

    python -m tests.reliability.ocr.generate_synthetic
"""

from __future__ import annotations

import csv
import random
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ════════════════════════════════════════════════════════════════════
# Sample catalogue
# ════════════════════════════════════════════════════════════════════
#
# Each entry produces one PNG. The ``noise`` flag controls whether
# extra distortion is applied (rotation jitter, salt-and-pepper) —
# good for testing that the OCR engine doesn't fail catastrophically
# on noisy input.


@dataclass(frozen=True)
class _Sample:
    filename: str
    text: str
    zone_type: str  # NIA, DNI, NAME
    style: str  # printed, handwriting_sim
    noise: bool


_SAMPLES: list[_Sample] = [
    _Sample("nia_clean_01.png", "1234567", "NIA", "printed", False),
    _Sample("nia_noisy_01.png", "9876543", "NIA", "handwriting_sim", True),
    _Sample("dni_clean_01.png", "12345678A", "DNI", "printed", False),
    _Sample("dni_noisy_01.png", "87654321Z", "DNI", "handwriting_sim", True),
    _Sample("name_clean_01.png", "MARIA GARCIA", "NAME", "printed", False),
    _Sample("name_noisy_01.png", "JUAN PEREZ LOPEZ", "NAME", "handwriting_sim", True),
]


# ════════════════════════════════════════════════════════════════════
# Image generation
# ════════════════════════════════════════════════════════════════════


_DEFAULT_SIZE = (600, 200)
_RNG_SEED = 1234  # deterministic noise


def _load_font(size: int, *, italic: bool = False) -> ImageFont.FreeTypeFont:
    """Load a system font, falling back to PIL's default on missing fonts."""
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf"
        if italic
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


def _render_text_image(
    text: str,
    *,
    size: tuple[int, int] = _DEFAULT_SIZE,
    handwriting_sim: bool = False,
) -> Image.Image:
    """Draw ``text`` onto a fresh white image.

    ``handwriting_sim`` switches to an italic font and slightly
    randomises baseline / horizontal offsets per character. It is a
    cheap proxy for hand-written input — enough to exercise OCR
    sensitivity without claiming to be realistic.
    """
    width, height = size
    img = Image.new("RGB", size, color="white")
    draw = ImageDraw.Draw(img)

    font = _load_font(size=72, italic=handwriting_sim)

    if not handwriting_sim:
        # Centre the whole string.
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (width - tw) // 2
        y = (height - th) // 2
        draw.text((x, y), text, fill="black", font=font)
        return img

    # "Handwriting" path: draw character by character with jitter.
    rng = random.Random(_RNG_SEED + hash(text) % 100)  # noqa: S311
    cursor_x = 40
    base_y = 60
    for ch in text:
        jitter_y = rng.randint(-6, 6)
        jitter_x = rng.randint(-2, 2)
        # Scale each character slightly differently to mimic an
        # uneven hand.
        ch_font = _load_font(size=rng.randint(58, 76), italic=True)
        draw.text(
            (cursor_x + jitter_x, base_y + jitter_y),
            ch,
            fill=(0, 0, 0),
            font=ch_font,
        )
        bbox = draw.textbbox((0, 0), ch, font=ch_font)
        cursor_x += (bbox[2] - bbox[0]) + rng.randint(2, 8)

    return img


def _add_noise(img: Image.Image) -> Image.Image:
    """Apply mild distortion: slight blur + salt-and-pepper specks."""
    # Slight blur — emulates a low-resolution scan.
    blurred = img.filter(ImageFilter.GaussianBlur(radius=0.6))

    rng = random.Random(_RNG_SEED + 99)  # noqa: S311
    pixels = blurred.load()
    width, height = blurred.size
    speck_count = (width * height) // 400  # ~0.25% of pixels
    for _ in range(speck_count):
        x = rng.randint(0, width - 1)
        y = rng.randint(0, height - 1)
        # 50/50 black/white speck.
        pixels[x, y] = (0, 0, 0) if rng.random() < 0.5 else (255, 255, 255)

    return blurred


# ════════════════════════════════════════════════════════════════════
# Public entry points
# ════════════════════════════════════════════════════════════════════


def regenerate(dataset_dir: Path) -> Path:
    """Wipe and recreate ``dataset/images/`` plus ``ground_truth.csv``.

    Returns the path to the CSV.
    """
    images_dir = dataset_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    # Wipe synthetic images only — real datasets must be kept under a
    # different filename pattern (``real_*``) and are not touched.
    for path in images_dir.glob("*"):
        if path.name.startswith(("nia_", "dni_", "name_")):
            path.unlink()

    rows = []
    for sample in _SAMPLES:
        img = _render_text_image(
            sample.text,
            handwriting_sim=(sample.style == "handwriting_sim"),
        )
        if sample.noise:
            img = _add_noise(img)
        path = images_dir / sample.filename
        img.save(path, format="PNG", optimize=True)
        rows.append(
            {
                "filename": sample.filename,
                "expected_text": sample.text,
                "zone_type": sample.zone_type,
                "style": sample.style,
                "noise": "yes" if sample.noise else "no",
                "source": "synthetic",
            }
        )

    csv_path = dataset_dir / "ground_truth.csv"
    # Preserve any non-synthetic rows already present in the CSV.
    existing_real_rows: list[dict] = []
    if csv_path.exists():
        with open(csv_path, encoding="utf-8") as f:
            existing_real_rows = [
                row for row in csv.DictReader(f) if row.get("source") != "synthetic"
            ]

    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["filename", "expected_text", "zone_type", "style", "noise", "source"],
        )
        writer.writeheader()
        writer.writerows(existing_real_rows + rows)

    return csv_path


if __name__ == "__main__":  # pragma: no cover — run as a script
    here = Path(__file__).resolve().parent
    csv = regenerate(here / "dataset")
    print(f"Wrote {csv}")  # noqa: T201
    print(f"Wrote {len(_SAMPLES)} synthetic images under {here / 'dataset' / 'images'}")  # noqa: T201
