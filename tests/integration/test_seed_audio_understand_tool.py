"""Integration tests for the ``seed_audio_understand`` tool handler.

Exercises input validation → request building (``input_audio`` parts) →
provider call (mocked) → structured output, plus the env-configured model ID.
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any
from unittest.mock import AsyncMock

import pytest
import respx
from fastmcp.tools import ToolResult
from pydantic import ValidationError

from ark_mcp.config.env import get_settings
from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.providers.modelark.files import ModelArkFile, ModelArkFilesService
from ark_mcp.providers.modelark.schemas import (
    ChatCompletionProviderRequest,
    ChatCompletionProviderResponse,
)
from ark_mcp.providers.modelark.understanding import SeedUnderstandingService
from ark_mcp.security.output_paths import OutputPathError
from ark_mcp.tools.seed_audio_understand import (
    SeedAudioUnderstandInput,
    SeedAudioUnderstandOutput,
    seed_audio_understand,
)
from ark_mcp.tools.seed_understand import UnderstandingResponseFormat
from tests.fixtures.fake_context import FakeContext

_AUDIO_URL = "https://cdn.example.com/speech.mp3"
_WAV_B64 = base64.b64encode(b"RIFF" + b"\x00" * 64).decode()


@respx.mock
async def test_malformed_upload_is_cleaned_and_returned_without_understanding_call(
    test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = get_settings().modelark_base_url.rstrip("/")
    respx.post(f"{base}/files").respond(200, json={"id": "file-malformed"})
    deletion = respx.delete(f"{base}/files/file-malformed").respond(200)
    captured = _capture_requests(monkeypatch)

    result = await seed_audio_understand(
        SeedAudioUnderstandInput(
            prompt="Transcribe", audios=[{"kind": "url", "url": _AUDIO_URL}], delivery="files_api"
        ),
        fake_ctx,
    )

    assert isinstance(result, ToolResult)
    assert result.is_error
    text = result.content[0].text
    assert "code=INVALID_RESPONSE" in text
    assert "ambiguous_completion=True" in text
    assert "retryable=False" in text
    assert deletion.called
    assert captured == []


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


def _rejection(message: str, *, status: int = 400) -> ProviderError:
    return ProviderError(
        NormalizedProviderError(
            provider="modelark",
            operation="chat_completion",
            http_status=status,
            code="InvalidParameter",
            message=message,
            retryable=False,
            ambiguous_completion=False,
        )
    )


_SIZE_REJECTION = (
    "The request failed because the size of the input audio (26 MiB) exceeds the limit (25 MiB)."
)


class _FakeFiles:
    """Records Files API calls; files classify as video when the URL ends in .mp4."""

    def __init__(self) -> None:
        self.uploads: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.fail_upload: ProviderError | None = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = self

        async def upload_url(self: ModelArkFilesService, url: str, **kwargs: Any) -> ModelArkFile:
            if fake.fail_upload is not None:
                raise fake.fail_upload
            fake.uploads.append({"url": url, **kwargs})
            mime = "video/mp4" if url.endswith(".mp4") else "audio/mpeg"
            return ModelArkFile(id=f"file-{len(fake.uploads)}", status="processing", mime_type=mime)

        async def wait_until_active(self: ModelArkFilesService, file: ModelArkFile) -> ModelArkFile:
            return file.model_copy(update={"status": "active"})

        async def delete(self: ModelArkFilesService, file_id: str) -> bool:
            fake.deleted.append(file_id)
            return True

        monkeypatch.setattr(ModelArkFilesService, "upload_url", upload_url)
        monkeypatch.setattr(ModelArkFilesService, "wait_until_active", wait_until_active)
        monkeypatch.setattr(ModelArkFilesService, "delete", delete)


def _capture_with_rejections(
    monkeypatch: pytest.MonkeyPatch, rejections: list[ProviderError]
) -> list[ChatCompletionProviderRequest]:
    """Reject the first requests with ``rejections``, then answer normally."""
    captured: list[ChatCompletionProviderRequest] = []

    async def mock_generate(
        self: SeedUnderstandingService, request: ChatCompletionProviderRequest
    ) -> tuple[ChatCompletionProviderResponse, str | None]:
        captured.append(request)
        if rejections:
            raise rejections.pop(0)
        return ChatCompletionProviderResponse.model_validate(_response("ok")), "req-audio"

    monkeypatch.setattr(SeedUnderstandingService, "generate", mock_generate)
    return captured


def _audio_parts(request: ChatCompletionProviderRequest) -> list[dict[str, Any]]:
    content = request.model_dump(exclude_none=True)["messages"][-1]["content"]
    return [part for part in content if part["type"] != "text"]


class TestSeedAudioUnderstandFilesApi:
    async def test_invalid_output_path_does_not_upload(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        upload = AsyncMock(side_effect=AssertionError("Must validate output before upload"))
        monkeypatch.setattr(ModelArkFilesService, "upload_url", upload)
        with pytest.raises(OutputPathError):
            await seed_audio_understand(
                SeedAudioUnderstandInput(
                    prompt="x",
                    audios=[{"kind": "url", "url": _AUDIO_URL}],
                    delivery="files_api",
                    save_to="/outside-allowed-roots/answer.txt",
                ),
                fake_ctx,
            )
        upload.assert_not_awaited()

    async def test_cancellation_during_processing_deletes_uploaded_file(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        files = _FakeFiles()
        files.install(monkeypatch)
        closed = AsyncMock()
        monkeypatch.setattr(
            ModelArkFilesService,
            "wait_until_active",
            AsyncMock(side_effect=asyncio.CancelledError()),
        )
        monkeypatch.setattr(ModelArkFilesService, "close", closed)
        with pytest.raises(asyncio.CancelledError):
            await seed_audio_understand(
                SeedAudioUnderstandInput(
                    prompt="x", audios=[{"kind": "url", "url": _AUDIO_URL}], delivery="files_api"
                ),
                fake_ctx,
            )
        assert files.deleted == ["file-1"]
        closed.assert_awaited_once()

    async def test_auto_retries_oversized_url_through_files_api(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        files = _FakeFiles()
        files.install(monkeypatch)
        captured = _capture_with_rejections(monkeypatch, [_rejection(_SIZE_REJECTION)])
        video_url = "https://cdn.example.com/episode.mp4"

        result = await seed_audio_understand(
            SeedAudioUnderstandInput(
                prompt="Review the soundtrack.",
                audios=[
                    {"kind": "url", "url": video_url},
                    {"kind": "base64", "data": _WAV_B64, "mime_type": "audio/wav"},
                ],
            ),
            fake_ctx,
        )

        assert isinstance(result, SeedAudioUnderstandOutput)
        assert result.choices[0].content == "ok"
        assert result.attempts == 1
        assert result.audio_delivery == ["files_api_video", "base64"]
        assert result.delivery_fallback_reason == _SIZE_REJECTION
        assert len(captured) == 2
        assert _audio_parts(captured[0])[0] == {
            "type": "input_audio",
            "input_audio": {"url": video_url},
        }
        retried = _audio_parts(captured[1])
        assert retried[0] == {"type": "video_url", "video_url": {"file_id": "file-1"}}
        assert retried[1]["input_audio"]["format"] == "wav"
        assert files.uploads == [
            {"url": video_url, "video_model": "seed-2-0-lite-260428", "video_fps": 0.2}
        ]
        assert files.deleted == ["file-1"]

    async def test_explicit_files_api_uploads_before_the_call(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        files = _FakeFiles()
        files.install(monkeypatch)
        captured = _capture_with_rejections(monkeypatch, [])

        result = await seed_audio_understand(
            SeedAudioUnderstandInput(
                prompt="Transcribe.",
                audios=[{"kind": "url", "url": _AUDIO_URL}],
                delivery="files_api",
            ),
            fake_ctx,
        )

        assert isinstance(result, SeedAudioUnderstandOutput)
        assert result.audio_delivery == ["files_api_audio"]
        assert result.delivery_fallback_reason is None
        assert len(captured) == 1
        assert _audio_parts(captured[0]) == [
            {"type": "input_audio", "input_audio": {"file_id": "file-1"}}
        ]
        assert files.deleted == ["file-1"]

    async def test_direct_reports_rejection_without_files_api(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        files = _FakeFiles()
        files.install(monkeypatch)
        captured = _capture_with_rejections(monkeypatch, [_rejection(_SIZE_REJECTION)])

        result = await seed_audio_understand(
            SeedAudioUnderstandInput(
                prompt="x", audios=[{"kind": "url", "url": _AUDIO_URL}], delivery="direct"
            ),
            fake_ctx,
        )

        assert isinstance(result, ToolResult)
        assert result.is_error
        assert len(captured) == 1
        assert files.uploads == []

    @pytest.mark.parametrize(
        "rejection",
        [
            _rejection("response_format.json_schema is invalid"),
            _rejection("the input audio is temporarily unavailable", status=503),
        ],
    )
    async def test_auto_ignores_unrelated_rejections(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        monkeypatch: pytest.MonkeyPatch,
        rejection: ProviderError,
    ) -> None:
        files = _FakeFiles()
        files.install(monkeypatch)
        captured = _capture_with_rejections(monkeypatch, [rejection])

        result = await seed_audio_understand(
            SeedAudioUnderstandInput(prompt="x", audios=[{"kind": "url", "url": _AUDIO_URL}]),
            fake_ctx,
        )

        assert isinstance(result, ToolResult)
        assert result.is_error
        assert len(captured) == 1
        assert files.uploads == []

    async def test_auto_without_urls_never_falls_back(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        files = _FakeFiles()
        files.install(monkeypatch)
        captured = _capture_with_rejections(monkeypatch, [_rejection(_SIZE_REJECTION)])

        result = await seed_audio_understand(
            SeedAudioUnderstandInput(
                prompt="x",
                audios=[{"kind": "base64", "data": _WAV_B64, "mime_type": "audio/wav"}],
            ),
            fake_ctx,
        )

        assert isinstance(result, ToolResult)
        assert len(captured) == 1
        assert files.uploads == []

    async def test_upload_failure_is_reported_before_any_billed_call(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        files = _FakeFiles()
        files.install(monkeypatch)
        files.fail_upload = _rejection("url is not reachable")
        captured = _capture_with_rejections(monkeypatch, [])

        result = await seed_audio_understand(
            SeedAudioUnderstandInput(
                prompt="x", audios=[{"kind": "url", "url": _AUDIO_URL}], delivery="files_api"
            ),
            fake_ctx,
        )

        assert isinstance(result, ToolResult)
        assert result.is_error
        assert captured == []

    async def test_uploads_are_deleted_when_the_retry_fails(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        files = _FakeFiles()
        files.install(monkeypatch)
        captured = _capture_with_rejections(
            monkeypatch, [_rejection(_SIZE_REJECTION), _rejection("audio is too long")]
        )

        result = await seed_audio_understand(
            SeedAudioUnderstandInput(prompt="x", audios=[{"kind": "url", "url": _AUDIO_URL}]),
            fake_ctx,
        )

        assert isinstance(result, ToolResult)
        assert result.is_error
        assert len(captured) == 2
        assert files.deleted == ["file-1"]


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

    def test_base64_data_uri_rejected(self, test_env: None) -> None:
        with pytest.raises(ValidationError, match="without a 'data:' URI prefix"):
            SeedAudioUnderstandInput(
                prompt="x",
                audios=[
                    {
                        "kind": "base64",
                        "data": f"data:audio/wav;base64,{_WAV_B64}",
                        "mime_type": "audio/wav",
                    }
                ],
            )

    @pytest.mark.parametrize(
        ("mime_type", "expected_format"),
        [("audio/mpeg; codecs=mp3", "mp3"), (" AUDIO/WAV ", "wav")],
    )
    def test_base64_mime_type_is_normalized(
        self, test_env: None, mime_type: str, expected_format: str
    ) -> None:
        payload = SeedAudioUnderstandInput(
            prompt="x",
            audios=[{"kind": "base64", "data": _WAV_B64, "mime_type": mime_type}],
        )
        assert payload.audios[0].provider_part()["format"] == expected_format

    def test_non_https_url_rejected(self, test_env: None) -> None:
        with pytest.raises(ValidationError):
            SeedAudioUnderstandInput(
                prompt="x", audios=[{"kind": "url", "url": "http://127.0.0.1/a.wav"}]
            )

    def test_too_many_audios_rejected(self, test_env: None) -> None:
        with pytest.raises(ValidationError):
            SeedAudioUnderstandInput(prompt="x", audios=[{"kind": "url", "url": _AUDIO_URL}] * 9)
