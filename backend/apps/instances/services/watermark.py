"""
Security utilities: watermark

- WatermarkService: applies dynamic watermark to page images (RF-16.6)

References: RF-16.6, RF-16.7, RNF-6
"""

import io
import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


class WatermarkService:
    """Apply dynamic watermark to page images served to students.

    The watermark includes the student's name and NIA, making
    redistribution traceable. Applied at serving time, never
    modifying the original stored image.
    """

    @staticmethod
    def apply_watermark(
        image_bytes: bytes,
        student_name: str,
        student_nia: str,
    ) -> bytes:
        """Overlay a translucent text watermark on a page image.

        Args:
            image_bytes: Original PNG/JPEG bytes.
            student_name: Student's full name for the watermark.
            student_nia: Student's NIA for the watermark.

        Returns:
            Image bytes with watermark applied.
        """
        try:
            from PIL import Image, ImageDraw, ImageFont

            image = Image.open(io.BytesIO(image_bytes)).convert("RGBA")
            watermark = Image.new("RGBA", image.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(watermark)

            # Build watermark text.
            text = f"{student_name} | {student_nia} | {datetime.now(UTC).strftime('%Y-%m-%d')}"

            # Try to use a default font; fall back to default if not available.
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
            except OSError:
                font = ImageFont.load_default()

            # Draw text diagonally across the image.
            img_w, img_h = image.size
            # Repeat the watermark text in a grid pattern.
            for y_offset in range(0, img_h, 200):
                for x_offset in range(-img_w, img_w, 400):
                    draw.text(
                        (x_offset, y_offset),
                        text,
                        fill=(128, 128, 128, 40),  # Semi-transparent gray
                        font=font,
                    )

            # Composite watermark onto image.
            result = Image.alpha_composite(image, watermark)
            result = result.convert("RGB")

            output = io.BytesIO()
            result.save(output, format="PNG")
            return output.getvalue()

        except Exception as exc:
            logger.warning("Watermark failed, returning original: %s", exc)
            return image_bytes
