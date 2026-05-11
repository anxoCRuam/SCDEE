"""
Security utilities: watermark

- WatermarkService: applies dynamic watermark to page images (RF-16.6)

References: RF-16.6, RF-16.7, RNF-6
"""

import io
import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# ── Tipos de watermark disponibles ───────────────────────────
STYLE_GRID = "grid"
STYLE_DIAGONAL = "diagonal"
STYLE_CENTER_STAMP = "center_stamp"
STYLE_BOTTOM_RIGHT = "bottom_right"
STYLE_CROSS = "cross"


class WatermarkService:
    """Apply dynamic watermark to page images served to students.

    Supports multiple visual styles so the operator can pick the one that
    balances deterrence and readability best.

    Usage:
        WatermarkService.apply_watermark(image_bytes, name, nia, style='grid')
    """

    @staticmethod
    def apply_watermark(
        image_bytes: bytes,
        student_name: str,
        student_nia: str,
        style: str = STYLE_GRID,
    ) -> bytes:
        """Public entry point – delegates to the chosen style."""
        try:
            from PIL import Image

            image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")

            if style == STYLE_GRID:
                return _watermark_grid(image, student_name, student_nia)
            if style == STYLE_DIAGONAL:
                return _watermark_diagonal(image, student_name, student_nia)
            if style == STYLE_CENTER_STAMP:
                return _watermark_center_stamp(image, student_name, student_nia)
            if style == STYLE_BOTTOM_RIGHT:
                return _watermark_bottom_right(image, student_name, student_nia)
            if style == STYLE_CROSS:
                return _watermark_cross(image, student_name, student_nia)
            # fallback al grid
            return _watermark_grid(image, student_name, student_nia)
        except Exception as exc:
            logger.warning("Watermark failed, returning original: %s", exc)
            return image_bytes


# ── Helpers ──────────────────────────────────────────────────
def _text_label(name: str, nia: str) -> str:
    return f"{name} | {nia} | {datetime.now(UTC).strftime('%Y-%m-%d')}"


def _get_font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    except OSError:
        return ImageFont.load_default()


def _save_result(image: "Image.Image") -> bytes:  # noqa: F821
    result = image.convert("RGB")
    out = io.BytesIO()
    result.save(out, format="PNG")
    return out.getvalue()


# ── Estilos ──────────────────────────────────────────────────


def _watermark_grid(image, student_name, student_nia):
    """Repeat text in a regular grid (current production style)."""
    from PIL import Image, ImageDraw

    text = _text_label(student_name, student_nia)
    font = _get_font(28)
    draw = ImageDraw.Draw(image)
    opacity = 100

    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x_step = tw + 80
    y_step = max(int(th * 2.5), 120)
    w, h = image.size

    # Draw directly on image with alpha (use a transparent layer)
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)

    for y in range(-th, h + th, y_step):
        for x in range(-tw, w + tw, x_step):
            overlay_draw.text((x, y), text, fill=(128, 128, 128, opacity), font=font)

    result = Image.alpha_composite(image, overlay)
    return _save_result(result)


def _watermark_diagonal(image, student_name, student_nia):
    """Single line of text repeated diagonally across the page."""
    import math

    from PIL import Image, ImageDraw

    text = _text_label(student_name, student_nia)
    font = _get_font(36)
    draw = ImageDraw.Draw(image)

    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]  # noqa: F841
    w, h = image.size
    step = tw + 200

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)

    # Diagonal angle ~30°
    angle = math.radians(30)
    dx = step * math.cos(angle)
    dy = step * math.sin(angle)  # noqa: F841

    start_x = -w
    while start_x < w + h:
        overlay_draw.text((start_x, 50), text, fill=(128, 128, 128, 50), font=font)
        start_x += dx
        # move vertically as well to cover page
        # simple version: just one diagonal line; you can add more lines shifting Y
        # but I'll do a parallel repetition
    # better: loop over several Y offsets
    for offset_y in range(0, h, 300):
        x = -w
        while x < w + 2 * h:
            overlay_draw.text((x, offset_y), text, fill=(128, 128, 128, 40), font=font)
            x += step

    result = Image.alpha_composite(image, overlay)
    return _save_result(result)


def _watermark_center_stamp(image, student_name, student_nia):
    """Big semi-transparent text stamp in the center."""
    from PIL import Image, ImageDraw

    text = _text_label(student_name, student_nia)
    font = _get_font(48)
    draw = ImageDraw.Draw(image)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    w, h = image.size

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.text(
        ((w - tw) // 2, (h - 60) // 2),
        text,
        fill=(255, 0, 0, 30),  # reddish tint
        font=font,
    )
    result = Image.alpha_composite(image, overlay)
    return _save_result(result)


def _watermark_bottom_right(image, student_name, student_nia):
    """Small text only in the bottom-right corner."""
    from PIL import Image, ImageDraw

    text = _text_label(student_name, student_nia)
    font = _get_font(20)
    draw = ImageDraw.Draw(image)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    w, h = image.size

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.text(
        (w - tw - 20, h - th - 20),
        text,
        fill=(128, 128, 128, 80),
        font=font,
    )
    result = Image.alpha_composite(image, overlay)
    return _save_result(result)


def _watermark_cross(image, student_name, student_nia):
    """Two crossing diagonal stripes."""

    from PIL import Image, ImageDraw

    text = _text_label(student_name, student_nia)
    font = _get_font(32)
    font_small = _get_font(24)  # noqa: F841
    draw = ImageDraw.Draw(image)  # noqa: F841

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)

    w, h = image.size
    # Diagonals: from top-left to bottom-right and top-right to bottom-left
    steps = 5
    for i in range(steps):
        # diagonal 1
        x = w * i / steps
        overlay_draw.text((x, h * i / steps), text, fill=(0, 0, 255, 30), font=font)
        # diagonal 2
        overlay_draw.text((w - x, h * i / steps), text, fill=(0, 0, 255, 30), font=font)
    result = Image.alpha_composite(image, overlay)
    return _save_result(result)
