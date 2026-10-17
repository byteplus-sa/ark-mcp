"""Cross-operation tests for ``vod_get_task``: routing, guards, export, and invariants."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from fastmcp.tools import ToolResult
from pydantic import ValidationError

from ark_mcp.config.env import get_settings
from ark_mcp.domain.artifacts import ArtifactPersistenceIssue, ArtifactRef, MediaType
from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.domain.models import TaskError, TaskOutputFile
from ark_mcp.providers.vod_mediakit.enhancement import VodMediaKitEnhancementService
from ark_mcp.providers.vod_mediakit.schemas import (
    EnhancementTask,
    SeparateVoiceTask,
    SubtitleTask,
    TranscodeTask,
)
from ark_mcp.providers.vod_mediakit.separate_voice import VodMediaKitSeparateVoiceService
from ark_mcp.providers.vod_mediakit.subtitles import (
    VodMediaKitSubtitleBurnInService,
    VodMediaKitSubtitleRemovalService,
)
from ark_mcp.providers.vod_mediakit.transcode import VodMediaKitTranscodeService
from ark_mcp.security.auth_context import AuthContext
from ark_mcp.security.output_paths import OutputPathError
from ark_mcp.tools import vod_get_task as vod_get_task_module
from ark_mcp.tools.vod_get_task import VodGetTaskInput, VodTaskOutput, vod_get_task
from tests.fixtures.fake_context import FakeContext

TASK_ID = "amk-tool-task-1"
MEDIAKIT_BASE_URL = "https://mediakit.test.example.com/api/v1"
VIDEO_URL = "https://tos-ap-southeast.bytepluses.com/output.mp4"

_SERVICES: dict[str, type[Any]] = {
    "enhance": VodMediaKitEnhancementService,
    "transcode": VodMediaKitTranscodeService,
    "separate_audio": VodMediaKitSeparateVoiceService,
    "add_subtitles": VodMediaKitSubtitleBurnInService,
    "remove_subtitles": VodMediaKitSubtitleRemovalService,
}


async def _close(_self: object) -> None:
    return None


def _succeeded_task(operation: str) -> Any:
    common = {
        "task_id": TASK_ID,
        "status": "succeeded",
        "provider_status": "completed",
        "source_expires_at": "2026-06-03T07:36:36+00:00",
    }
    if operation == "enhance":
        return EnhancementTask(**common, output_url=VIDEO_URL, fps=24, tool_version="fast")
    if operation == "transcode":
        return TranscodeTask(**common, output_url=VIDEO_URL, video_codec="h265")
    if operation == "separate_audio":
        return SeparateVoiceTask(
            **common,
            voice_url="https://vod.ap-southeast-1.byteplusvod.com/voice.wav",
            background_url="https://vod.ap-southeast-1.byteplusvod.com/background.wav",
            music_url="https://vod.ap-southeast-1.byteplusvod.com/music.mp3",
            sfx_url="https://vod.ap-southeast-1.byteplusvod.com/sfx.aac",
        )
    return SubtitleTask(**common, output_url=VIDEO_URL, resolution="1080p")


def _stub_services(monkeypatch: pytest.MonkeyPatch) -> dict[str, AsyncMock]:
    gets: dict[str, AsyncMock] = {}
    for name, service in _SERVICES.items():
        gets[name] = AsyncMock(return_value=_succeeded_task(name))
        monkeypatch.setattr(service, "get", gets[name])
        monkeypatch.setattr(service, "close", _close)
    return gets


def _artifact(media_type: str = "video") -> ArtifactRef:
    return ArtifactRef(
        id=f"artifact-{media_type}",
        uri=f"seed-media://artifacts/artifact-{media_type}",
        media_type=media_type,
        mime_type="video/mp4" if media_type == "video" else "audio/aac",
        bytes=3,
        sha256="abc",
        created_at="2026-09-09T00:00:00Z",
    )


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "exports"
    path.mkdir()
    monkeypatch.setenv("OUTPUT_ROOTS", str(path))
    get_settings.cache_clear()
    return path.resolve()


@pytest.mark.parametrize(
    ("operation", "roles", "detail"),
    [
        ("enhance", ["video"], {"fps": 24, "tool_version": "fast"}),
        ("transcode", ["video"], {"video_codec": "h265"}),
        ("separate_audio", ["voice", "background", "music", "sfx"], {}),
        ("add_subtitles", ["video"], {"resolution": "1080p"}),
        ("remove_subtitles", ["video"], {"resolution": "1080p"}),
    ],
)
async def test_operation_routes_to_its_own_provider_service(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    roles: list[str],
    detail: dict[str, object],
) -> None:
    gets = _stub_services(monkeypatch)

    result = await vod_get_task(
        VodGetTaskInput(task_id=TASK_ID, operation=operation, persist_output=False),  # type: ignore[arg-type]
        fake_ctx,
    )

    assert isinstance(result, VodTaskOutput)
    assert result.operation == operation
    assert result.status == "succeeded"
    assert [output.role for output in result.outputs] == roles
    assert all(output.persistence == "not_requested" for output in result.outputs)
    assert all(output.source_expires_at == "2026-06-03T07:36:36+00:00" for output in result.outputs)
    for field, value in detail.items():
        assert getattr(result, field) == value
    gets[operation].assert_awaited_once_with(TASK_ID)
    assert all(get.await_count == 0 for name, get in gets.items() if name != operation)


@respx.mock
async def test_mismatched_operation_explains_how_to_fix_it(
    test_env: None, fake_ctx: FakeContext
) -> None:
    respx.get(f"{MEDIAKIT_BASE_URL}/tasks/{TASK_ID}").mock(
        return_value=httpx.Response(
            200,
            json={
                "success": True,
                "task_id": TASK_ID,
                "task_type": "transcode-video",
                "status": "running",
            },
        )
    )

    result = await vod_get_task(
        VodGetTaskInput(task_id=TASK_ID, operation="enhance", persist_output=False), fake_ctx
    )

    assert isinstance(result, ToolResult)
    assert result.is_error is True
    text = result.content[0].text
    assert "code=INVALID_RESPONSE" in text
    assert "Check that operation ('enhance') matches the operation returned by the vod_*" in text


@pytest.mark.parametrize(
    ("http_status", "code"),
    [(200, "TaskNotFound"), (404, "HTTP_404"), (404, None), (400, "TASK_NOT_FOUND")],
)
async def test_provider_not_found_tells_caller_to_check_operation(
    test_env: None,
    fake_ctx: FakeContext,
    monkeypatch: pytest.MonkeyPatch,
    http_status: int,
    code: str | None,
) -> None:
    error = ProviderError(
        NormalizedProviderError(
            provider="byteplus-vod-mediakit",
            operation="get_transcode_task",
            http_status=http_status,
            code=code,
            message="Unknown task.",
            retryable=False,
        )
    )
    monkeypatch.setattr(VodMediaKitTranscodeService, "get", AsyncMock(side_effect=error))
    monkeypatch.setattr(VodMediaKitTranscodeService, "close", _close)

    result = await vod_get_task(
        VodGetTaskInput(task_id=TASK_ID, operation="transcode", persist_output=False), fake_ctx
    )

    assert isinstance(result, ToolResult)
    assert result.is_error is True
    text = result.content[0].text
    assert "Unknown task. Check that operation ('transcode') matches" in text
    assert any("Failed to retrieve transcode task" in message for message in fake_ctx.messages)


async def test_unrelated_provider_error_has_no_operation_hint(
    test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    error = ProviderError(
        NormalizedProviderError(
            provider="byteplus-vod-mediakit",
            operation="get_audio_separation",
            http_status=500,
            code="HTTP_500",
            message="MediaKit returned HTTP 500.",
            retryable=False,
            ambiguous_completion=False,
        )
    )
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "get", AsyncMock(side_effect=error))
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "close", _close)

    result = await vod_get_task(
        VodGetTaskInput(task_id=TASK_ID, operation="separate_audio", persist_output=False),
        fake_ctx,
    )

    assert isinstance(result, ToolResult)
    assert "HTTP_500" in result.content[0].text
    assert "Check that operation" not in result.content[0].text


async def test_task_owned_by_another_principal_is_rejected_before_provider_call(
    test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    gets = _stub_services(monkeypatch)
    runtime = fake_ctx.lifespan_context["runtime"]
    await runtime.ownership_store.record(
        "vod-mediakit", TASK_ID, AuthContext(principal_id="bob", tenant_id="t", transport="http")
    )
    monkeypatch.setattr(
        vod_get_task_module,
        "get_principal",
        lambda _ctx: AuthContext(principal_id="eve", tenant_id="t", transport="http"),
    )

    with pytest.raises(PermissionError):
        await vod_get_task(
            VodGetTaskInput(task_id=TASK_ID, operation="enhance", persist_output=False), fake_ctx
        )

    gets["enhance"].assert_not_awaited()


async def test_foreground_persistence_is_rejected_without_provider_call(
    test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    gets = _stub_services(monkeypatch)
    fake_ctx.task_id = None

    result = await vod_get_task(VodGetTaskInput(task_id=TASK_ID, operation="transcode"), fake_ctx)

    assert isinstance(result, ToolResult)
    assert result.is_error is True
    assert "persist_output=false" in result.content[0].text
    gets["transcode"].assert_not_awaited()


async def test_foreground_status_check_without_persistence_succeeds(
    test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_services(monkeypatch)
    fake_ctx.task_id = None

    result = await vod_get_task(
        VodGetTaskInput(task_id=TASK_ID, operation="transcode", persist_output=False), fake_ctx
    )

    assert isinstance(result, VodTaskOutput)
    assert result.status == "succeeded"
    assert fake_ctx.progress_reports == [(20, 100), (60, 100)]


async def test_separate_audio_output_path_writes_every_track(
    test_env: None, root: Path, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_services(monkeypatch)
    store = fake_ctx.lifespan_context["runtime"].artifact_store
    put_base64 = store.put_base64
    written: dict[str, bytes] = {}

    async def copy_from_trusted_url(*, url: str, mime_type: str, **kwargs: Any) -> ArtifactRef:
        data = url.encode()
        ref: ArtifactRef = await put_base64(
            base64.b64encode(data).decode(), MediaType.AUDIO, mime_type, auth=kwargs.get("auth")
        )
        written[ref.id] = data
        return ref

    monkeypatch.setattr(store, "copy_from_trusted_url", copy_from_trusted_url)
    target = root / "tracks"

    result = await vod_get_task(
        VodGetTaskInput(task_id=TASK_ID, operation="separate_audio", output_path=f"{target}/"),
        fake_ctx,
    )

    assert isinstance(result, VodTaskOutput)
    assert [output.role for output in result.outputs] == ["voice", "background", "music", "sfx"]
    for output in result.outputs:
        assert output.persistence == "persisted"
        assert output.artifact is not None
        assert output.artifact.export_error is None
        assert output.artifact.local_path is not None
        local = Path(output.artifact.local_path)
        assert local.parent == target
        assert local.read_bytes() == written[output.artifact.id]
    assert len({output.artifact.local_path for output in result.outputs if output.artifact}) == 4


async def test_video_output_path_writes_the_file(
    test_env: None, root: Path, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_services(monkeypatch)
    store = fake_ctx.lifespan_context["runtime"].artifact_store
    put_base64 = store.put_base64

    async def copy_from_trusted_url(*, mime_type: str, **kwargs: Any) -> ArtifactRef:
        ref: ArtifactRef = await put_base64(
            base64.b64encode(b"mp4").decode(), MediaType.VIDEO, mime_type, auth=kwargs.get("auth")
        )
        return ref

    monkeypatch.setattr(store, "copy_from_trusted_url", copy_from_trusted_url)
    target = root / "subtitled.mp4"

    result = await vod_get_task(
        VodGetTaskInput(task_id=TASK_ID, operation="add_subtitles", output_path=str(target)),
        fake_ctx,
    )

    assert isinstance(result, VodTaskOutput)
    artifact = result.outputs[0].artifact
    assert artifact is not None
    assert artifact.local_path == str(target)
    assert target.read_bytes() == b"mp4"


async def test_separate_audio_rejects_a_file_output_path_before_provider_call(
    test_env: None, root: Path, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    gets = _stub_services(monkeypatch)

    with pytest.raises(OutputPathError, match="directory"):
        await vod_get_task(
            VodGetTaskInput(
                task_id=TASK_ID, operation="separate_audio", output_path=str(root / "voice.wav")
            ),
            fake_ctx,
        )

    gets["separate_audio"].assert_not_awaited()


async def test_output_path_requires_persist_output(
    test_env: None, root: Path, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    gets = _stub_services(monkeypatch)

    with pytest.raises(ValueError, match="persist_output=true"):
        await vod_get_task(
            VodGetTaskInput(
                task_id=TASK_ID,
                operation="enhance",
                persist_output=False,
                output_path=str(root / "out.mp4"),
            ),
            fake_ctx,
        )

    gets["enhance"].assert_not_awaited()


async def test_processing_task_does_not_touch_the_artifact_store(
    test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    task = SeparateVoiceTask(task_id=TASK_ID, status="processing", provider_status="queued")
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "get", AsyncMock(return_value=task))
    monkeypatch.setattr(VodMediaKitSeparateVoiceService, "close", _close)
    runtime = fake_ctx.lifespan_context["runtime"]
    copy = AsyncMock()
    monkeypatch.setattr(runtime.artifact_store, "copy_from_trusted_url", copy)

    result = await vod_get_task(
        VodGetTaskInput(task_id=TASK_ID, operation="separate_audio"), fake_ctx
    )

    assert isinstance(result, VodTaskOutput)
    assert result.status == "processing"
    assert result.provider_status == "queued"
    assert result.outputs == []
    copy.assert_not_awaited()


async def test_partially_cached_tracks_only_copy_the_missing_ones(
    test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_services(monkeypatch)
    runtime = fake_ctx.lifespan_context["runtime"]
    cached_voice = _artifact("audio")
    await runtime.task_artifact_cache.set("vod-mediakit", TASK_ID, {"voice": cached_voice})
    copy = AsyncMock(return_value=_artifact("audio").model_copy(update={"id": "fresh"}))
    monkeypatch.setattr(runtime.artifact_store, "copy_from_trusted_url", copy)

    result = await vod_get_task(
        VodGetTaskInput(task_id=TASK_ID, operation="separate_audio"), fake_ctx
    )

    assert isinstance(result, VodTaskOutput)
    assert result.outputs[0].artifact == cached_voice
    assert copy.await_count == 3
    cached = await runtime.task_artifact_cache.get("vod-mediakit", TASK_ID)
    assert cached is not None
    assert set(cached) == {"voice", "background", "music", "sfx"}


def test_input_rejects_unknown_operation_and_unsafe_task_id() -> None:
    with pytest.raises(ValidationError):
        VodGetTaskInput(task_id=TASK_ID, operation="enhance_video")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        VodGetTaskInput(task_id="../tasks/other", operation="enhance")
    with pytest.raises(ValidationError):
        VodGetTaskInput(task_id="", operation="enhance")


def _output(**overrides: Any) -> TaskOutputFile:
    values: dict[str, Any] = {
        "role": "video",
        "source_url": VIDEO_URL,
        "persistence": "not_requested",
    }
    values.update(overrides)
    return TaskOutputFile(**values)


def _issue() -> ArtifactPersistenceIssue:
    return ArtifactPersistenceIssue(
        code="storage_failed", message="failed", retryable=True, artifact_limit_bytes=1
    )


@pytest.mark.parametrize(
    "values",
    [
        {"status": "processing", "outputs": []},
        {"status": "succeeded", "outputs": [_output()]},
        {
            "status": "succeeded",
            "outputs": [_output(persistence="persisted", artifact=_artifact())],
        },
        {
            "status": "succeeded",
            "outputs": [_output(persistence="failed", persistence_issue=_issue())],
        },
        {"status": "failed", "outputs": [], "error": TaskError(code="X", message="boom")},
    ],
)
def test_task_output_accepts_consistent_states(values: dict[str, Any]) -> None:
    VodTaskOutput(task_id=TASK_ID, operation="transcode", **values)


@pytest.mark.parametrize(
    "values",
    [
        {"status": "processing", "outputs": [_output()]},
        {"status": "processing", "outputs": [], "error": TaskError(message="boom")},
        {"status": "succeeded", "outputs": []},
        {"status": "succeeded", "outputs": [_output(source_url=None)]},
        {"status": "succeeded", "outputs": [_output()], "error": TaskError(message="boom")},
        {"status": "succeeded", "outputs": [_output(persistence="persisted")]},
        {"status": "succeeded", "outputs": [_output(persistence="failed")]},
        {"status": "succeeded", "outputs": [_output(artifact=_artifact())]},
        {"status": "failed", "outputs": []},
        {"status": "failed", "outputs": [_output()], "error": TaskError(message="boom")},
    ],
)
def test_task_output_rejects_inconsistent_states(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        VodTaskOutput(task_id=TASK_ID, operation="transcode", **values)
