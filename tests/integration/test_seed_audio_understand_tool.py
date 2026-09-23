"""Integration tests for the ``seed_audio_understand`` tool handler.

Exercises input validation → request building (``input_audio`` parts) →
provider call (mocked) → structured output, plus the env-configured model ID.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest
from pydantic import ValidationError

from ark_mcp.config.env import get_settings
from ark_mcp.providers.modelark.schemas import (
    ChatCompletionProviderRequest,
    ChatCompletionProviderResponse,
)
from ark_mcp.providers.modelark.understanding import SeedUnderstandingService
from ark_mcp.tools.seed_audio_understand import (
    SeedAudioUnderstandInput,
    SeedAudioUnderstandOutput,
    seed_audio_understand,
)
from ark_mcp.tools.seed_understand import UnderstandingResponseFormat
from tests.fixtures.fake_context import FakeContext

_AUDIO_URL = "https://cdn.example.com/speech.mp3"
_WAV_B64 = base64.b64encode(b"RIFF" + b"\x00" * 64).decode()


def _response(content: str) -> dict[str, Any]:
    return {
        "id": "chatcmpl-audio-1",
        "model": "seed-2-0-lite-260428",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                    "reasoning_content": "secret reasoning",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 70,
            "completion_tokens": 20,
            "total_tokens": 90,
            "completion_tokens_details": {"reasoning_tokens": 12},
        },
    }


def _capture_requests(
    monkeypatch: pytest.MonkeyPatch, content: str = "the quick brown fox"
) -> list[ChatCompletionProviderRequest]:
    captured: list[ChatCompletionProviderRequest] = []

    async def mock_generate(
        self: SeedUnderstandingService, request: ChatCompletionProviderRequest
    ) -> tuple[ChatCompletionProviderResponse, str | None]:
        captured.append(request)
        return ChatCompletionProviderResponse.model_validate(_response(content)), "req-audio"

    monkeypatch.setattr(SeedUnderstandingService, "generate", mock_generate)
    return captured


class TestBuildRequestAudio:
    def test_url_and_base64_parts(self) -> None:
        request = SeedUnderstandingService.build_request(
            model="seed-2-0-lite-260428",
            prompt="Transcribe.",
            audio_parts=[
                {"kind": "url", "url": _AUDIO_URL},
                {"kind": "base64", "data": "AAAA", "format": "wav"},
            ],
        )
        body = request.model_dump(exclude_none=True)
        content = body["messages"][-1]["content"]
        assert content[0] == {"type": "input_audio", "input_audio": {"url": _AUDIO_URL}}
        assert content[1] == {
            "type": "input_audio",
            "input_audio": {"data": "AAAA", "format": "wav"},
        }
        assert content[-1] == {"type": "text", "text": "Transcribe."}


class TestSeedAudioUnderstandTool:
    async def test_url_audio_uses_default_model_with_thinking(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured = _capture_requests(monkeypatch)

        result = await seed_audio_understand(
            SeedAudioUnderstandInput(
                prompt="Transcribe this.",
                audios=[{"kind": "url", "url": _AUDIO_URL}],
                system="Be terse.",
                reasoning_effort="low",
            ),
            fake_ctx,
        )

        assert isinstance(result, SeedAudioUnderstandOutput)
        assert result.model == "seed-2-0-lite-260428"
        assert result.choices[0].content == "the quick brown fox"
        assert "secret reasoning" not in result.model_dump_json()
        assert result.usage.reasoning_tokens == 12
        assert result.request_id == "req-audio"

        body = captured[0].model_dump(exclude_none=True)
        assert body["model"] == "seed-2-0-lite-260428"
        assert body["thinking"] == {"type": "enabled"}
        assert body["reasoning_effort"] == "low"
        assert body["stream"] is False
        assert body["messages"][0] == {"role": "system", "content": "Be terse."}
        user_content = body["messages"][1]["content"]
        assert user_content[0] == {"type": "input_audio", "input_audio": {"url": _AUDIO_URL}}
        assert user_content[-1]["text"] == "Transcribe this."

    async def test_model_is_configurable_via_env(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("SEED_AUDIO_UNDERSTANDING_MODEL", "seed-audio-next-270101")
        get_settings.cache_clear()
        captured = _capture_requests(monkeypatch)

        result = await seed_audio_understand(
            SeedAudioUnderstandInput(prompt="x", audios=[{"kind": "url", "url": _AUDIO_URL}]),
            fake_ctx,
        )

        assert isinstance(result, SeedAudioUnderstandOutput)
        assert result.model == "seed-audio-next-270101"
        assert captured[0].model == "seed-audio-next-270101"

    @pytest.mark.parametrize(
        ("mime_type", "expected_format"),
        [
            ("audio/wav", "wav"),
            ("audio/mpeg", "mp3"),
            ("audio/flac", "flac"),
            ("audio/aac", "aac"),
            ("audio/mp4", "m4a"),
        ],
    )
    async def test_base64_audio_sends_raw_data_with_format(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
        mime_type: str,
        expected_format: str,
    ) -> None:
        captured = _capture_requests(monkeypatch)

        await seed_audio_understand(
            SeedAudioUnderstandInput(
                prompt="x",
                audios=[{"kind": "base64", "data": _WAV_B64, "mime_type": mime_type}],
            ),
            fake_ctx,
        )

        part = captured[0].model_dump(exclude_none=True)["messages"][-1]["content"][0]
        assert part == {
            "type": "input_audio",
            "input_audio": {"data": _WAV_B64, "format": expected_format},
        }

    async def test_json_schema_answer_is_parsed(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured = _capture_requests(monkeypatch, '{"gender": "male", "transcript": "hi"}')
        schema = {
            "type": "object",
            "properties": {"gender": {"type": "string"}, "transcript": {"type": "string"}},
            "required": ["gender", "transcript"],
            "additionalProperties": False,
        }

        result = await seed_audio_understand(
            SeedAudioUnderstandInput(
                prompt="Describe.",
                audios=[{"kind": "url", "url": _AUDIO_URL}],
                response_format=UnderstandingResponseFormat.model_validate(
                    {"type": "json_schema", "json_schema": {"name": "speaker", "schema": schema}}
                ),
            ),
            fake_ctx,
        )

        assert isinstance(result, SeedAudioUnderstandOutput)
        assert result.choices[0].parsed == {"gender": "male", "transcript": "hi"}
        sent = captured[0].model_dump(exclude_none=True)["response_format"]
        assert sent["type"] == "json_schema"
        assert sent["json_schema"]["schema"] == schema


class TestSeedAudioUnderstandValidation:
    def test_audios_required(self, test_env: None) -> None:
        with pytest.raises(ValidationError):
            SeedAudioUnderstandInput(prompt="x", audios=[])

    def test_base64_without_mime_type_rejected(self, test_env: None) -> None:
        with pytest.raises(ValidationError, match="requires mime_type"):
            SeedAudioUnderstandInput(prompt="x", audios=[{"kind": "base64", "data": _WAV_B64}])

    def test_base64_unsupported_format_rejected(self, test_env: None) -> None:
        with pytest.raises(ValidationError, match="requires mime_type"):
            SeedAudioUnderstandInput(
                prompt="x",
                audios=[{"kind": "base64", "data": _WAV_B64, "mime_type": "audio/ogg"}],
            )

    def test_non_https_url_rejected(self, test_env: None) -> None:
        with pytest.raises(ValidationError):
            SeedAudioUnderstandInput(
                prompt="x", audios=[{"kind": "url", "url": "http://127.0.0.1/a.wav"}]
            )

    def test_too_many_audios_rejected(self, test_env: None) -> None:
        with pytest.raises(ValidationError):
            SeedAudioUnderstandInput(prompt="x", audios=[{"kind": "url", "url": _AUDIO_URL}] * 9)
