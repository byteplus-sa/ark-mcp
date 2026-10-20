"""Integration tests for the ``seedream_generate_image`` tool handler.

Exercises the full path: input validation → capability registry check →
provider call (mocked) → artifact persistence → structured output.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastmcp.tools import ToolResult

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.providers.modelark.schemas import SeedreamProviderResponse
from ark_mcp.providers.modelark.seedream import SeedreamService
from ark_mcp.security.safe_downloader import DownloadedMedia
from ark_mcp.tools.seedream_generate_image import (
    SeedreamGenerateInput,
    SeedreamGenerateOutput,
    seedream_generate_image,
)
from tests.fixtures.fake_context import FakeContext


def _patch_seedream_service(monkeypatch: pytest.MonkeyPatch, response_data: dict[str, Any]) -> None:
    """Patch SeedreamService.generate to return a fixed response."""

    async def mock_generate(
        self: SeedreamService, request: Any
    ) -> tuple[SeedreamProviderResponse, str | None]:
        response = SeedreamProviderResponse.model_validate(response_data)
        return response, "req-test-456"

    monkeypatch.setattr(SeedreamService, "generate", mock_generate)


class TestSeedreamGenerateImageTool:
    """Full-path integration tests for seedream_generate_image."""

    async def test_text_to_image_with_url_persistence(
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
                "data": [{"url": "https://tos-ap-southeast.bytepluses.com/out.png"}],
                "usage": {"prompt_tokens": 10, "total_tokens": 20},
            },
        )

        # Mock the HTTP download for copy_from_trusted_url.
        with (
            patch("ark_mcp.artifacts.registry.get_artifact_store", return_value=temp_store),
            patch(
                "ark_mcp.security.safe_downloader.SafeDownloader.download",
                new=AsyncMock(
                    return_value=DownloadedMedia(
                        body=b"fake-png-data",
                        content_type="image/png",
                        final_url="https://tos-ap-southeast.bytepluses.com/out.png",
                    )
                ),
            ),
        ):
            result = await seedream_generate_image(
                SeedreamGenerateInput(prompt="a red circle"), fake_ctx
            )

        assert isinstance(result, SeedreamGenerateOutput)
        assert result.provider == "byteplus-modelark"
        assert result.model == "dola-seedream-5-0-pro-260628"
        assert len(result.artifacts) == 1
        assert result.artifacts[0].media_type == "image"
        assert result.usage.total_tokens == 20

    async def test_b64_json_persistence(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        temp_store: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import base64

        img_b64 = base64.b64encode(b"fake-image-bytes").decode()
        _patch_seedream_service(
            monkeypatch,
            {"created": 1721400000, "data": [{"b64_json": img_b64}]},
        )

        with patch("ark_mcp.artifacts.registry.get_artifact_store", return_value=temp_store):
            result = await seedream_generate_image(
                SeedreamGenerateInput(prompt="test image"), fake_ctx
            )

        assert len(result.artifacts) == 1
        assert result.artifacts[0].uri.startswith("seed-media://artifacts/")

        # Verify stored data.
        stored = await temp_store.get(result.artifacts[0].id)
        assert stored.data == b"fake-image-bytes"

    async def test_provider_output_format_overrides_request_default(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        temp_store: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        import base64

        _patch_seedream_service(
            monkeypatch,
            {
                "created": 1721400000,
                "data": [{"b64_json": base64.b64encode(b"fake").decode(), "output_format": "png"}],
            },
        )
        result = await seedream_generate_image(SeedreamGenerateInput(prompt="test"), fake_ctx)
        assert isinstance(result, SeedreamGenerateOutput)
        assert result.artifacts[0].mime_type == "image/png"

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
                "data": [{"url": "https://tos-ap-southeast.bytepluses.com/img.png"}],
            },
        )

        with patch("ark_mcp.artifacts.registry.get_artifact_store"):
            result = await seedream_generate_image(
                SeedreamGenerateInput(prompt="test", persist=False), fake_ctx
            )

        assert result.artifacts[0].id == "provider-url"
        assert result.artifacts[0].uri == "https://tos-ap-southeast.bytepluses.com/img.png"

    async def test_batch_rejected_for_pro_model(
        self,
        test_env: None,
        fake_ctx: FakeContext,
    ) -> None:
        with pytest.raises(ValueError, match=r"batch generation\) is not supported"):
            await seedream_generate_image(
                SeedreamGenerateInput(prompt="test", max_images=3), fake_ctx
            )

    async def test_too_many_references_rejected(
        self,
        test_env: None,
        fake_ctx: FakeContext,
    ) -> None:
        from ark_mcp.domain.media import MediaSource, MediaSourceKind

        images = [
            MediaSource(kind=MediaSourceKind.url, url=f"https://example.com/{i}.png")
            for i in range(11)
        ]
        with pytest.raises(ValueError, match="reference images exceed the maximum"):
            await seedream_generate_image(
                SeedreamGenerateInput(prompt="test", images=images), fake_ctx
            )

    async def test_prompt_optimization_defaults_to_standard_for_pro(
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
            return response, "req-test-456"

        monkeypatch.setattr(SeedreamService, "generate", mock_generate)

        await seedream_generate_image(SeedreamGenerateInput(prompt="test", persist=False), fake_ctx)

        assert captured[0].optimize_prompt_options == {"mode": "standard"}

    async def test_prompt_optimization_explicit_fast_overrides_default(
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
            return response, "req-test-456"

        monkeypatch.setattr(SeedreamService, "generate", mock_generate)

        await seedream_generate_image(
            SeedreamGenerateInput(prompt="test", prompt_optimization="fast", persist=False),
            fake_ctx,
        )

        assert captured[0].optimize_prompt_options == {"mode": "fast"}

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

        result = await seedream_generate_image(SeedreamGenerateInput(prompt="test"), fake_ctx)
        assert isinstance(result, ToolResult)
        assert result.is_error
        assert result.structured_content is None
        assert "http_status=403" in result.content[0].text


class TestSeedreamUnifiedModes:
    """Edit, transparent background and dry-run modes of the single Seedream tool."""

    async def test_point_edit_prepends_markup_and_background_is_forwarded(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: list[Any] = []

        async def mock_generate(self: SeedreamService, request: Any) -> Any:
            captured.append(request)
            return SeedreamProviderResponse.model_validate(
                {"created": 1, "data": [{"url": "https://tos-ap-southeast.bytepluses.com/o.png"}]}
            ), "req"

        monkeypatch.setattr(SeedreamService, "generate", mock_generate)
        monkeypatch.setattr(SeedreamService, "close", AsyncMock())
        result = await seedream_generate_image(
            SeedreamGenerateInput(
                prompt="cut out the cat",
                images=[{"kind": "url", "url": "https://example.com/cat.png"}],
                point={"x": 10, "y": 20},
                background="transparent",
                persist=False,
            ),
            fake_ctx,
        )
        assert isinstance(result, SeedreamGenerateOutput)
        assert captured[0].prompt == "Image 1<point>10 20</point> cut out the cat"
        assert captured[0].background == "transparent"

    @pytest.mark.parametrize(
        ("extra", "message"),
        [
            ({"images": None}, "needs exactly one input image"),
            ({"output_format": "jpeg"}, "requires output_format omitted or 'png'"),
        ],
    )
    async def test_transparent_background_rules(
        self, test_env: None, fake_ctx: FakeContext, extra: dict[str, Any], message: str
    ) -> None:
        arguments: dict[str, Any] = {
            "prompt": "cut out",
            "images": [{"kind": "url", "url": "https://example.com/a.png"}],
            "background": "transparent",
            **extra,
        }
        with pytest.raises(ValueError, match=message):
            await seedream_generate_image(SeedreamGenerateInput(**arguments), fake_ctx)

    @pytest.mark.parametrize("scheme", ["https", "HTTPS", "hTtPs"])
    async def test_dry_run_returns_requests_and_cost_without_generating(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
        scheme: str,
    ) -> None:
        generate = AsyncMock(side_effect=AssertionError("dry_run must not generate"))
        monkeypatch.setattr(SeedreamService, "generate", generate)
        result = await seedream_generate_image(
            SeedreamGenerateInput(
                prompt="a fox",
                variations=3,
                seed=7,
                images=[
                    {"kind": "url", "url": f"{scheme}://cdn.example.com/private/ref.png?sig=x"}
                ],
                dry_run=True,
            ),
            fake_ctx,
        )
        assert isinstance(result, SeedreamGenerateOutput)
        assert result.dry_run is True and result.preview is not None
        assert [request["seed"] for request in result.preview.requests] == [7, 8, 9]
        assert all(
            request["image"] == "https://cdn.example.com" for request in result.preview.requests
        )
        assert result.preview.estimated_cost_usd == 0.09
        generate.assert_not_awaited()

    async def test_max_images_and_variations_are_exclusive(self) -> None:
        with pytest.raises(ValueError, match="either max_images"):
            SeedreamGenerateInput(prompt="x", variations=2, max_images=2)
