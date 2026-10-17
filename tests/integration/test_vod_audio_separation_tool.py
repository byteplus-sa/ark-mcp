"""Integration tests for the VOD AI MediaKit audio separation MCP tools."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastmcp.tools import ToolResult
from pydantic import ValidationError

from ark_mcp.artifacts.store import ArtifactPersistenceError
from ark_mcp.domain.artifacts import ArtifactRef
from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.domain.models import TaskOutputFile
from ark_mcp.providers.vod_mediakit.schemas import (
    SeparateVoiceSubmission,
    SeparateVoiceTask,
)
from ark_mcp.providers.vod_mediakit.separate_voice import (
    VodMediaKitSeparateVoiceService,
)
from ark_mcp.tools.vod_get_task import VodGetTaskInput, VodTaskOutput, vod_get_task
from ark_mcp.tools.vod_separate_audio import (
    VodSeparateAudioInput,
    VodSeparateAudioOutput,
    vod_separate_audio,
)
from tests.fixtures.fake_context import FakeContext
from tests.fixtures.ownership import owned_task_ids


async def _close(_self: VodMediaKitSeparateVoiceService) -> None:
    return None


def _submission() -> SeparateVoiceSubmission:
    return SeparateVoiceSubmission(
        status="accepted",
        request_id="body-1",
        provider_log_id="log-1",
        task_id="amk-tool-separate-voice-1",
    )


def _poll_input(**kwargs: object) -> VodGetTaskInput:
    return VodGetTaskInput(operation="separate_audio", **kwargs)  # type: ignore[arg-type]


def _tracks(result: VodTaskOutput) -> dict[str, TaskOutputFile]:
    return {output.role: output for output in result.outputs}


def _succeeded_task() -> SeparateVoiceTask:
    return SeparateVoiceTask(
        task_id="amk-tool-separate-voice-1",
        status="succeeded",
        provider_status="completed",
        request_id="req-get",
        voice_url="https://vod.ap-southeast-1.byteplusvod.com/voice.aac",
        background_url="https://vod.ap-southeast-1.byteplusvod.com/background.aac",
        duration_seconds=120.5,
        created_at="2026-06-02T07:36:15+00:00",
        finished_at="2026-06-02T07:36:37+00:00",
        source_expires_at="2026-06-03T07:36:36+00:00",
    )


def _succeeded_three_way_task() -> SeparateVoiceTask:
    return SeparateVoiceTask(
        task_id="amk-tool-separate-voice-1",
        status="succeeded",
        provider_status="completed",
        request_id="req-get",
        voice_url="https://vod.ap-southeast-1.byteplusvod.com/voice.aac",
        music_url="https://vod.ap-southeast-1.byteplusvod.com/music.aac",
        sfx_url="https://vod.ap-southeast-1.byteplusvod.com/sfx.aac",
        duration_seconds=120.5,
        created_at="2026-06-02T07:36:15+00:00",
        finished_at="2026-06-02T07:36:37+00:00",
        source_expires_at="2026-06-03T07:36:36+00:00",
    )


def _audio_ref() -> ArtifactRef:
    return ArtifactRef(
        id="artifact-1",
        uri="seed-media://artifacts/artifact-1",
        media_type="audio",
        mime_type="audio/aac",
        bytes=123,
        sha256="abc",
        created_at="2026-08-12T00:00:00Z",
    )


async def test_submit_accepts_and_records_ownership(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    async def submit(
        _self: VodMediaKitSeparateVoiceService, request: object
    ) -> SeparateVoiceSubmission:
        captured.append(request)
        return _submission()

    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "submit", submit)
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "close", _close)
    runtime = fake_ctx.lifespan_context["runtime"]

    result = await vod_separate_audio(
        VodSeparateAudioInput(video_url="https://example.com/clip.mp4", scene="Drama"),
        fake_ctx,
    )

    assert isinstance(result, VodSeparateAudioOutput)
    assert result.status == "accepted"
    assert result.task_id == "amk-tool-separate-voice-1"
    assert result.request_id == "body-1"
    assert result.provider_log_id == "log-1"
    assert result.recommended_poll_after_ms == 3000
    assert result.operation == "separate_audio"
    assert captured
    assert await owned_task_ids(runtime, "vod-mediakit") == {"amk-tool-separate-voice-1"}


async def test_blocked_source_url_returns_sanitized_error(
    test_env: None,
    fake_ctx: FakeContext,
) -> None:
    result = await vod_separate_audio(
        VodSeparateAudioInput(audio_url="https://10.0.0.1/input.mp3"), fake_ctx
    )
    assert isinstance(result, ToolResult)
    assert result.is_error
    assert "Invalid media URL." in result.content[0].text
    assert "10.0.0.1" not in result.content[0].text
    assert "resolves to blocked" not in result.content[0].text


async def test_submit_serializes_documented_request(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    async def submit(
        _self: VodMediaKitSeparateVoiceService, request: object
    ) -> SeparateVoiceSubmission:
        captured.append(request.model_dump(mode="json", exclude_none=True))  # type: ignore[attr-defined]
        return _submission()

    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "submit", submit)
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "close", _close)

    await vod_separate_audio(
        VodSeparateAudioInput(audio_url="https://example.com/song.mp3", output_format="mp3"),
        fake_ctx,
    )

    request = captured[0]
    assert request["audio_url"] == "https://example.com/song.mp3"
    assert request["scene"] == "Audio"
    assert request["output_format"] == "mp3"
    assert "video_url" not in request


def test_submit_requires_exactly_one_source() -> None:
    with pytest.raises(ValidationError):
        VodSeparateAudioInput()
    with pytest.raises(ValidationError):
        VodSeparateAudioInput(
            audio_url="https://example.com/a.mp3",
            video_url="https://example.com/b.mp4",
        )


async def test_submit_provider_error_returns_mcp_error(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = ProviderError(
        NormalizedProviderError(
            provider="byteplus-vod-mediakit",
            operation="separate_voice",
            http_status=429,
            code="RATE_LIMITED",
            message="Try later.",
            retryable=True,
        )
    )
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "submit", AsyncMock(side_effect=error))
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "close", _close)

    result = await vod_separate_audio(
        VodSeparateAudioInput(video_url="https://example.com/clip.mp4"), fake_ctx
    )

    assert isinstance(result, ToolResult)
    assert result.is_error is True


async def test_submit_missing_credential_raises(
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ark_mcp.config.env import Settings

    settings = Settings(_env_file=None, BYTEPLUS_VOD_MEDIAKIT_API_KEY="")
    assert settings.has_vod_mediakit is False

    runtime = fake_ctx.lifespan_context["runtime"]
    monkeypatch.setattr(runtime, "settings", settings)
    with pytest.raises(ValueError):
        await vod_separate_audio(
            VodSeparateAudioInput(video_url="https://example.com/clip.mp4"), fake_ctx
        )


async def test_poll_processing_returns_processing(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = SeparateVoiceTask(
        task_id="amk-tool-separate-voice-1",
        status="processing",
        provider_status="running",
        request_id="req-1",
        created_at="2026-06-02T07:36:15+00:00",
    )
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "get", AsyncMock(return_value=task))
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "close", _close)

    result = await vod_get_task(_poll_input(task_id="amk-tool-separate-voice-1"), fake_ctx)

    assert isinstance(result, VodTaskOutput)
    assert result.status == "processing"
    assert result.provider_status == "running"
    assert result.operation == "separate_audio"
    assert result.outputs == []


async def test_poll_succeeded_persists_tracks_once(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        VodMediaKitSeparateVoiceService, "get", AsyncMock(return_value=_succeeded_task())
    )
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "close", _close)
    runtime = fake_ctx.lifespan_context["runtime"]
    copy = AsyncMock(return_value=_audio_ref())
    monkeypatch.setattr(runtime.artifact_store, "copy_from_trusted_url", copy)

    result = await vod_get_task(_poll_input(task_id="amk-tool-separate-voice-1"), fake_ctx)

    assert isinstance(result, VodTaskOutput)
    assert result.status == "succeeded"
    assert result.duration_seconds == 120.5
    tracks = _tracks(result)
    assert list(tracks) == ["voice", "background"]
    assert tracks["voice"].persistence == "persisted"
    assert tracks["voice"].artifact == _audio_ref()
    assert str(tracks["voice"].source_url) == "https://vod.ap-southeast-1.byteplusvod.com/voice.aac"
    assert tracks["background"].persistence == "persisted"
    assert copy.await_count == 2

    result2 = await vod_get_task(_poll_input(task_id="amk-tool-separate-voice-1"), fake_ctx)
    assert isinstance(result2, VodTaskOutput)
    assert _tracks(result2)["voice"].persistence == "persisted"
    assert copy.await_count == 2


async def test_poll_infers_track_mime_from_url_suffix(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = SeparateVoiceTask(
        task_id="amk-tool-separate-voice-1",
        status="succeeded",
        provider_status="completed",
        voice_url="https://vod.ap-southeast-1.byteplusvod.com/voice.mp3?sign=1",
        background_url="https://vod.ap-southeast-1.byteplusvod.com/background.flac?sign=2",
    )
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "get", AsyncMock(return_value=task))
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "close", _close)
    runtime = fake_ctx.lifespan_context["runtime"]
    copy = AsyncMock(return_value=_audio_ref())
    monkeypatch.setattr(runtime.artifact_store, "copy_from_trusted_url", copy)

    await vod_get_task(_poll_input(task_id="amk-tool-separate-voice-1"), fake_ctx)

    assert copy.await_count == 2
    assert copy.await_args_list[0].kwargs["mime_type"] == "audio/mpeg"
    assert copy.await_args_list[1].kwargs["mime_type"] == "audio/flac"


async def test_poll_succeeded_three_way_tracks(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        VodMediaKitSeparateVoiceService, "get", AsyncMock(return_value=_succeeded_three_way_task())
    )
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "close", _close)
    runtime = fake_ctx.lifespan_context["runtime"]
    copy = AsyncMock(return_value=_audio_ref())
    monkeypatch.setattr(runtime.artifact_store, "copy_from_trusted_url", copy)

    result = await vod_get_task(_poll_input(task_id="amk-tool-separate-voice-1"), fake_ctx)

    assert isinstance(result, VodTaskOutput)
    assert list(_tracks(result)) == ["voice", "music", "sfx"]
    assert copy.await_count == 3


async def test_poll_succeeded_persist_false(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        VodMediaKitSeparateVoiceService, "get", AsyncMock(return_value=_succeeded_task())
    )
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "close", _close)
    runtime = fake_ctx.lifespan_context["runtime"]
    copy = AsyncMock()
    monkeypatch.setattr(runtime.artifact_store, "copy_from_trusted_url", copy)

    result = await vod_get_task(
        _poll_input(task_id="amk-tool-separate-voice-1", persist_output=False),
        fake_ctx,
    )

    assert isinstance(result, VodTaskOutput)
    assert result.status == "succeeded"
    voice = _tracks(result)["voice"]
    assert voice.persistence == "not_requested"
    assert voice.artifact is None
    assert voice.source_url is not None
    copy.assert_not_awaited()


async def test_poll_succeeded_persistence_failure_preserves_success(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        VodMediaKitSeparateVoiceService, "get", AsyncMock(return_value=_succeeded_task())
    )
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "close", _close)
    runtime = fake_ctx.lifespan_context["runtime"]
    monkeypatch.setattr(
        runtime.artifact_store,
        "copy_from_trusted_url",
        AsyncMock(
            side_effect=ArtifactPersistenceError(
                "output_too_large", "Output exceeds the artifact limit.", retryable=False
            )
        ),
    )

    result = await vod_get_task(_poll_input(task_id="amk-tool-separate-voice-1"), fake_ctx)

    assert isinstance(result, VodTaskOutput)
    assert result.status == "succeeded"
    voice = _tracks(result)["voice"]
    assert voice.persistence == "failed"
    assert voice.persistence_issue is not None
    assert voice.persistence_issue.code == "output_too_large"
    assert voice.persistence_issue.artifact_limit_bytes == 10_485_760
    assert voice.source_url is not None


async def test_poll_failed_returns_error(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = SeparateVoiceTask(
        task_id="amk-tool-separate-voice-1",
        status="failed",
        provider_status="failed",
        failure_code="DownloadFailed",
        failure_message="Failed to download the source file.",
        finished_at="2026-06-02T07:36:37+00:00",
    )
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "get", AsyncMock(return_value=task))
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "close", _close)

    result = await vod_get_task(_poll_input(task_id="amk-tool-separate-voice-1"), fake_ctx)

    assert isinstance(result, VodTaskOutput)
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "DownloadFailed"
    assert result.outputs == []


async def test_poll_provider_error_returns_mcp_error(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = ProviderError(
        NormalizedProviderError(
            provider="byteplus-vod-mediakit",
            operation="get_audio_separation",
            http_status=429,
            code="RATE_LIMITED",
            message="Try later.",
            retryable=True,
        )
    )
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "get", AsyncMock(side_effect=error))
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "close", _close)

    result = await vod_get_task(_poll_input(task_id="amk-tool-separate-voice-1"), fake_ctx)

    assert isinstance(result, ToolResult)
    assert result.is_error is True
