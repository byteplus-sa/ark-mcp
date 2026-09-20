"""Integration tests for the ``seedream_generate_image_variations`` tool."""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import patch

import pytest

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.providers.modelark.schemas import SeedreamProviderResponse
from ark_mcp.providers.modelark.seedream import SeedreamService
from ark_mcp.tools.seedream_generate_image_variations import (
    SeedreamVariationsInput,
    SeedreamVariationsOutput,
    seedream_generate_image_variations,
)
from tests.fixtures.fake_context import FakeContext


def _patch_seedream_by_seed(
    monkeypatch: pytest.MonkeyPatch,
    responses_by_seed: dict[int | None, dict[str, Any] | Exception],
) -> None:
    """Mock SeedreamService.generate to return different results based on the request seed."""

    async def mock_generate(
        self: SeedreamService, request: Any
    ) -> tuple[SeedreamProviderResponse, str | None]:
        response = responses_by_seed.get(request.seed)
        if isinstance(response, Exception):
            raise response
        return SeedreamProviderResponse.model_validate(response), f"req-{request.seed}"

    monkeypatch.setattr(SeedreamService, "generate", mock_generate)

    async def mock_close(self: SeedreamService) -> None:
        pass

    monkeypatch.setattr(SeedreamService, "close", mock_close)


class TestSeedreamVariationsTool:
    """Integration tests for seedream_generate_image_variations."""

    async def test_prompt_optimization_defaults_to_standard(
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
            return response, f"req-{request.seed}"

        monkeypatch.setattr(SeedreamService, "generate", mock_generate)

        async def mock_close(self: SeedreamService) -> None:
            pass

        monkeypatch.setattr(SeedreamService, "close", mock_close)

        await seedream_generate_image_variations(
            SeedreamVariationsInput(prompt="a red circle", variations=2, base_seed=42),
            fake_ctx,
        )

        assert len(captured) == 2
        assert all(req.optimize_prompt_options == {"mode": "standard"} for req in captured)

    async def test_three_variations_all_succeed(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        temp_store: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        img_b64 = base64.b64encode(b"fake-image").decode()
        _patch_seedream_by_seed(
            monkeypatch,
            {
                42: {"created": 1721400000, "data": [{"b64_json": img_b64}]},
                43: {"created": 1721400000, "data": [{"b64_json": img_b64}]},
                44: {"created": 1721400000, "data": [{"b64_json": img_b64}]},
            },
        )

        with patch("ark_mcp.artifacts.registry.get_artifact_store", return_value=temp_store):
            result = await seedream_generate_image_variations(
                SeedreamVariationsInput(
                    prompt="a red circle",
                    variations=3,
                    base_seed=42,
                ),
                fake_ctx,
            )

        assert isinstance(result, SeedreamVariationsOutput)
        assert result.summary.total == 3
        assert result.summary.succeeded == 3
        assert result.summary.failed == 0
        assert len(result.summary.variations) == 3
        assert result.summary.variations[0].seed == 42
        assert result.summary.variations[1].seed == 43
        assert result.summary.variations[2].seed == 44
        assert all(r.artifact is not None for r in result.summary.variations)

    async def test_partial_failure(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        temp_store: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        img_b64 = base64.b64encode(b"fake-image").decode()
        _patch_seedream_by_seed(
            monkeypatch,
            {
                10: {"created": 1721400000, "data": [{"b64_json": img_b64}]},
                11: ProviderError(
                    NormalizedProviderError(
                        provider="modelark",
                        operation="generate_image",
                        http_status=400,
                        code="INVALID_PARAM",
                        message="bad prompt",
                        retryable=False,
                    )
                ),
                12: {"created": 1721400000, "data": [{"b64_json": img_b64}]},
            },
        )

        with patch("ark_mcp.artifacts.registry.get_artifact_store", return_value=temp_store):
            result = await seedream_generate_image_variations(
                SeedreamVariationsInput(
                    prompt="test",
                    variations=3,
                    base_seed=10,
                ),
                fake_ctx,
            )

        assert result.summary.succeeded == 2
        assert result.summary.failed == 1
        assert result.summary.variations[1].error is not None
        assert result.summary.variations[1].error.code == "INVALID_PARAM"

    async def test_variation_prompts_override(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        temp_store: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        img_b64 = base64.b64encode(b"fake").decode()
        _patch_seedream_by_seed(
            monkeypatch,
            {
                None: {"created": 1721400000, "data": [{"b64_json": img_b64}]},
            },
        )

        with patch("ark_mcp.artifacts.registry.get_artifact_store", return_value=temp_store):
            result = await seedream_generate_image_variations(
                SeedreamVariationsInput(
                    variation_prompts=["prompt A", "prompt B"],
                    variations=2,
                ),
                fake_ctx,
            )

        assert result.summary.succeeded == 2

    async def test_no_prompt_raises(
        self,
        test_env: None,
        fake_ctx: FakeContext,
    ) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Either prompt or variation_prompts"):
            SeedreamVariationsInput(variations=2)

    async def test_prompts_length_mismatch_raises(
        self,
        test_env: None,
        fake_ctx: FakeContext,
    ) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="must have exactly 3"):
            SeedreamVariationsInput(
                prompt="test",
                variations=3,
                variation_prompts=["only one"],
            )

    async def test_variations_equals_one(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        temp_store: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        img_b64 = base64.b64encode(b"fake").decode()
        _patch_seedream_by_seed(
            monkeypatch,
            {None: {"created": 1721400000, "data": [{"b64_json": img_b64}]}},
        )

        with patch("ark_mcp.artifacts.registry.get_artifact_store", return_value=temp_store):
            result = await seedream_generate_image_variations(
                SeedreamVariationsInput(prompt="test", variations=1),
                fake_ctx,
            )

        assert result.summary.total == 1
        assert result.summary.succeeded == 1

    async def test_all_variations_fail(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        _patch_seedream_by_seed(
            monkeypatch,
            {
                0: ProviderError(
                    NormalizedProviderError(
                        provider="modelark",
                        operation="generate_image",
                        http_status=403,
                        code="FORBIDDEN",
                        message="denied",
                        retryable=False,
                    )
                ),
                1: ProviderError(
                    NormalizedProviderError(
                        provider="modelark",
                        operation="generate_image",
                        http_status=403,
                        code="FORBIDDEN",
                        message="denied",
                        retryable=False,
                    )
                ),
            },
        )

        result = await seedream_generate_image_variations(
            SeedreamVariationsInput(prompt="test", variations=2, base_seed=0),
            fake_ctx,
        )

        assert result.summary.succeeded == 0
        assert result.summary.failed == 2
        assert all(r.error is not None for r in result.summary.variations)

    async def test_base_seed_none(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        temp_store: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        img_b64 = base64.b64encode(b"fake").decode()
        _patch_seedream_by_seed(
            monkeypatch,
            {None: {"created": 1721400000, "data": [{"b64_json": img_b64}]}},
        )

        with patch("ark_mcp.artifacts.registry.get_artifact_store", return_value=temp_store):
            result = await seedream_generate_image_variations(
                SeedreamVariationsInput(prompt="test", variations=2, base_seed=None),
                fake_ctx,
            )

        assert all(r.seed is None for r in result.summary.variations)
