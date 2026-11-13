from __future__ import annotations

import asyncio
from io import BytesIO

import pytest
from PIL import Image

from ark_mcp.artifacts.image_inspection import inspect_image_bytes


def make_image(image_format: str = "PNG", *, transparent: bool = False) -> bytes:
    output = BytesIO()
    mode = "RGBA" if transparent else "RGB"
    color = (0, 0, 255, 0) if transparent else (0, 0, 255)
    with Image.new(mode, (32, 24), color) as image:
        image.save(output, format=image_format)
    return output.getvalue()


@pytest.mark.parametrize("image_format", ["PNG", "JPEG"])
def test_inspection_measures_format_dimensions_and_opaque_pixels(image_format: str) -> None:
    result = inspect_image_bytes(make_image(image_format))
    assert result.status == "verified"
    assert result.width == 32
    assert result.height == 24
    assert result.output_format == image_format.lower()
    assert result.has_transparency is False
    assert result.issues == []


def test_inspection_checks_actual_alpha_not_only_rgba_mode() -> None:
    assert inspect_image_bytes(make_image(transparent=True)).has_transparency is True
    output = BytesIO()
    with Image.new("RGBA", (32, 24), (0, 0, 255, 255)) as image:
        image.save(output, format="PNG")
    assert inspect_image_bytes(output.getvalue()).has_transparency is False


def test_palette_transparency_is_measured() -> None:
    output = BytesIO()
    with Image.new("P", (32, 24), 0) as image:
        image.save(output, format="PNG", transparency=0)
    assert inspect_image_bytes(output.getvalue()).has_transparency is True


@pytest.mark.parametrize("data", [b"", b"not-an-image", make_image()[:45], make_image()[:-20]])
def test_invalid_or_truncated_image_is_reported_without_exception(data: bytes) -> None:
    result = inspect_image_bytes(data)
    assert result.status == "invalid"
    assert result.issues
    assert result.has_transparency is None


def test_inspection_restricts_decoders_to_png_and_jpeg() -> None:
    result = inspect_image_bytes(make_image("GIF"))
    assert result.status == "invalid"


def test_inspection_checks_pixel_bound_before_loading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ark_mcp.artifacts.image_inspection.MAX_IMAGE_PIXELS", 767)
    result = inspect_image_bytes(make_image())
    assert result.status == "invalid"
    assert result.issues == ["pixel_limit_exceeded"]


def test_inspection_checks_byte_bound() -> None:
    result = inspect_image_bytes(b"x" * (10 * 1024 * 1024 + 1))
    assert result.status == "invalid"
    assert result.issues == ["byte_limit_exceeded"]


async def test_concurrent_inspections_do_not_mutate_pillow_limits() -> None:
    original_limit = Image.MAX_IMAGE_PIXELS
    results = await asyncio.gather(
        *(
            asyncio.to_thread(inspect_image_bytes, make_image(transparent=bool(index % 2)))
            for index in range(10)
        )
    )
    assert all(result.status == "verified" for result in results)
    assert [result.has_transparency for result in results] == [
        bool(index % 2) for index in range(10)
    ]
    assert original_limit == Image.MAX_IMAGE_PIXELS
