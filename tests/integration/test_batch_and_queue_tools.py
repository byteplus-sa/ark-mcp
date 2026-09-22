"""media_upload_batch, seedance_get_tasks, and derived Seedance queue info."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ark_mcp.config.env import get_settings
from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.providers.modelark.schemas import SeedanceTaskListResponse, SeedanceTaskResponse
from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.providers.modelark.seedance_queue import queue_info
from ark_mcp.security.auth_context import AuthContext
from ark_mcp.security.safe_downloader import DownloadedMedia
from ark_mcp.tools.media_upload_batch import (
    MediaUploadBatchInput,
    MediaUploadBatchOutput,
    media_upload_batch,
)
from ark_mcp.tools.seedance_get_tasks import (
    SeedanceGetTasksInput,
    SeedanceGetTasksOutput,
    seedance_get_tasks,
)
from tests.fixtures.fake_context import FakeContext

VIDEO_URL = "https://tos-ap-southeast.bytepluses.com/video.mp4"


def _gateway(fail_keys: tuple[str, ...] = ()) -> AsyncMock:
    gw = AsyncMock()

    async def upload_bytes(*, key: str, data: bytes, mime_type: str) -> None:
        if any(marker in mime_type for marker in fail_keys):
            raise ProviderError(
                NormalizedProviderError(
                    provider="tos",
                    operation="upload",
                    code="SERVER_ERROR",
                    message="bucket unavailable",
                    retryable=False,
                )
            )

    gw.upload_bytes = AsyncMock(side_effect=upload_bytes)
    gw.upload_file = AsyncMock(return_value=None)
    gw.presign_get = AsyncMock(return_value="https://tos.example.com/presigned")
    gw.close = AsyncMock()
    return gw


# --- media_upload_batch --------------------------------------------------------


async def test_batch_uploads_items_and_reports_failures_per_item(
    test_env: None, fake_ctx: FakeContext, tmp_path: Path
) -> None:
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"video-bytes")
    gw = _gateway(fail_keys=("webp",))
    with patch("ark_mcp.tools.media_upload_batch.make_object_storage_gateway", return_value=gw):
        result = await media_upload_batch(
            MediaUploadBatchInput(
                items=[
                    {"media_type": "video", "file_path": str(clip)},
                    {
                        "media_type": "image",
                        "mime_type": "image/png",
                        "data": base64.b64encode(b"png").decode(),
                    },
                    {
                        "media_type": "image",
                        "mime_type": "image/webp",
                        "data": base64.b64encode(b"webp").decode(),
                    },
                    {"media_type": "image", "file_path": str(tmp_path / "missing.png")},
                ]
            ),
            fake_ctx,
        )
    assert isinstance(result, MediaUploadBatchOutput)
    assert (result.succeeded, result.failed) == (2, 2)
    first, second, third, fourth = result.items
    assert first.mime_type == "video/mp4"  # inferred from the extension
    assert first.url == "https://tos.example.com/presigned"
    assert second.object_key is not None
    assert third.error == "bucket unavailable"
    assert fourth.error == "File not found."
    gw.close.assert_called_once()


async def test_batch_rejects_total_above_cap_before_uploading(
    test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MEDIA_UPLOAD_BATCH_MAX_BYTES", "4")
    get_settings.cache_clear()
    gw = _gateway()
    with (
        patch("ark_mcp.tools.media_upload_batch.make_object_storage_gateway", return_value=gw),
        pytest.raises(ValueError, match="MEDIA_UPLOAD_BATCH_MAX_BYTES"),
    ):
        await media_upload_batch(
            MediaUploadBatchInput(
                items=[
                    {
                        "media_type": "image",
                        "mime_type": "image/png",
                        "data": base64.b64encode(b"more than four").decode(),
                    }
                ]
            ),
            fake_ctx,
        )
    gw.upload_bytes.assert_not_called()


# --- seedance_get_tasks --------------------------------------------------------


def _task(task_id: str, status: str, **extra: Any) -> SeedanceTaskResponse:
    return SeedanceTaskResponse(
        id=task_id,
        model="dreamina-seedance-2-0-260128",
        status=status,
        created_at=1721400000,
        updated_at=1721400100,
        **extra,
    )


async def _noop_close(self: SeedanceService) -> None:
    return None


async def test_get_tasks_uses_one_list_call_and_reports_counts(
    test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    listed = [
        _task("t-queued", "queued", service_tier="flex"),
        _task("t-running", "running"),
        _task("t-failed", "failed"),
    ]
    list_calls: list[dict[str, Any]] = []

    async def mock_list(self: SeedanceService, **kwargs: Any) -> Any:
        list_calls.append(kwargs)
        return SeedanceTaskListResponse(data=listed), "req"

    async def mock_get(self: SeedanceService, task_id: str) -> Any:
        raise AssertionError("get_task should not be needed")

    monkeypatch.setattr(SeedanceService, "list_tasks", mock_list)
    monkeypatch.setattr(SeedanceService, "get_task", mock_get)
    monkeypatch.setattr(SeedanceService, "close", _noop_close)

    result = await seedance_get_tasks(
        SeedanceGetTasksInput(task_ids=["t-queued", "t-running", "t-failed", "t-queued"]),
        fake_ctx,
    )
    assert isinstance(result, SeedanceGetTasksOutput)
    assert len(list_calls) == 1
    assert list_calls[0]["task_ids"] == ["t-queued", "t-running", "t-failed"]  # deduped
    assert [t.task_id for t in result.tasks] == ["t-queued", "t-running", "t-failed"]
    assert result.counts == {"queued": 1, "running": 1, "failed": 1}
    assert result.active_tasks == 2
    assert result.all_terminal is False
    queued = result.tasks[0].queue
    assert queued is not None
    assert queued.service_tier == "flex"
    assert queued.hint is not None and "flex" in queued.hint
    assert result.tasks[2].queue is None


async def test_get_tasks_refetches_and_persists_succeeded(
    test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    await fake_ctx.lifespan_context["runtime"].task_artifact_cache.clear()

    async def mock_list(self: SeedanceService, **kwargs: Any) -> Any:
        return SeedanceTaskListResponse(data=[_task("t-done", "succeeded")]), "req"

    async def mock_get(self: SeedanceService, task_id: str) -> Any:
        return _task(task_id, "succeeded", content={"video_url": VIDEO_URL}), "req"

    monkeypatch.setattr(SeedanceService, "list_tasks", mock_list)
    monkeypatch.setattr(SeedanceService, "get_task", mock_get)
    monkeypatch.setattr(SeedanceService, "close", _noop_close)

    download = AsyncMock(
        return_value=DownloadedMedia(body=b"mp4", content_type="video/mp4", final_url=VIDEO_URL)
    )
    with patch("ark_mcp.security.safe_downloader.SafeDownloader.download", new=download):
        fake_ctx.task_id = "task-1"
        result = await seedance_get_tasks(
            SeedanceGetTasksInput(task_ids=["t-done"], persist_output=True), fake_ctx
        )
        again = await seedance_get_tasks(
            SeedanceGetTasksInput(task_ids=["t-done"], persist_output=True), fake_ctx
        )
    assert isinstance(result, SeedanceGetTasksOutput)
    assert isinstance(again, SeedanceGetTasksOutput)
    video = result.tasks[0].video
    assert video is not None and video.uri.startswith("seed-media://")
    assert again.tasks[0].video == video
    assert download.await_count == 1  # cached, never downloaded twice
    assert result.all_terminal is True
    await fake_ctx.lifespan_context["runtime"].task_artifact_cache.clear()


async def test_get_tasks_reports_unowned_ids(
    test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = fake_ctx.lifespan_context["runtime"]
    await runtime.ownership_store.record(
        "modelark", "t-other", AuthContext(principal_id="bob", tenant_id="t")
    )

    async def mock_list(self: SeedanceService, **kwargs: Any) -> Any:
        return SeedanceTaskListResponse(data=[_task("t-mine", "queued")]), "req"

    monkeypatch.setattr(SeedanceService, "list_tasks", mock_list)
    monkeypatch.setattr(SeedanceService, "close", _noop_close)
    result = await seedance_get_tasks(
        SeedanceGetTasksInput(task_ids=["t-mine", "t-other"]), fake_ctx
    )
    assert isinstance(result, SeedanceGetTasksOutput)
    assert [t.task_id for t in result.tasks] == ["t-mine"]
    assert [(e.task_id, e.code) for e in result.errors] == [("t-other", "NOT_OWNED")]


# --- queue info ----------------------------------------------------------------


def test_queue_info_for_queued_task_uses_expiry_setting() -> None:
    task = _task("q", "queued", execution_expires_after=3600, service_tier="default")
    now = datetime.fromtimestamp(1721400000 + 1260, tz=UTC)
    info = queue_info(task, now=now)
    assert info is not None
    assert info.queued_seconds == 1260
    assert info.expires_at == datetime.fromtimestamp(1721403600, tz=UTC).isoformat()
    assert info.expires_at_estimated is False
    assert info.hint is not None and info.hint.startswith("Queued 21m on default tier")


def test_queue_info_assumes_default_expiry_when_missing() -> None:
    info = queue_info(_task("q", "queued"))
    assert info is not None
    assert info.expires_at_estimated is True
    assert info.expires_at == datetime.fromtimestamp(1721400000 + 172800, tz=UTC).isoformat()


def test_queue_info_running_task() -> None:
    now = datetime.fromtimestamp(1721400100 + 90, tz=UTC)
    info = queue_info(_task("r", "running"), now=now)
    assert info is not None
    assert (info.queued_seconds, info.running_seconds) == (100, 90)


def test_queue_info_none_when_finished() -> None:
    assert queue_info(_task("s", "succeeded")) is None
