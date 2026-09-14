"""Integration tests for the ``seedream_edit_image`` tool handler.

Exercises the full path: input validation → prompt construction →
provider call (mocked) → artifact persistence → structured output.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.tools import ToolResult

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.domain.media import MediaSource, MediaSourceKind
from ark_mcp.providers.modelark.schemas import SeedreamProviderResponse
from ark_mcp.providers.modelark.seedream import SeedreamService
from ark_mcp.security.safe_downloader import DownloadedMedia
from ark_mcp.tools.seedream_edit_image import (
    EditCoordinate,
    SeedreamEditInput,
    SeedreamEditOutput,
    seedream_edit_image,
)
from tests.fixtures.fake_context import FakeContext


def _ref_image() -> MediaSource:
    return MediaSource(kind=MediaSourceKind.url, url="https://example.com/img.png")


def _patch_seedream_service(monkeypatch: pytest.MonkeyPatch, response_data: dict[str, Any]) -> None:
    async def mock_generate(
        self: SeedreamService, request: Any
    ) -> tuple[SeedreamProviderResponse, str | None]:
        response = SeedreamProviderResponse.model_validate(response_data)
        return response, "req-edit-123"

    monkeypatch.setattr(SeedreamService, "generate", mock_generate)


class TestSeedreamEditImageTool:
    async def test_prompt_optimization_defaults_to_fast(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: list[Any] = []

        async def mock_generate(
            self: SeedreamService, request: Any
        ) -> tuple[SeedreamProviderResponse, str | None]:
            captured.append(request)
            response = SeedreamProviderResponse.model_validate(
                {"created": 1721400000, "data": [{"b64_json": "aV9hbWFnZQ=="}]}
            )
            return response, "req-edit-123"

        monkeypatch.setattr(SeedreamService, "generate", mock_generate)

        await seedream_edit_image(
            SeedreamEditInput(
                prompt="Replace the object with a crown.",
                images=[_ref_image()],
                point=EditCoordinate(x=520, y=460),
                persist=False,
            ),
            fake_ctx,
        )

        assert captured[0].optimize_prompt_options == {"mode": "fast"}

    async def test_point_edit_with_url_persistence(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        temp_store: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_seedream_service(
            monkeypatch,
            {
                "created": 1721400000,
                "data": [{"url": "https://tos-ap-southeast.bytepluses.com/edited.png"}],
                "usage": {"prompt_tokens": 10, "total_tokens": 20},
            },
        )

        with (
            patch("ark_mcp.artifacts.registry.get_artifact_store", return_value=temp_store),
            patch(
                "ark_mcp.security.safe_downloader.SafeDownloader.download",
                new=AsyncMock(
                    return_value=DownloadedMedia(
                        body=b"fake-png-data",
                        content_type="image/png",
                        final_url="https://tos-ap-southeast.bytepluses.com/edited.png",
                    )
                ),
            ),
        ):
            result = await seedream_edit_image(
                SeedreamEditInput(
                    prompt="Replace the object with a crown.",
                    images=[_ref_image()],
                    point=EditCoordinate(x=520, y=460),
                ),
                fake_ctx,
            )

        assert isinstance(result, SeedreamEditOutput)
        assert result.provider == "byteplus-modelark"
        assert result.model == "dola-seedream-5-0-pro-260628"
        assert len(result.artifacts) == 1
        assert result.artifacts[0].media_type == "image"
        assert result.usage.total_tokens == 20

    async def test_bbox_edit_persistence(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        temp_store: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import base64

        img_b64 = base64.b64encode(b"edited-bytes").decode()
        _patch_seedream_service(
            monkeypatch,
            {"created": 1721400000, "data": [{"b64_json": img_b64}]},
        )

        with patch("ark_mcp.artifacts.registry.get_artifact_store", return_value=temp_store):
            result = await seedream_edit_image(
                SeedreamEditInput(
                    prompt="Replace with a garden.",
                    images=[_ref_image()],
                    bbox={"x1": 120, "y1": 180, "x2": 640, "y2": 760},
                ),
                fake_ctx,
            )

        assert len(result.artifacts) == 1
        stored = await temp_store.get(result.artifacts[0].id)
        assert stored.data == b"edited-bytes"

    async def test_no_persistence_returns_provider_url(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_seedream_service(
            monkeypatch,
            {
                "created": 1721400000,
                "data": [{"url": "https://tos-ap-southeast.bytepluses.com/edited.png"}],
            },
        )

        with patch("ark_mcp.artifacts.registry.get_artifact_store"):
            result = await seedream_edit_image(
                SeedreamEditInput(
                    prompt="edit",
                    images=[_ref_image()],
                    point=EditCoordinate(x=500, y=500),
                    persist=False,
                ),
                fake_ctx,
            )

        assert result.artifacts[0].id == "provider-url"
        assert result.artifacts[0].uri == "https://tos-ap-southeast.bytepluses.com/edited.png"

    async def test_provider_error_propagates(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def mock_generate(
            self: SeedreamService, request: Any
        ) -> tuple[SeedreamProviderResponse, str | None]:
            raise ProviderError(
                NormalizedProviderError(
                    provider="modelark",
                    operation="generate_image",
                    http_status=403,
                    code="FORBIDDEN",
                    message="model not activated",
                    retryable=False,
                )
            )

        monkeypatch.setattr(SeedreamService, "generate", mock_generate)

        result = await seedream_edit_image(
            SeedreamEditInput(
                prompt="edit", images=[_ref_image()], point=EditCoordinate(x=500, y=500)
            ),
            fake_ctx,
        )
        assert isinstance(result, ToolResult)
        assert result.is_error
        assert result.structured_content is None
        assert "http_status=403" in result.content[0].text

    async def test_too_many_references_rejected(
        self,
        test_env: None,
        fake_ctx: FakeContext,
    ) -> None:
        images = [
            MediaSource(kind=MediaSourceKind.url, url=f"https://example.com/{i}.png")
            for i in range(11)
        ]
        with pytest.raises(ValueError, match="at most 10"):
            await seedream_edit_image(
                SeedreamEditInput(prompt="edit", images=images, point=EditCoordinate(x=500, y=500)),
                fake_ctx,
            )
