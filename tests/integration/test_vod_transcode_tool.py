"""Integration tests for the VOD AI MediaKit transcode MCP tools."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastmcp.tools import ToolResult

from ark_mcp.artifacts.store import ArtifactPersistenceError
from ark_mcp.domain.artifacts import ArtifactRef
from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.providers.vod_mediakit.schemas import TranscodeSubmission, TranscodeTask
from ark_mcp.providers.vod_mediakit.transcode import VodMediaKitTranscodeService
from ark_mcp.tools.vod_get_task import VodGetTaskInput, VodTaskOutput, vod_get_task
from ark_mcp.tools.vod_transcode_video import (
    VodTranscodeVideoInput,
    VodTranscodeVideoOutput,
    vod_transcode_video,
)
from tests.fixtures.fake_context import FakeContext
from tests.fixtures.ownership import owned_task_ids


async def _close(_self: VodMediaKitTranscodeService) -> None:
    return None


def _submission() -> TranscodeSubmission:
    return TranscodeSubmission(
        status="accepted",
        request_id="body-1",
        provider_log_id="log-1",
        task_id="amk-tool-transcode-video-1",
    )


def _poll_input(**kwargs: object) -> VodGetTaskInput:
    return VodGetTaskInput(operation="transcode", **kwargs)  # type: ignore[arg-type]


def _succeeded_task() -> TranscodeTask:
    return TranscodeTask(
        task_id="amk-tool-transcode-video-1",
        status="succeeded",
        provider_status="completed",
        request_id="req-get",
        output_url="https://tos-ap-southeast.bytepluses.com/transcoded.mp4",
        duration_seconds=15.07,
        resolution="720p",
        video_codec="h264",
        created_at="2026-06-02T07:36:15+00:00",
        finished_at="2026-06-02T07:36:37+00:00",
        source_expires_at="2026-06-03T07:36:36+00:00",
    )


async def test_transcode_submit_accepts_and_records_ownership(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    async def submit(_self: VodMediaKitTranscodeService, request: object) -> TranscodeSubmission:
        captured.append(request)
        return _submission()

    monkeypatch.setattr(VodMediaKitTranscodeService, "submit", submit)
    monkeypatch.setattr(VodMediaKitTranscodeService, "close", _close)
    runtime = fake_ctx.lifespan_context["runtime"]

    result = await vod_transcode_video(
        VodTranscodeVideoInput(video_url="https://example.com/portrait.mp4"), fake_ctx
    )

    assert isinstance(result, VodTranscodeVideoOutput)
    assert result.status == "accepted"
    assert result.task_id == "amk-tool-transcode-video-1"
    assert result.request_id == "body-1"
    assert result.provider_log_id == "log-1"
    assert result.recommended_poll_after_ms == 3000
    assert result.operation == "transcode"
    assert captured
    assert await owned_task_ids(runtime, "vod-mediakit") == {"amk-tool-transcode-video-1"}


async def test_blocked_source_url_returns_sanitized_error(
    test_env: None,
    fake_ctx: FakeContext,
) -> None:
    result = await vod_transcode_video(
        VodTranscodeVideoInput(video_url="https://10.0.0.1/input.mp4"), fake_ctx
    )
    assert isinstance(result, ToolResult)
    assert result.is_error
    assert "Invalid media URL." in result.content[0].text
    assert "10.0.0.1" not in result.content[0].text
    assert "resolves to blocked" not in result.content[0].text


async def test_transcode_submit_default_options_are_verified_profile(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    async def submit(_self: VodMediaKitTranscodeService, request: object) -> TranscodeSubmission:
        captured.append(request.model_dump(mode="json"))  # type: ignore[attr-defined]
        return _submission()

    monkeypatch.setattr(VodMediaKitTranscodeService, "submit", submit)
    monkeypatch.setattr(VodMediaKitTranscodeService, "close", _close)

    await vod_transcode_video(
        VodTranscodeVideoInput(video_url="https://example.com/portrait.mp4"), fake_ctx
    )

    request = captured[0]
    video = request["video"]
    assert video["scale_type"] == 2
    assert video["scale_mode"] == 2
    assert video["scale_width"] == 720
    assert video["scale_height"] == 720
    assert request["container_format"] == "MP4"


async def test_transcode_submit_provider_error_returns_mcp_error(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = ProviderError(
        NormalizedProviderError(
            provider="byteplus-vod-mediakit",
            operation="transcode_video",
            http_status=429,
            code="RATE_LIMITED",
            message="Try later.",
            retryable=True,
        )
    )
    monkeypatch.setattr(VodMediaKitTranscodeService, "submit", AsyncMock(side_effect=error))
    monkeypatch.setattr(VodMediaKitTranscodeService, "close", _close)

    result = await vod_transcode_video(
        VodTranscodeVideoInput(video_url="https://example.com/portrait.mp4"), fake_ctx
    )

    assert isinstance(result, ToolResult)
    assert result.is_error is True


async def test_transcode_submit_missing_credential_raises(
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ark_mcp.config.env import Settings

    settings = Settings(_env_file=None, BYTEPLUS_VOD_MEDIAKIT_API_KEY="")
    assert settings.has_vod_mediakit is False

    runtime = fake_ctx.lifespan_context["runtime"]
    monkeypatch.setattr(runtime, "settings", settings)
    with pytest.raises(ValueError):
        await vod_transcode_video(
            VodTranscodeVideoInput(video_url="https://example.com/portrait.mp4"), fake_ctx
        )


def test_transcode_video_options_validation() -> None:
    from ark_mcp.tools.vod_transcode_video import VodTranscodeVideoOptions

    # scale_type=2 with no dimensions auto-fills the verified 720 profile.
    opts = VodTranscodeVideoOptions(scale_type=2, scale_mode=2)
    assert opts.scale_width == 720
    assert opts.scale_height == 720
    with pytest.raises(ValueError):
        VodTranscodeVideoOptions(scale_type=0, scale_width=720)
    with pytest.raises(ValueError):
        VodTranscodeVideoOptions(scale_type=1, scale_mode=2)
    opts = VodTranscodeVideoOptions(scale_type=1, scale_short=720, scale_long=1280)
    assert opts.scale_short == 720


async def test_poll_processing_returns_processing(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = TranscodeTask(
        task_id="amk-tool-transcode-video-1",
        status="processing",
        provider_status="running",
        request_id="req-1",
        created_at="2026-06-02T07:36:15+00:00",
    )
    monkeypatch.setattr(VodMediaKitTranscodeService, "get", AsyncMock(return_value=task))
    monkeypatch.setattr(VodMediaKitTranscodeService, "close", _close)

    result = await vod_get_task(_poll_input(task_id="amk-tool-transcode-video-1"), fake_ctx)

    assert isinstance(result, VodTaskOutput)
    assert result.status == "processing"
    assert result.provider_status == "running"
    assert result.operation == "transcode"
    assert result.outputs == []


async def test_poll_succeeded_persists_artifact_once(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref = ArtifactRef(
        id="artifact-1",
        uri="seed-media://artifacts/artifact-1",
        media_type="video",
        mime_type="video/mp4",
        bytes=123,
        sha256="abc",
        created_at="2026-08-12T00:00:00Z",
    )
    monkeypatch.setattr(
        VodMediaKitTranscodeService, "get", AsyncMock(return_value=_succeeded_task())
    )
    monkeypatch.setattr(VodMediaKitTranscodeService, "close", _close)
    runtime = fake_ctx.lifespan_context["runtime"]
    copy = AsyncMock(return_value=ref)
    monkeypatch.setattr(runtime.artifact_store, "copy_from_trusted_url", copy)

    result = await vod_get_task(_poll_input(task_id="amk-tool-transcode-video-1"), fake_ctx)

    assert isinstance(result, VodTaskOutput)
    assert result.status == "succeeded"
    assert result.outputs[0].persistence == "persisted"
    assert result.outputs[0].artifact == ref
    assert (
        str(result.outputs[0].source_url)
        == "https://tos-ap-southeast.bytepluses.com/transcoded.mp4"
    )
    assert result.resolution == "720p"
    assert result.video_codec == "h264"
    copy.assert_awaited_once()

    # Second poll reuses the cache and does not re-download.
    result2 = await vod_get_task(_poll_input(task_id="amk-tool-transcode-video-1"), fake_ctx)
    assert isinstance(result2, VodTaskOutput)
    assert result2.outputs[0].persistence == "persisted"
    assert copy.await_count == 1


async def test_poll_succeeded_persist_false(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        VodMediaKitTranscodeService, "get", AsyncMock(return_value=_succeeded_task())
    )
    monkeypatch.setattr(VodMediaKitTranscodeService, "close", _close)
    runtime = fake_ctx.lifespan_context["runtime"]
    copy = AsyncMock()
    monkeypatch.setattr(runtime.artifact_store, "copy_from_trusted_url", copy)

    result = await vod_get_task(
        _poll_input(task_id="amk-tool-transcode-video-1", persist_output=False),
        fake_ctx,
    )

    assert isinstance(result, VodTaskOutput)
    assert result.status == "succeeded"
    assert result.outputs[0].persistence == "not_requested"
    assert result.outputs[0].artifact is None
    assert result.outputs[0].source_url is not None
    copy.assert_not_awaited()


async def test_poll_succeeded_persistence_failure_preserves_success(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        VodMediaKitTranscodeService, "get", AsyncMock(return_value=_succeeded_task())
    )
    monkeypatch.setattr(VodMediaKitTranscodeService, "close", _close)
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

    result = await vod_get_task(_poll_input(task_id="amk-tool-transcode-video-1"), fake_ctx)

    assert isinstance(result, VodTaskOutput)
    assert result.status == "succeeded"
    assert result.outputs[0].persistence == "failed"
    assert result.outputs[0].persistence_issue is not None
    assert result.outputs[0].persistence_issue.code == "output_too_large"
    assert result.outputs[0].persistence_issue.artifact_limit_bytes == 209_715_200
    assert result.outputs[0].source_url is not None


async def test_poll_succeeded_generic_storage_failure_preserves_success(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        VodMediaKitTranscodeService, "get", AsyncMock(return_value=_succeeded_task())
    )
    monkeypatch.setattr(VodMediaKitTranscodeService, "close", _close)
    runtime = fake_ctx.lifespan_context["runtime"]
    monkeypatch.setattr(
        runtime.artifact_store,
        "copy_from_trusted_url",
        AsyncMock(side_effect=RuntimeError("backend detail")),
    )

    result = await vod_get_task(_poll_input(task_id="amk-tool-transcode-video-1"), fake_ctx)

    assert isinstance(result, VodTaskOutput)
    assert result.status == "succeeded"
    assert result.outputs[0].persistence == "failed"
    assert result.outputs[0].persistence_issue is not None
    assert result.outputs[0].persistence_issue.code == "storage_failed"
    assert "backend detail" not in result.outputs[0].persistence_issue.message
    assert result.outputs[0].source_url is not None


async def test_poll_failed_returns_error(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = TranscodeTask(
        task_id="amk-tool-transcode-video-1",
        status="failed",
        provider_status="failed",
        failure_code="DownloadFailed",
        failure_message="Failed to download the source video.",
        finished_at="2026-06-02T07:36:37+00:00",
    )
    monkeypatch.setattr(VodMediaKitTranscodeService, "get", AsyncMock(return_value=task))
    monkeypatch.setattr(VodMediaKitTranscodeService, "close", _close)

    result = await vod_get_task(_poll_input(task_id="amk-tool-transcode-video-1"), fake_ctx)

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
            operation="get_transcode_task",
            http_status=429,
            code="RATE_LIMITED",
            message="Try later.",
            retryable=True,
        )
    )
    monkeypatch.setattr(VodMediaKitTranscodeService, "get", AsyncMock(side_effect=error))
    monkeypatch.setattr(VodMediaKitTranscodeService, "close", _close)

    result = await vod_get_task(_poll_input(task_id="amk-tool-transcode-video-1"), fake_ctx)

    assert isinstance(result, ToolResult)
    assert result.is_error is True
