"""Contract tests for the Seedream edit-image tool.

Tests prompt construction, coordinate validation, and the input model's
validation rules. The handler delegates to SeedreamService which is
already covered by the adapter contract tests.
"""

from __future__ import annotations

import pytest

from ark_mcp.domain.media import MediaSource, MediaSourceKind
from ark_mcp.tools._seedream_shared import EditBbox, EditCoordinate, edit_prompt
from ark_mcp.tools.seedream_generate_image import (
    SeedreamGenerateInput,
)

_IMG = MediaSource(kind=MediaSourceKind.url, url="https://example.com/a.png")


class TestEditCoordinate:
    def test_valid_point(self) -> None:
        coord = EditCoordinate(x=500, y=300)
        assert coord.x == 500
        assert coord.y == 300

    def test_point_below_zero_rejected(self) -> None:
        with pytest.raises(ValueError):
            EditCoordinate(x=-1, y=0)

    def test_point_above_999_rejected(self) -> None:
        with pytest.raises(ValueError):
            EditCoordinate(x=0, y=1000)

    def test_point_boundary_zero(self) -> None:
        coord = EditCoordinate(x=0, y=0)
        assert coord.x == 0
        assert coord.y == 0

    def test_point_boundary_max(self) -> None:
        coord = EditCoordinate(x=999, y=999)
        assert coord.x == 999
        assert coord.y == 999


class TestEditBbox:
    def test_valid_bbox(self) -> None:
        bbox = EditBbox(x1=100, y1=200, x2=800, y2=600)
        assert bbox.x1 == 100
        assert bbox.y1 == 200
        assert bbox.x2 == 800
        assert bbox.y2 == 600

    def test_bbox_x1_gt_x2_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"x1.*must not exceed x2"):
            EditBbox(x1=500, y1=0, x2=100, y2=999)

    def test_bbox_y1_gt_y2_rejected(self) -> None:
        with pytest.raises(ValueError, match=r"y1.*must not exceed y2"):
            EditBbox(x1=0, y1=800, x2=999, y2=100)

    def test_bbox_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError):
            EditBbox(x1=0, y1=0, x2=1000, y2=500)

    def test_bbox_degenerate_zero_area(self) -> None:
        bbox = EditBbox(x1=100, y1=100, x2=100, y2=100)
        assert bbox.x1 == bbox.x2
        assert bbox.y1 == bbox.y2


class TestSeedreamEditInput:
    def _ref_image(self) -> MediaSource:
        return MediaSource(kind=MediaSourceKind.url, url="https://example.com/img.png")

    def test_point_edit_minimal_valid(self) -> None:
        inp = SeedreamGenerateInput(
            prompt="replace with a crown",
            images=[self._ref_image()],
            point=EditCoordinate(x=520, y=460),
        )
        assert inp.point.x == 520
        assert inp.point.y == 460

    def test_bbox_edit_minimal_valid(self) -> None:
        inp = SeedreamGenerateInput(
            prompt="replace with a garden",
            images=[self._ref_image()],
            bbox=EditBbox(x1=120, y1=180, x2=640, y2=760),
        )
        assert inp.bbox.x1 == 120
        assert inp.bbox.y1 == 180

    def test_point_and_bbox_together_valid(self) -> None:
        inp = SeedreamGenerateInput(
            prompt="cross-image edit",
            images=[self._ref_image(), self._ref_image()],
            point=EditCoordinate(x=50, y=50),
            bbox=EditBbox(x1=179, y1=283, x2=796, y2=986),
        )
        assert inp.point is not None
        assert inp.bbox is not None

    def test_images_without_coordinates_are_plain_image_to_image(self) -> None:
        inp = SeedreamGenerateInput(prompt="make it blue", images=[_IMG])
        assert inp.point is None and inp.bbox is None

    def test_coordinates_without_images_rejected(self) -> None:
        with pytest.raises(ValueError, match="provide images"):
            SeedreamGenerateInput(prompt="edit", point=EditCoordinate(x=1, y=1))

    def test_no_images_rejected(self) -> None:
        with pytest.raises(ValueError):
            SeedreamGenerateInput(
                prompt="edit something",
                images=[],
                point=EditCoordinate(x=500, y=500),
            )

    def test_empty_prompt_rejected(self) -> None:
        with pytest.raises(ValueError):
            SeedreamGenerateInput(
                prompt="",
                images=[self._ref_image()],
                point=EditCoordinate(x=500, y=500),
            )

    def test_prompt_too_long_rejected(self) -> None:
        with pytest.raises(ValueError):
            SeedreamGenerateInput(
                prompt="x" * 4001,
                images=[self._ref_image()],
                point=EditCoordinate(x=500, y=500),
            )


class TestBuildEditPrompt:
    def test_point_prompt(self) -> None:
        result = edit_prompt(
            instruction="Replace the object with a crown.",
            point=EditCoordinate(x=520, y=460),
            bbox=None,
        )
        assert "Image 1<point>520 460</point>" in result
        assert result.endswith("Replace the object with a crown.")

    def test_bbox_prompt(self) -> None:
        result = edit_prompt(
            instruction="Replace with a garden.",
            point=None,
            bbox=EditBbox(x1=120, y1=180, x2=640, y2=760),
        )
        assert "Image 1<bbox>120 180 640 760</bbox>" in result
        assert result.endswith("Replace with a garden.")

    def test_point_and_bbox_prompt(self) -> None:
        result = edit_prompt(
            instruction="Replace the object with a crown.",
            point=EditCoordinate(x=50, y=50),
            bbox=EditBbox(x1=179, y1=283, x2=796, y2=986),
        )
        assert "Image 1<bbox>179 283 796 986</bbox>" in result
        assert "Image 1<point>50 50</point>" in result
        assert result.endswith("Replace the object with a crown.")

    def test_bbox_before_point_in_prompt(self) -> None:
        result = edit_prompt(
            instruction="edit",
            point=EditCoordinate(x=100, y=200),
            bbox=EditBbox(x1=10, y1=20, x2=30, y2=40),
        )
        bbox_pos = result.index("<bbox>")
        point_pos = result.index("<point>")
        assert bbox_pos < point_pos
