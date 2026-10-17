"""Integration tests for the ``seed_audio_generate`` tool."""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import patch

import pytest

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.providers.seed_speech.schemas import SeedAudioProviderResponse
from ark_mcp.providers.seed_speech.seed_audio import SeedAudioService
from ark_mcp.tools.seed_audio_generate import (
    SeedAudioGenerateInput,
    SeedAudioGenerateOutput,
    seed_audio_generate,
)
from tests.fixtures.fake_context import FakeContext


def _patch_audio_by_prompt(
    monkeypatch: pytest.MonkeyPatch,
    responses_by_prompt: dict[str, dict[str, Any] | Exception],
) -> None:
    """Mock SeedAudioService.generate to return different results based on the prompt."""

    async def mock_generate(
        self: SeedAudioService,
        request: Any,
        *,
        request_id: str | None = None,
    ) -> tuple[SeedAudioProviderResponse, str | None]:
        response = responses_by_prompt.get(request.text_prompt)
        if isinstance(response, Exception):
            raise response
        return SeedAudioProviderResponse.model_validate(response), "log-test"

    monkeypatch.setattr(SeedAudioService, "generate", mock_generate)

    async def mock_close(self: SeedAudioService) -> None:
        pass

    monkeypatch.setattr(SeedAudioService, "close", mock_close)


class TestSeedAudioVariationsTool:
    """Integration tests for seed_audio_generate."""

    async def test_three_variations_all_succeed(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        temp_store: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        audio_b64 = base64.b64encode(b"fake-audio").decode()
        _patch_audio_by_prompt(
            monkeypatch,
            {
                "hello": {"code": 0, "audio": audio_b64, "duration": 1.0},
            },
        )

        with patch("ark_mcp.artifacts.registry.get_artifact_store", return_value=temp_store):
            result = await seed_audio_generate(
                SeedAudioGenerateInput(
                    text_prompt="hello",
                    variations=3,
                ),
                fake_ctx,
            )

        assert isinstance(result, SeedAudioGenerateOutput)
        assert result.summary.total == 3
        assert result.summary.succeeded == 3
        assert result.summary.failed == 0

    async def test_partial_failure(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        temp_store: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        audio_b64 = base64.b64encode(b"fake").decode()
        _patch_audio_by_prompt(
            monkeypatch,
            {
                "good": {"code": 0, "audio": audio_b64, "duration": 1.0},
                "bad": ProviderError(
                    NormalizedProviderError(
                        provider="seed-speech",
                        operation="generate_audio",
                        http_status=400,
                        code="INVALID_PARAM",
                        message="bad",
                        retryable=False,
                    )
                ),
            },
        )

        with patch("ark_mcp.artifacts.registry.get_artifact_store", return_value=temp_store):
            result = await seed_audio_generate(
                SeedAudioGenerateInput(
                    variation_prompts=["good", "bad", "good"],
                    variations=3,
                ),
                fake_ctx,
            )

        assert result.summary.succeeded == 2
        assert result.summary.failed == 1
        assert result.summary.variations[1].error is not None

    async def test_no_prompt_raises(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="Either text_prompt or variation_prompts"):
            SeedAudioGenerateInput(variations=2)

    async def test_media_mixing_raises(self) -> None:
        from pydantic import ValidationError

        from ark_mcp.domain.media import AudioReference, MediaSource, MediaSourceKind

        with pytest.raises(ValidationError, match="mutually exclusive"):
            SeedAudioGenerateInput(
                text_prompt="test",
                variations=1,
                audio_references=[AudioReference(kind="speaker", speaker_id="v1")],
                image_reference=MediaSource(kind=MediaSourceKind.url, url="https://x.com/i.png"),
            )

    async def test_single_generation_leaves_summary_empty(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        temp_store: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        audio_b64 = base64.b64encode(b"fake-audio").decode()
        _patch_audio_by_prompt(
            monkeypatch, {"solo": {"code": 0, "audio": audio_b64, "duration": 2.0}}
        )

        with patch("ark_mcp.artifacts.registry.get_artifact_store", return_value=temp_store):
            result = await seed_audio_generate(
                SeedAudioGenerateInput(variation_prompts=["solo"]), fake_ctx
            )

        assert isinstance(result, SeedAudioGenerateOutput)
        assert result.summary is None
        assert result.artifact is not None
        assert result.duration_seconds == 2.0

    async def test_variation_prompts_count_must_match(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="exactly 3 entries"):
            SeedAudioGenerateInput(variation_prompts=["a", "b"], variations=3)

    async def test_too_many_variations_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            SeedAudioGenerateInput(text_prompt="x", variations=6)
