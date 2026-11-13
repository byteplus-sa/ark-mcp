"""Integration tests for VOD AI MediaKit subtitle tools."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastmcp.tools import ToolResult

from ark_mcp.artifacts.store import ArtifactPersistenceError
from ark_mcp.domain.artifacts import ArtifactRef
from ark_mcp.providers.vod_mediakit.schemas import SubtitleSubmission, SubtitleTask
from ark_mcp.providers.vod_mediakit.subtitles import (
    VodMediaKitSubtitleBurnInService,
    VodMediaKitSubtitleRemovalService,
)
from ark_mcp.security.auth_context import AuthContext
from ark_mcp.tools.vod_add_subtitles import VodAddSubtitlesInput, vod_add_subtitles
from ark_mcp.tools.vod_get_task import VodGetTaskInput, VodTaskOutput, vod_get_task
from ark_mcp.tools.vod_remove_subtitles import (
    VodRemoveSubtitlesInput,
    vod_remove_subtitles,
)
from tests.fixtures.fake_context import FakeContext
from tests.fixtures.ownership import owned_task_ids


async def _close(_self: object) -> None:
    return None


def _submission(task_id: str) -> SubtitleSubmission:
    return SubtitleSubmission(
        status="accepted",
        request_id="req-1",
        provider_log_id="log-1",
        task_id=task_id,
    )


def _succeeded_task(task_id: str) -> SubtitleTask:
    return SubtitleTask(
        task_id=task_id,
        status="succeeded",
        provider_status="completed",
        request_id="req-get",
        output_url="https://tos-ap-southeast.bytepluses.com/subtitle-output.mp4",
        duration_seconds=15.5,
        resolution="1080p",
        created_at="2026-06-02T07:36:15+00:00",
        finished_at="2026-06-02T07:36:37+00:00",
        source_expires_at="2026-06-03T07:36:36+00:00",
    )


def _artifact() -> ArtifactRef:
    return ArtifactRef(
        id="artifact-1",
        uri="seed-media://artifacts/artifact-1",
        media_type="video",
        mime_type="video/mp4",
        bytes=123,
        sha256="abc",
        created_at="2026-09-09T00:00:00Z",
    )


async def test_add_subtitles_maps_request_and_records_ownership(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    async def submit(_self: object, request: object) -> SubtitleSubmission:
        captured.append(request.model_dump(mode="json", by_alias=True, exclude_none=True))  # type: ignore[attr-defined]
        return _submission("amk-tool-add-subtitle-to-video-1")

    monkeypatch.setattr(VodMediaKitSubtitleBurnInService, "submit", submit)
    monkeypatch.setattr(VodMediaKitSubtitleBurnInService, "close", _close)
    runtime = fake_ctx.lifespan_context["runtime"]

    result = await vod_add_subtitles(
        VodAddSubtitlesInput(
            video_url="https://example.com/video.mp4",
            subtitles=[{"subtitle_text": "Hello 世界", "start_time": 0.5, "end_time": 3.0}],
            subtitle_font_type="zhanku_kuaile",
            project="default",
        ),
        fake_ctx,
    )

    assert result.task_id == "amk-tool-add-subtitle-to-video-1"
    assert result.operation == "add_subtitles"
    assert captured[0]["Project"] == "default"
    assert captured[0]["subtitles"] == [
        {"subtitle_text": "Hello 世界", "start_time": 0.5, "end_time": 3.0}
    ]
    assert await owned_task_ids(runtime, "vod-mediakit") == {"amk-tool-add-subtitle-to-video-1"}


async def test_add_subtitles_rejects_private_subtitle_url(
    test_env: None, fake_ctx: FakeContext
) -> None:
    result = await vod_add_subtitles(
        VodAddSubtitlesInput(
            video_url="https://example.com/video.mp4",
            subtitle_url="https://10.0.0.1/private.srt",
        ),
        fake_ctx,
    )

    assert isinstance(result, ToolResult)
    assert result.is_error
    assert "10.0.0.1" not in result.content[0].text


async def test_remove_subtitles_maps_ergonomic_enums(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, object]] = []

    async def submit(_self: object, request: object) -> SubtitleSubmission:
        captured.append(request.model_dump(mode="json", by_alias=True, exclude_none=True))  # type: ignore[attr-defined]
        return _submission("amk-tool-erase-video-subtitle-pro-1")

    monkeypatch.setattr(VodMediaKitSubtitleRemovalService, "submit", submit)
    monkeypatch.setattr(VodMediaKitSubtitleRemovalService, "close", _close)

    result = await vod_remove_subtitles(
        VodRemoveSubtitlesInput(
            video_url="https://example.com/video.mp4",
            mode="text",
            output_encode_mode="size",
            model_version="v5",
            project="default",
        ),
        fake_ctx,
    )

    assert result.task_id == "amk-tool-erase-video-subtitle-pro-1"
    assert result.recommended_poll_after_ms == 15000
    assert result.operation == "remove_subtitles"
    assert captured[0]["mode"] == "Text"
    assert captured[0]["output_encode_mode"] == "Size"
    assert captured[0]["Project"] == "default"


async def test_poll_addition_persists_once_across_concurrent_calls(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "amk-tool-add-subtitle-to-video-1"
    runtime = fake_ctx.lifespan_context["runtime"]
    await runtime.ownership_store.record("vod-mediakit", task_id, AuthContext())
    monkeypatch.setattr(
        VodMediaKitSubtitleBurnInService,
        "get",
        AsyncMock(return_value=_succeeded_task(task_id)),
    )
    monkeypatch.setattr(VodMediaKitSubtitleBurnInService, "close", _close)
    copy = AsyncMock(return_value=_artifact())
    monkeypatch.setattr(runtime.artifact_store, "copy_from_trusted_url", copy)

    results = await asyncio.gather(
        vod_get_task(
            VodGetTaskInput(task_id=task_id, operation="add_subtitles", persist_output=True),
            fake_ctx,
        ),
        vod_get_task(
            VodGetTaskInput(task_id=task_id, operation="add_subtitles", persist_output=True),
            fake_ctx,
        ),
    )

    assert all(isinstance(result, VodTaskOutput) for result in results)
    assert all(result.outputs[0].persistence == "persisted" for result in results)
    assert all(result.outputs[0].artifact == _artifact() for result in results)
    assert all(result.resolution == "1080p" for result in results)
    assert copy.await_count == 1


async def test_poll_removal_processing_and_failed(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "amk-tool-erase-video-subtitle-pro-1"
    runtime = fake_ctx.lifespan_context["runtime"]
    await runtime.ownership_store.record("vod-mediakit", task_id, AuthContext())
    get = AsyncMock(
        side_effect=[
            SubtitleTask(
                task_id=task_id,
                status="processing",
                provider_status="running",
            ),
            SubtitleTask(
                task_id=task_id,
                status="failed",
                provider_status="failed",
                failure_code="AbilityProcessingError",
                failure_message="MediaKit could not process the video.",
            ),
        ]
    )
    monkeypatch.setattr(VodMediaKitSubtitleRemovalService, "get", get)
    monkeypatch.setattr(VodMediaKitSubtitleRemovalService, "close", _close)

    processing = await vod_get_task(
        VodGetTaskInput(task_id=task_id, operation="remove_subtitles", persist_output=True),
        fake_ctx,
    )
    failed = await vod_get_task(
        VodGetTaskInput(task_id=task_id, operation="remove_subtitles", persist_output=True),
        fake_ctx,
    )

    assert isinstance(processing, VodTaskOutput)
    assert processing.status == "processing"
    assert processing.outputs == []
    assert isinstance(failed, VodTaskOutput)
    assert failed.status == "failed"
    assert failed.error is not None
    assert failed.error.code == "AbilityProcessingError"
    assert failed.outputs == []


async def test_poll_persistence_failure_preserves_provider_success(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "amk-tool-add-subtitle-to-video-2"
    runtime = fake_ctx.lifespan_context["runtime"]
    await runtime.ownership_store.record("vod-mediakit", task_id, AuthContext())
    monkeypatch.setattr(
        VodMediaKitSubtitleBurnInService,
        "get",
        AsyncMock(return_value=_succeeded_task(task_id)),
    )
    monkeypatch.setattr(VodMediaKitSubtitleBurnInService, "close", _close)
    monkeypatch.setattr(
        runtime.artifact_store,
        "copy_from_trusted_url",
        AsyncMock(
            side_effect=ArtifactPersistenceError(
                "output_too_large",
                "Output exceeds the artifact limit.",
                retryable=False,
            )
        ),
    )

    result = await vod_get_task(
        VodGetTaskInput(task_id=task_id, operation="add_subtitles", persist_output=True), fake_ctx
    )

    assert isinstance(result, VodTaskOutput)
    assert result.status == "succeeded"
    assert result.outputs[0].source_url is not None
    assert result.outputs[0].persistence == "failed"
    assert result.outputs[0].persistence_issue is not None
    assert result.outputs[0].persistence_issue.code == "output_too_large"


async def test_poll_can_skip_persistence(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task_id = "amk-tool-add-subtitle-to-video-3"
    runtime = fake_ctx.lifespan_context["runtime"]
    await runtime.ownership_store.record("vod-mediakit", task_id, AuthContext())
    monkeypatch.setattr(
        VodMediaKitSubtitleBurnInService,
        "get",
        AsyncMock(return_value=_succeeded_task(task_id)),
    )
    monkeypatch.setattr(VodMediaKitSubtitleBurnInService, "close", _close)
    copy = AsyncMock()
    monkeypatch.setattr(runtime.artifact_store, "copy_from_trusted_url", copy)

    result = await vod_get_task(
        VodGetTaskInput(task_id=task_id, operation="add_subtitles", persist_output=False), fake_ctx
    )

    assert isinstance(result, VodTaskOutput)
    assert result.outputs[0].persistence == "not_requested"
    assert result.outputs[0].artifact is None
    copy.assert_not_awaited()
