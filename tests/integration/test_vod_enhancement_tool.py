"""Integration tests for the VOD AI MediaKit MCP tool."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastmcp.tools import ToolResult

from ark_mcp.artifacts.store import ArtifactPersistenceError
from ark_mcp.domain.artifacts import ArtifactRef
from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.providers.vod_mediakit.enhancement import VodMediaKitEnhancementService
from ark_mcp.providers.vod_mediakit.schemas import (
    EnhancementSubmission,
    EnhancementTask,
    VodMediaKitEnhancementRequest,
)
from ark_mcp.security.auth_context import AuthContext
from ark_mcp.tools.vod_enhance_video import (
    VodEnhanceVideoInput,
    VodEnhanceVideoOutput,
    vod_enhance_video,
)
from ark_mcp.tools.vod_get_enhancement_task import (
    VodEnhancementTaskOutput,
    VodGetEnhancementTaskInput,
    vod_get_enhancement_task,
)
from tests.fixtures.fake_context import FakeContext


async def _close(_self: VodMediaKitEnhancementService) -> None:
    return None


def _submission() -> EnhancementSubmission:
    return EnhancementSubmission(
        status="succeeded",
        request_id="log-1",
        provider_log_id="provider-log-1",
        task_id="task-1",
        output_url="https://tos-ap-southeast.bytepluses.com/enhanced.mp4",
        mime_type="video/mp4",
        expires_at="2026-08-13T00:00:00Z",
        output_size_bytes=123,
        provider_status="completed",
    )


def _succeeded_task() -> EnhancementTask:
    return EnhancementTask(
        task_id="amk-tool-enhance-video-1",
        status="succeeded",
        provider_status="completed",
        request_id="req-get",
        output_url="https://tos-ap-southeast.bytepluses.com/enhanced.mp4",
        duration_seconds=30.917,
        fps=24,
        resolution="4k",
        tool_version="professional",
        created_at="2026-09-08T13:39:47+00:00",
        finished_at="2026-09-08T13:54:16+00:00",
        source_expires_at="2026-09-09T13:54:15+00:00",
    )


async def test_success_without_persistence_preserves_provider_url(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []

    async def enhance(
        _self: VodMediaKitEnhancementService, request: object
    ) -> EnhancementSubmission:
        captured.append(request)
        return _submission()

    monkeypatch.setattr(VodMediaKitEnhancementService, "enhance", enhance)
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", _close)
    result = await vod_enhance_video(
        VodEnhanceVideoInput(video_url="https://example.com/input.mp4", persist=False), fake_ctx
    )

    assert isinstance(result, VodEnhanceVideoOutput)
    assert str(result.source_url) == "https://tos-ap-southeast.bytepluses.com/enhanced.mp4"
    assert result.persistence == "not_requested"
    assert result.video is None
    assert result.estimated_cost_usd is None
    assert captured


async def test_blocked_source_url_returns_sanitized_error(
    test_env: None,
    fake_ctx: FakeContext,
) -> None:
    result = await vod_enhance_video(
        VodEnhanceVideoInput(video_url="https://10.0.0.1/input.mp4"), fake_ctx
    )
    assert isinstance(result, ToolResult)
    assert result.is_error
    assert "Invalid media URL." in result.content[0].text
    assert "10.0.0.1" not in result.content[0].text
    assert "resolves to blocked" not in result.content[0].text


async def test_accepted_task_is_owned_and_not_persisted(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submission = EnhancementSubmission(
        status="accepted",
        request_id="request-1",
        provider_log_id="log-1",
        task_id="amk-tool-enhance-video-1",
    )
    monkeypatch.setattr(
        VodMediaKitEnhancementService, "enhance", AsyncMock(return_value=submission)
    )
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", _close)
    runtime = fake_ctx.lifespan_context["runtime"]
    copy = AsyncMock()
    monkeypatch.setattr(runtime.artifact_store, "copy_from_trusted_url", copy)

    result = await vod_enhance_video(
        VodEnhanceVideoInput(video_url="https://example.com/input.mp4"), fake_ctx
    )

    assert isinstance(result, VodEnhanceVideoOutput)
    assert result.status == "accepted"
    assert result.request_id == "request-1"
    assert result.provider_log_id == "log-1"
    assert result.task_id == "amk-tool-enhance-video-1"
    assert result.source_url is None
    assert result.persistence == "not_applicable"
    assert result.video is None
    assert await runtime.ownership_store.list_task_ids("vod-mediakit", AuthContext()) == {
        "amk-tool-enhance-video-1"
    }
    copy.assert_not_awaited()


async def test_successful_persistence_returns_artifact(
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
        VodMediaKitEnhancementService, "enhance", AsyncMock(return_value=_submission())
    )
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", _close)
    store = fake_ctx.lifespan_context["runtime"].artifact_store
    copy = AsyncMock(return_value=ref)
    monkeypatch.setattr(store, "copy_from_trusted_url", copy)

    result = await vod_enhance_video(
        VodEnhanceVideoInput(video_url="https://example.com/input.mp4"), fake_ctx
    )

    assert isinstance(result, VodEnhanceVideoOutput)
    assert result.persistence == "persisted"
    assert result.video == ref
    copy.assert_awaited_once()


async def test_persistence_failure_does_not_erase_provider_success(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        VodMediaKitEnhancementService, "enhance", AsyncMock(return_value=_submission())
    )
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", _close)
    store = fake_ctx.lifespan_context["runtime"].artifact_store
    monkeypatch.setattr(
        store,
        "copy_from_trusted_url",
        AsyncMock(
            side_effect=ArtifactPersistenceError(
                "output_too_large", "Output exceeds the artifact limit.", retryable=False
            )
        ),
    )

    result = await vod_enhance_video(
        VodEnhanceVideoInput(video_url="https://example.com/input.mp4"), fake_ctx
    )

    assert isinstance(result, VodEnhanceVideoOutput)
    assert result.status == "succeeded"
    assert result.persistence == "failed"
    assert result.persistence_issue is not None
    assert result.persistence_issue.code == "output_too_large"
    assert result.persistence_issue.artifact_limit_bytes == 209_715_200


async def test_unexpected_storage_failure_does_not_erase_provider_success(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        VodMediaKitEnhancementService, "enhance", AsyncMock(return_value=_submission())
    )
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", _close)
    store = fake_ctx.lifespan_context["runtime"].artifact_store
    monkeypatch.setattr(
        store, "copy_from_trusted_url", AsyncMock(side_effect=RuntimeError("backend detail"))
    )

    result = await vod_enhance_video(
        VodEnhanceVideoInput(video_url="https://example.com/input.mp4"), fake_ctx
    )

    assert isinstance(result, VodEnhanceVideoOutput)
    assert result.status == "succeeded"
    assert result.persistence == "failed"
    assert result.persistence_issue is not None
    assert result.persistence_issue.code == "storage_failed"
    assert "backend detail" not in result.persistence_issue.message


async def test_provider_error_returns_mcp_error(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = ProviderError(
        NormalizedProviderError(
            provider="byteplus-vod-mediakit",
            operation="enhance_video",
            http_status=429,
            code="RATE_LIMITED",
            message="Try later.",
            retryable=True,
        )
    )
    monkeypatch.setattr(VodMediaKitEnhancementService, "enhance", AsyncMock(side_effect=error))
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", _close)

    result = await vod_enhance_video(
        VodEnhanceVideoInput(video_url="https://example.com/input.mp4"), fake_ctx
    )

    assert isinstance(result, ToolResult)
    assert result.is_error is True


@pytest.mark.parametrize(
    "overrides",
    [
        {"tool_version": "fast", "resolution": "8k"},
        {"tool_version": "fast", "resolution": "6k"},
        {"tool_version": "fast", "resolution_limit": 2161},
        {"tool_version": "fast", "enhance_style": "natural"},
        {"resolution": "1080p", "resolution_limit": 1080},
        {"resolution_limit": 127},
        {"resolution_limit": 4321},
        {"resolution": "16k"},
        {"tool_version": "ultra"},
        {"bitrate": 9},
        {"bitrate": 150001},
        {"fps": 14},
        {"fps": 121},
        {"bitrate_level": "extreme"},
    ],
)
def test_input_rejects_unsupported_profile_values(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        VodEnhanceVideoInput(video_url="https://example.com/input.mp4", **overrides)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "overrides",
    [
        {"tool_version": "standard", "resolution": "1080p"},
        {"tool_version": "professional", "resolution": "8k"},
        {"tool_version": "standard", "resolution_limit": 4320, "enhance_style": "natural"},
        {"tool_version": "fast", "resolution": "4k"},
        {"tool_version": "fast", "resolution": "720p", "bitrate": 8000, "fps": 30},
        {"tool_version": "fast", "resolution_limit": 2160, "fps": None},
    ],
)
def test_input_accepts_documented_profiles(overrides: dict[str, object]) -> None:
    VodEnhanceVideoInput(video_url="https://example.com/input.mp4", **overrides)  # type: ignore[arg-type]


async def test_selected_tier_and_resolution_reach_the_provider_request(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[VodMediaKitEnhancementRequest] = []

    async def enhance(
        _self: VodMediaKitEnhancementService, request: VodMediaKitEnhancementRequest
    ) -> EnhancementSubmission:
        captured.append(request)
        return EnhancementSubmission(status="accepted", task_id="amk-tool-enhance-video-fast-1")

    monkeypatch.setattr(VodMediaKitEnhancementService, "enhance", enhance)
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", _close)

    result = await vod_enhance_video(
        VodEnhanceVideoInput(
            video_url="https://example.com/input.mp4",
            tool_version="fast",
            resolution="720p",
            bitrate_level="medium",
            fps=None,
        ),
        fake_ctx,
    )

    assert isinstance(result, VodEnhanceVideoOutput)
    assert result.status == "accepted"
    assert captured[0].is_fast
    assert captured[0].resolution == "720p"
    assert captured[0].bitrate_level == "medium"
    assert captured[0].fps is None


def test_default_input_keeps_previous_professional_4k_profile() -> None:
    request = VodMediaKitEnhancementRequest.model_validate(
        VodEnhanceVideoInput(video_url="https://example.com/input.mp4").model_dump(
            exclude={"persist", "input_duration_seconds"}
        )
    )

    assert request.tool_version == "professional"
    assert request.resolution == "4k"
    assert request.bitrate_level == "high"
    assert request.fps == 24


async def test_poll_enhancement_succeeded_persists_once(
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
        created_at="2026-09-08T14:00:00Z",
    )
    monkeypatch.setattr(
        VodMediaKitEnhancementService, "get", AsyncMock(return_value=_succeeded_task())
    )
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", _close)
    runtime = fake_ctx.lifespan_context["runtime"]
    await runtime.ownership_store.record("vod-mediakit", "amk-tool-enhance-video-1", AuthContext())
    copy = AsyncMock(return_value=ref)
    monkeypatch.setattr(runtime.artifact_store, "copy_from_trusted_url", copy)

    result = await vod_get_enhancement_task(
        VodGetEnhancementTaskInput(task_id="amk-tool-enhance-video-1"), fake_ctx
    )
    result_again = await vod_get_enhancement_task(
        VodGetEnhancementTaskInput(task_id="amk-tool-enhance-video-1"), fake_ctx
    )

    assert isinstance(result, VodEnhancementTaskOutput)
    assert result.status == "succeeded"
    assert result.video == ref
    assert result.persistence == "persisted"
    assert result.duration_seconds == 30.917
    assert result.fps == 24
    assert result.resolution == "4k"
    assert result.tool_version == "professional"
    assert result_again.video == ref
    assert copy.await_count == 1


async def test_concurrent_first_polls_persist_one_shared_artifact(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref = ArtifactRef(
        id="artifact-concurrent",
        uri="seed-media://artifacts/artifact-concurrent",
        media_type="video",
        mime_type="video/mp4",
        bytes=123,
        sha256="abc",
        created_at="2026-09-08T14:00:00Z",
    )
    get_call_count = 0
    both_gets_started = asyncio.Event()

    async def get_task(_self: VodMediaKitEnhancementService, _task_id: str) -> EnhancementTask:
        nonlocal get_call_count
        get_call_count += 1
        if get_call_count == 2:
            both_gets_started.set()
        await both_gets_started.wait()
        return _succeeded_task()

    monkeypatch.setattr(VodMediaKitEnhancementService, "get", get_task)
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", _close)
    runtime = fake_ctx.lifespan_context["runtime"]
    await runtime.ownership_store.record("vod-mediakit", "amk-tool-enhance-video-1", AuthContext())
    copy_started = asyncio.Event()
    release_copy = asyncio.Event()
    copy_count = 0

    async def copy_from_trusted_url(**_kwargs: object) -> ArtifactRef:
        nonlocal copy_count
        copy_count += 1
        copy_started.set()
        await release_copy.wait()
        return ref

    monkeypatch.setattr(runtime.artifact_store, "copy_from_trusted_url", copy_from_trusted_url)
    monkeypatch.setattr(
        runtime.task_artifact_cache,
        "set",
        AsyncMock(side_effect=RuntimeError("cache unavailable")),
    )

    first = asyncio.create_task(
        vod_get_enhancement_task(
            VodGetEnhancementTaskInput(task_id="amk-tool-enhance-video-1"), fake_ctx
        )
    )
    second = asyncio.create_task(
        vod_get_enhancement_task(
            VodGetEnhancementTaskInput(task_id="amk-tool-enhance-video-1"), fake_ctx
        )
    )
    await asyncio.wait_for(copy_started.wait(), timeout=1)
    await asyncio.sleep(0)
    release_copy.set()
    results = await asyncio.gather(first, second)

    assert copy_count == 1
    first_result, second_result = results
    assert isinstance(first_result, VodEnhancementTaskOutput)
    assert isinstance(second_result, VodEnhancementTaskOutput)
    assert first_result.video == ref
    assert second_result.video == ref
    assert any("artifact cache update failed" in message for message in fake_ctx.messages)


async def test_poll_enhancement_can_skip_persistence(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        VodMediaKitEnhancementService, "get", AsyncMock(return_value=_succeeded_task())
    )
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", _close)
    runtime = fake_ctx.lifespan_context["runtime"]
    await runtime.ownership_store.record("vod-mediakit", "amk-tool-enhance-video-1", AuthContext())
    copy = AsyncMock()
    monkeypatch.setattr(runtime.artifact_store, "copy_from_trusted_url", copy)

    result = await vod_get_enhancement_task(
        VodGetEnhancementTaskInput(
            task_id="amk-tool-enhance-video-1",
            persist_output=False,
        ),
        fake_ctx,
    )

    assert isinstance(result, VodEnhancementTaskOutput)
    assert result.status == "succeeded"
    assert result.persistence == "not_requested"
    assert result.video is None
    assert str(result.source_url) == "https://tos-ap-southeast.bytepluses.com/enhanced.mp4"
    copy.assert_not_awaited()


async def test_poll_enhancement_persistence_failure_preserves_success(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        VodMediaKitEnhancementService, "get", AsyncMock(return_value=_succeeded_task())
    )
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", _close)
    runtime = fake_ctx.lifespan_context["runtime"]
    await runtime.ownership_store.record("vod-mediakit", "amk-tool-enhance-video-1", AuthContext())
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

    result = await vod_get_enhancement_task(
        VodGetEnhancementTaskInput(task_id="amk-tool-enhance-video-1"), fake_ctx
    )

    assert isinstance(result, VodEnhancementTaskOutput)
    assert result.status == "succeeded"
    assert result.persistence == "failed"
    assert result.persistence_issue is not None
    assert result.persistence_issue.code == "output_too_large"
    assert result.video is None
    assert any(message.startswith("WARNING:") for message in fake_ctx.messages)


async def test_poll_enhancement_cache_lookup_failure_preserves_success(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref = ArtifactRef(
        id="artifact-cache-read",
        uri="seed-media://artifacts/artifact-cache-read",
        media_type="video",
        mime_type="video/mp4",
        bytes=123,
        sha256="abc",
        created_at="2026-09-08T14:00:00Z",
    )
    monkeypatch.setattr(
        VodMediaKitEnhancementService, "get", AsyncMock(return_value=_succeeded_task())
    )
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", _close)
    runtime = fake_ctx.lifespan_context["runtime"]
    await runtime.ownership_store.record("vod-mediakit", "amk-tool-enhance-video-1", AuthContext())
    monkeypatch.setattr(
        runtime.task_artifact_cache,
        "get",
        AsyncMock(side_effect=RuntimeError("https://private.example.com/?token=secret")),
    )
    monkeypatch.setattr(
        runtime.artifact_store,
        "copy_from_trusted_url",
        AsyncMock(return_value=ref),
    )

    result = await vod_get_enhancement_task(
        VodGetEnhancementTaskInput(task_id="amk-tool-enhance-video-1"), fake_ctx
    )

    assert isinstance(result, VodEnhancementTaskOutput)
    assert result.status == "succeeded"
    assert result.persistence == "persisted"
    assert result.video == ref
    messages = "\n".join(fake_ctx.messages)
    assert "artifact cache lookup failed" in messages
    assert "private.example.com" not in messages
    assert "token=secret" not in messages


async def test_poll_enhancement_cache_update_failure_returns_created_artifact(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ref = ArtifactRef(
        id="artifact-cache-write",
        uri="seed-media://artifacts/artifact-cache-write",
        media_type="video",
        mime_type="video/mp4",
        bytes=123,
        sha256="abc",
        created_at="2026-09-08T14:00:00Z",
    )
    monkeypatch.setattr(
        VodMediaKitEnhancementService, "get", AsyncMock(return_value=_succeeded_task())
    )
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", _close)
    runtime = fake_ctx.lifespan_context["runtime"]
    await runtime.ownership_store.record("vod-mediakit", "amk-tool-enhance-video-1", AuthContext())
    monkeypatch.setattr(
        runtime.artifact_store,
        "copy_from_trusted_url",
        AsyncMock(return_value=ref),
    )
    monkeypatch.setattr(
        runtime.task_artifact_cache,
        "set",
        AsyncMock(side_effect=RuntimeError("https://private.example.com/?token=secret")),
    )

    result = await vod_get_enhancement_task(
        VodGetEnhancementTaskInput(task_id="amk-tool-enhance-video-1"), fake_ctx
    )

    assert isinstance(result, VodEnhancementTaskOutput)
    assert result.status == "succeeded"
    assert result.persistence == "persisted"
    assert result.video == ref
    messages = "\n".join(fake_ctx.messages)
    assert "artifact cache update failed" in messages
    assert "private.example.com" not in messages
    assert "token=secret" not in messages


async def test_poll_enhancement_processing_has_no_output(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = EnhancementTask(
        task_id="amk-tool-enhance-video-1",
        status="processing",
        provider_status="running",
    )
    monkeypatch.setattr(VodMediaKitEnhancementService, "get", AsyncMock(return_value=task))
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", _close)

    result = await vod_get_enhancement_task(
        VodGetEnhancementTaskInput(task_id="amk-tool-enhance-video-1"), fake_ctx
    )

    assert isinstance(result, VodEnhancementTaskOutput)
    assert result.status == "processing"
    assert result.source_url is None
    assert result.persistence == "not_applicable"


async def test_poll_enhancement_failed_returns_safe_error(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = EnhancementTask(
        task_id="amk-tool-enhance-video-1",
        status="failed",
        provider_status="failed",
        failure_code="DownloadFailed",
        failure_message="Failed to download the source video.",
    )
    monkeypatch.setattr(VodMediaKitEnhancementService, "get", AsyncMock(return_value=task))
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", _close)

    result = await vod_get_enhancement_task(
        VodGetEnhancementTaskInput(task_id="amk-tool-enhance-video-1"), fake_ctx
    )

    assert isinstance(result, VodEnhancementTaskOutput)
    assert result.status == "failed"
    assert result.error is not None
    assert result.error.code == "DownloadFailed"
    assert result.persistence == "not_applicable"


async def test_poll_enhancement_provider_error_returns_mcp_error(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = ProviderError(
        NormalizedProviderError(
            provider="byteplus-vod-mediakit",
            operation="get_enhancement_task",
            http_status=429,
            code="RATE_LIMITED",
            message="Try later.",
            retryable=False,
        )
    )
    monkeypatch.setattr(VodMediaKitEnhancementService, "get", AsyncMock(side_effect=error))
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", _close)

    result = await vod_get_enhancement_task(
        VodGetEnhancementTaskInput(task_id="amk-tool-enhance-video-1"), fake_ctx
    )

    assert isinstance(result, ToolResult)
    assert result.is_error is True
    assert "RATE_LIMITED" in result.content[0].text
