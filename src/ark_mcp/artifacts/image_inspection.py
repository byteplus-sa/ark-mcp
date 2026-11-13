"""Bounded PNG/JPEG byte inspection without changing decoder-global policy."""

from __future__ import annotations

from io import BytesIO
from typing import cast

from PIL import Image, UnidentifiedImageError

from ark_mcp.domain.models import SeedreamImageInspection
from ark_mcp.security.media_policy import get_media_limits

MAX_IMAGE_PIXELS = 36_000_000


def inspect_image_bytes(data: bytes) -> SeedreamImageInspection:
    """Measure valid static image bytes; return safe issues for invalid input."""
    if len(data) > get_media_limits().image_max_bytes:
        return SeedreamImageInspection(status="invalid", issues=["byte_limit_exceeded"])
    if not data.startswith((b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff")):
        return SeedreamImageInspection(status="invalid", issues=["invalid_image"])
    try:
        with Image.open(BytesIO(data), formats=("PNG", "JPEG")) as image:
            width, height = image.size
            if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                return SeedreamImageInspection(status="invalid", issues=["pixel_limit_exceeded"])
            if getattr(image, "n_frames", 1) != 1:
                return SeedreamImageInspection(status="invalid", issues=["animated_image"])
            image_format = (image.format or "").lower()
            image.verify()
        with Image.open(BytesIO(data), formats=("PNG", "JPEG")) as image:
            image.load()
            has_transparency = False
            if "A" in image.getbands() or "transparency" in image.info:
                with image.convert("RGBA") as rgba, rgba.getchannel("A") as alpha:
                    has_transparency = cast("tuple[int, int]", alpha.getextrema())[0] < 255
        return SeedreamImageInspection(
            status="verified",
            width=width,
            height=height,
            output_format=image_format,
            has_transparency=has_transparency,
        )
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError):
        return SeedreamImageInspection(status="invalid", issues=["invalid_image"])
