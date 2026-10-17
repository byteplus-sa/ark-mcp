"""Integration tests for gen_task_get, gen_task_list and gen_task_cancel_or_delete."""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from fastmcp.tools import ToolResult

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import refresh_capability_registry
from ark_mcp.domain.artifacts import ArtifactRef
from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.providers.modelark.generation_tasks import GenerationTaskService
from ark_mcp.providers.modelark.schemas import (
    GenerationTaskErrorDetail,
    GenerationTaskListResponse,
    GenerationTaskResponse,
    GenerationTaskUsage,
)
from ark_mcp.security.auth_context import AuthContext
from ark_mcp.security.safe_downloader import DownloadedMedia
from ark_mcp.tools.gen_task_cancel_or_delete import (
    GenTaskCancelOrDeleteInput,
    GenTaskCancelOrDeleteOutput,
    gen_task_cancel_or_delete,
)
from ark_mcp.tools.gen_task_get import GenTaskGetInput, GenTaskGetOutput, gen_task_get
from ark_mcp.tools.gen_task_list import GenTaskListInput, GenTaskPage, gen_task_list
from tests.fixtures.fake_context import FakeContext

SEEDANCE_MODEL = "dreamina-seedance-2-0-260128"
HYPER3D_MODEL = "hyper3d-gen2-260112"
VIDEO_URL = "https://tos-ap-southeast.bytepluses.com/video.mp4"
FILE_URL = "https://tos-ap-southeast.bytepluses.com/model.glb"
ALICE = AuthContext(principal_id="alice", tenant_id="tenant-a", transport="http")


@pytest.fixture
def seed3d_enabled(test_env: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("BYTEPLUS_MODELARK_3D_ENABLED", "true")
    get_settings.cache_clear()
    refresh_capability_registry()
    yield
    get_settings.cache_clear()
    refresh_capability_registry()


def _task(
    task_id: str, status: str, *, model: str = SEEDANCE_MODEL, **extra: Any
) -> GenerationTaskResponse:
    return GenerationTaskResponse(
        id=task_id,
        model=model,
        status=status,
        created_at=1721400000,
        updated_at=1721400100,
        **extra,
    )


async def _noop_close(self: GenerationTaskService) -> None:
    return None


def _serve(
    monkeypatch: pytest.MonkeyPatch,
    tasks: list[GenerationTaskResponse],
    *,
    listed: list[GenerationTaskResponse] | None = None,
) -> dict[str, list[Any]]:
    calls: dict[str, list[Any]] = {"list": [], "get": [], "delete": []}
    by_id = {task.id: task for task in tasks}

    async def mock_list(self: GenerationTaskService, **kwargs: Any) -> Any:
        calls["list"].append(kwargs)
        wanted = kwargs.get("task_ids")
        source = listed if listed is not None else tasks
        data = [task for task in source if wanted is None or task.id in wanted]
        if kwargs.get("status"):
            data = [task for task in data if task.status == kwargs["status"]]
        start = (kwargs["page"] - 1) * kwargs["page_size"]
        return GenerationTaskListResponse(data=data[start : start + kwargs["page_size"]]), "req"

    async def mock_get(self: GenerationTaskService, task_id: str) -> Any:
        calls["get"].append(task_id)
        if task_id not in by_id:
            raise ProviderError(
                NormalizedProviderError(
                    provider="modelark",
                    operation="get_task",
                    http_status=404,
                    code="NotFound",
                    message="task not found",
                    request_id="req-404",
                    retryable=False,
                    ambiguous_completion=False,
                )
            )
        return by_id[task_id], "req-get"

    async def mock_delete(self: GenerationTaskService, task_id: str) -> str:
        calls["delete"].append(task_id)
        return "req-delete"

    monkeypatch.setattr(GenerationTaskService, "list_tasks", mock_list)
    monkeypatch.setattr(GenerationTaskService, "get_task", mock_get)
    monkeypatch.setattr(GenerationTaskService, "delete_task", mock_delete)
    monkeypatch.setattr(GenerationTaskService, "close", _noop_close)
    return calls


def _download(body: bytes, content_type: str, url: str) -> Any:
    return patch(
        "ark_mcp.security.safe_downloader.SafeDownloader.download",
        new=AsyncMock(
            return_value=DownloadedMedia(body=body, content_type=content_type, final_url=url)
        ),
    )


class TestGenTaskGet:
    async def test_queued_task_reports_status_and_poll_hint(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _serve(monkeypatch, [_task("t-1", "queued")])
        result = await gen_task_get(GenTaskGetInput(task_ids=["t-1"]), fake_ctx)
        assert isinstance(result, GenTaskGetOutput)
        task = result.tasks[0]
        assert (task.status, task.kind, task.family) == ("queued", "video", "seedance_2")
        assert task.outputs == []
        assert task.queue is not None
        assert result.all_terminal is False
        assert result.recommended_poll_after_ms == 5000

    async def test_succeeded_without_persistence_returns_source_urls(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _serve(monkeypatch, [_task("t-done", "succeeded", content={"video_url": VIDEO_URL})])
        result = await gen_task_get(GenTaskGetInput(task_ids=["t-done"]), fake_ctx)
        assert isinstance(result, GenTaskGetOutput)
        [output] = result.tasks[0].outputs
        assert output.role == "video"
        assert str(output.source_url) == VIDEO_URL
        assert output.persistence == "not_requested"
        assert output.artifact is None
        assert result.all_terminal is True
        assert result.recommended_poll_after_ms is None

    async def test_succeeded_video_is_persisted_once(
        self,
        test_env: None,
        fake_ctx: FakeContext,
        temp_store: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await fake_ctx.lifespan_context["runtime"].task_artifact_cache.clear()
        task = _task(
            "t-video",
            "succeeded",
            content={"video_url": VIDEO_URL},
            usage=GenerationTaskUsage(completion_tokens=100, prompt_tokens=10),
        )
        _serve(monkeypatch, [task])
        with _download(b"video-bytes", "video/mp4", VIDEO_URL) as download:
            first = await gen_task_get(
                GenTaskGetInput(task_ids=["t-video"], persist_output=True), fake_ctx
            )
            second = await gen_task_get(
                GenTaskGetInput(task_ids=["t-video"], persist_output=True), fake_ctx
            )
        assert isinstance(first, GenTaskGetOutput) and isinstance(second, GenTaskGetOutput)
        [output] = first.tasks[0].outputs
        assert output.persistence == "persisted"
        assert output.artifact is not None and output.artifact.uri.startswith("seed-media://")
        assert (await temp_store.get(output.artifact.id)).data == b"video-bytes"
        assert second.tasks[0].outputs[0].artifact == output.artifact
        assert download.await_count == 1
        assert first.tasks[0].usage is not None and first.tasks[0].usage.completion_tokens == 100
        await fake_ctx.lifespan_context["runtime"].task_artifact_cache.clear()

    async def test_cached_artifacts_are_reused(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cache = fake_ctx.lifespan_context["runtime"].task_artifact_cache
        cached = ArtifactRef(
            id="cached-video",
            uri="seed-media://artifacts/cached-video",
            media_type="video",
            mime_type="video/mp4",
            created_at=datetime.now(UTC).isoformat(),
        )
        await cache.set("modelark", "t-cached", {"video": cached, "last_frame": None})
        _serve(monkeypatch, [_task("t-cached", "succeeded", content={"video_url": VIDEO_URL})])
        result = await gen_task_get(
            GenTaskGetInput(task_ids=["t-cached"], persist_output=True), fake_ctx
        )
        assert isinstance(result, GenTaskGetOutput)
        assert result.tasks[0].outputs[0].artifact == cached
        await cache.clear()

    async def test_3d_task_persists_model_file_with_inferred_mime(
        self,
        seed3d_enabled: None,
        fake_ctx: FakeContext,
        temp_store: Any,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        await fake_ctx.lifespan_context["runtime"].task_artifact_cache.clear()
        task = _task(
            "t-3d",
            "succeeded",
            model=HYPER3D_MODEL,
            content={"file_url": FILE_URL},
            usage=GenerationTaskUsage(total_tokens=42),
        )
        _serve(monkeypatch, [task])
        with _download(b"glb-bytes", "model/gltf-binary", FILE_URL):
            result = await gen_task_get(
                GenTaskGetInput(task_ids=["t-3d"], persist_output=True), fake_ctx
            )
        assert isinstance(result, GenTaskGetOutput)
        task_out = result.tasks[0]
        assert (task_out.kind, task_out.family) == ("3d", "seed3d_hyper3d")
        assert task_out.settings is None and task_out.queue is None and task_out.draft is None
        [output] = task_out.outputs
        assert output.role == "model_file"
        assert output.artifact is not None and output.artifact.mime_type == "model/gltf-binary"
        assert task_out.usage is not None and task_out.usage.total_tokens == 42
        await fake_ctx.lifespan_context["runtime"].task_artifact_cache.clear()

    async def test_unknown_model_reports_urls_without_persisting(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        task = _task("t-old", "succeeded", model="retired-model", content={"video_url": VIDEO_URL})
        _serve(monkeypatch, [task])
        with _download(b"x", "video/mp4", VIDEO_URL) as download:
            result = await gen_task_get(
                GenTaskGetInput(task_ids=["t-old"], persist_output=True), fake_ctx
            )
        assert isinstance(result, GenTaskGetOutput)
        task_out = result.tasks[0]
        assert (task_out.kind, task_out.family) == ("unknown", None)
        assert task_out.outputs[0].persistence == "not_requested"
        assert download.await_count == 0

    async def test_failed_task_carries_error(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        failed = _task(
            "t-fail",
            "failed",
            error=GenerationTaskErrorDetail(code="MODERATION_FAILED", message="content rejected"),
        )
        _serve(monkeypatch, [failed])
        result = await gen_task_get(GenTaskGetInput(task_ids=["t-fail"]), fake_ctx)
        assert isinstance(result, GenTaskGetOutput)
        error = result.tasks[0].error
        assert error is not None and error.code == "MODERATION_FAILED"

    async def test_batch_uses_one_list_call_and_dedupes(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tasks = [_task("a", "queued"), _task("b", "running"), _task("c", "failed")]
        calls = _serve(monkeypatch, tasks)
        result = await gen_task_get(GenTaskGetInput(task_ids=["a", "b", "c", "a"]), fake_ctx)
        assert isinstance(result, GenTaskGetOutput)
        assert len(calls["list"]) == 1
        assert calls["list"][0]["task_ids"] == ["a", "b", "c"]
        assert calls["get"] == []
        assert [task.task_id for task in result.tasks] == ["a", "b", "c"]
        assert result.active_tasks == 2

    async def test_missing_and_urlless_entries_are_refetched(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        full = _task("t-done", "succeeded", content={"video_url": VIDEO_URL})
        calls = _serve(
            monkeypatch,
            [full, _task("t-unlisted", "queued")],
            listed=[_task("t-done", "succeeded")],
        )
        result = await gen_task_get(
            GenTaskGetInput(task_ids=["t-done", "t-unlisted", "t-gone"]), fake_ctx
        )
        assert isinstance(result, GenTaskGetOutput)
        assert sorted(calls["get"]) == ["t-done", "t-gone", "t-unlisted"]
        assert [task.task_id for task in result.tasks] == ["t-done", "t-unlisted"]
        assert result.tasks[0].outputs[0].role == "video"
        assert [(e.task_id, e.code) for e in result.errors] == [("t-gone", "NOT_FOUND")]

    async def test_tasks_owned_by_others_are_reported_not_fetched(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime = fake_ctx.lifespan_context["runtime"]
        await runtime.ownership_store.record(
            "modelark", "t-other", AuthContext(principal_id="bob", tenant_id="t")
        )
        calls = _serve(monkeypatch, [_task("t-mine", "queued"), _task("t-other", "queued")])
        result = await gen_task_get(GenTaskGetInput(task_ids=["t-mine", "t-other"]), fake_ctx)
        assert isinstance(result, GenTaskGetOutput)
        assert [task.task_id for task in result.tasks] == ["t-mine"]
        assert [(e.task_id, e.code) for e in result.errors] == [("t-other", "NOT_OWNED")]
        assert calls["list"][0]["task_ids"] == ["t-mine"]

    async def test_persist_output_in_foreground_is_rejected(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _serve(monkeypatch, [_task("t-1", "queued")])
        fake_ctx.task_id = None
        result = await gen_task_get(
            GenTaskGetInput(task_ids=["t-1"], persist_output=True), fake_ctx
        )
        assert isinstance(result, ToolResult)

    async def test_output_dir_requires_persist_output(
        self, test_env: None, fake_ctx: FakeContext, tmp_path: Any
    ) -> None:
        with pytest.raises(ValueError, match="output_dir requires persist_output=true"):
            await gen_task_get(
                GenTaskGetInput(task_ids=["t-1"], output_dir=str(tmp_path)), fake_ctx
            )

    async def test_pages_when_provider_caps_page_size(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ids = [f"t-{index}" for index in range(25)]
        pages: list[int] = []

        async def mock_list(self: GenerationTaskService, **kwargs: Any) -> Any:
            pages.append(kwargs["page"])
            chunk = ids[(kwargs["page"] - 1) * 20 : kwargs["page"] * 20]
            return GenerationTaskListResponse(data=[_task(i, "queued") for i in chunk]), "req"

        monkeypatch.setattr(GenerationTaskService, "list_tasks", mock_list)
        monkeypatch.setattr(GenerationTaskService, "close", _noop_close)
        result = await gen_task_get(GenTaskGetInput(task_ids=ids), fake_ctx)
        assert isinstance(result, GenTaskGetOutput)
        assert pages == [1, 2]
        assert len(result.tasks) == 25


class TestGenTaskList:
    async def test_local_principal_pages_with_provider_page_cursor(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        tasks = [_task(f"t-{index}", "queued") for index in range(3)]
        calls: list[dict[str, Any]] = []

        async def mock_list(self: GenerationTaskService, **kwargs: Any) -> Any:
            calls.append(kwargs)
            start = (kwargs["page"] - 1) * kwargs["page_size"]
            chunk = tasks[start : start + kwargs["page_size"]]
            return GenerationTaskListResponse(data=chunk), "req"

        monkeypatch.setattr(GenerationTaskService, "list_tasks", mock_list)
        monkeypatch.setattr(GenerationTaskService, "close", _noop_close)
        first = await gen_task_list(GenTaskListInput(page_size=2), fake_ctx)
        assert isinstance(first, GenTaskPage)
        assert [task.task_id for task in first.tasks] == ["t-0", "t-1"]
        assert first.next_cursor is not None
        second = await gen_task_list(
            GenTaskListInput(page_size=2, cursor=first.next_cursor), fake_ctx
        )
        assert isinstance(second, GenTaskPage)
        assert [task.task_id for task in second.tasks] == ["t-2"]
        assert second.next_cursor is None
        assert [call["page"] for call in calls] == [1, 2]

    async def test_remote_principal_pages_owned_tasks_newest_first_without_url_overflow(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("ark_mcp.tools.gen_task_list.get_principal", lambda _ctx: ALICE)
        store = fake_ctx.lifespan_context["runtime"].ownership_store
        ids = [f"cgt-{index:04d}" for index in range(1000)]
        for task_id in ids:
            await store.record("modelark", task_id, ALICE)
        await store.record(
            "modelark", "cgt-bob", AuthContext(principal_id="bob", tenant_id="tenant-a")
        )
        tasks = [_task(task_id, "succeeded") for task_id in ids]
        calls = _serve(monkeypatch, tasks)

        seen: list[str] = []
        cursor: str | None = None
        pages = 0
        while True:
            page = await gen_task_list(GenTaskListInput(page_size=100, cursor=cursor), fake_ctx)
            assert isinstance(page, GenTaskPage)
            seen.extend(task.task_id for task in page.tasks)
            pages += 1
            cursor = page.next_cursor
            if cursor is None:
                break
        assert seen == list(reversed(ids))
        assert "cgt-bob" not in seen
        for call in calls["list"]:
            query = urlencode({"filter.task_ids": call["task_ids"]}, doseq=True)
            assert len(call["task_ids"]) <= 100
            assert len(query) < 8000

    async def test_remote_principal_status_filter_and_partial_pages(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("ark_mcp.tools.gen_task_list.get_principal", lambda _ctx: ALICE)
        store = fake_ctx.lifespan_context["runtime"].ownership_store
        for index in range(5):
            await store.record("modelark", f"mix-{index}", ALICE)
        tasks = [
            _task(f"mix-{index}", "failed" if index % 2 else "succeeded") for index in range(5)
        ]
        _serve(monkeypatch, tasks)
        page = await gen_task_list(GenTaskListInput(status="failed"), fake_ctx)
        assert isinstance(page, GenTaskPage)
        assert [task.task_id for task in page.tasks] == ["mix-3", "mix-1"]
        assert page.next_cursor is None

    async def test_remote_principal_ignores_tasks_outside_list_window(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("ark_mcp.tools.gen_task_list.get_principal", lambda _ctx: ALICE)
        store = fake_ctx.lifespan_context["runtime"].ownership_store
        await store.record("modelark", "fresh", ALICE)
        stale = (datetime.now(UTC) - timedelta(days=8)).isoformat(timespec="microseconds")
        store._connection.execute(
            "INSERT INTO task_ownership VALUES (?, ?, ?, ?, ?)",
            ("modelark", "stale", "alice", "tenant-a", stale),
        )
        calls = _serve(monkeypatch, [_task("fresh", "queued"), _task("stale", "queued")])
        page = await gen_task_list(GenTaskListInput(), fake_ctx)
        assert isinstance(page, GenTaskPage)
        assert [task.task_id for task in page.tasks] == ["fresh"]
        assert calls["list"][0]["task_ids"] == ["fresh"]

    async def test_cursor_with_different_filters_is_rejected(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _serve(monkeypatch, [_task(f"t-{index}", "queued") for index in range(3)])
        first = await gen_task_list(GenTaskListInput(page_size=1), fake_ctx)
        assert isinstance(first, GenTaskPage) and first.next_cursor is not None
        with pytest.raises(ValueError, match="different filters"):
            await gen_task_list(
                GenTaskListInput(page_size=1, status="queued", cursor=first.next_cursor),
                fake_ctx,
            )

    @pytest.mark.parametrize(
        "cursor",
        ["not-base64!!", base64.urlsafe_b64encode(b'{"v": 9}').decode()],
    )
    async def test_malformed_cursor_is_rejected(
        self, test_env: None, fake_ctx: FakeContext, cursor: str
    ) -> None:
        with pytest.raises(ValueError, match="not a valid gen_task_list cursor"):
            await gen_task_list(GenTaskListInput(cursor=cursor), fake_ctx)

    async def test_crafted_cursor_cannot_reveal_other_principals_tasks(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("ark_mcp.tools.gen_task_list.get_principal", lambda _ctx: ALICE)
        store = fake_ctx.lifespan_context["runtime"].ownership_store
        await store.record("modelark", "alice-1", ALICE)
        await store.record("modelark", "alice-2", ALICE)
        await store.record(
            "modelark", "bob-task", AuthContext(principal_id="bob", tenant_id="tenant-a")
        )
        _serve(
            monkeypatch,
            [_task("alice-1", "queued"), _task("alice-2", "queued"), _task("bob-task", "queued")],
        )
        first = await gen_task_list(GenTaskListInput(page_size=1), fake_ctx)
        assert isinstance(first, GenTaskPage) and first.next_cursor is not None
        payload = json.loads(base64.urlsafe_b64decode(first.next_cursor.encode()))
        payload["created_at"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        payload["task_id"] = "zzz"
        crafted = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        seen: list[str] = []
        cursor: str | None = crafted
        while cursor is not None:
            page = await gen_task_list(GenTaskListInput(page_size=1, cursor=cursor), fake_ctx)
            assert isinstance(page, GenTaskPage)
            seen.extend(task.task_id for task in page.tasks)
            cursor = page.next_cursor
        assert seen == ["alice-2", "alice-1"]


class TestGenTaskCancelOrDelete:
    async def test_delete_succeeded_task(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _serve(monkeypatch, [_task("t-done", "succeeded")])
        result = await gen_task_cancel_or_delete(
            GenTaskCancelOrDeleteInput(
                task_id="t-done", mode="delete", expected_status="succeeded"
            ),
            fake_ctx,
        )
        assert isinstance(result, GenTaskCancelOrDeleteOutput)
        assert (result.mode, result.previous_status) == ("delete", "succeeded")
        assert calls["delete"] == ["t-done"]

    async def test_cancel_queued_3d_task(
        self, seed3d_enabled: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _serve(monkeypatch, [_task("t-3d", "queued", model=HYPER3D_MODEL)])
        result = await gen_task_cancel_or_delete(
            GenTaskCancelOrDeleteInput(task_id="t-3d", mode="cancel", expected_status="queued"),
            fake_ctx,
        )
        assert isinstance(result, GenTaskCancelOrDeleteOutput)
        assert result.mode == "cancel"
        assert calls["delete"] == ["t-3d"]

    async def test_status_mismatch_is_rejected_before_delete(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _serve(monkeypatch, [_task("t-run", "running")])
        with pytest.raises(ValueError, match="has status 'running'"):
            await gen_task_cancel_or_delete(
                GenTaskCancelOrDeleteInput(
                    task_id="t-run", mode="cancel", expected_status="queued"
                ),
                fake_ctx,
            )
        assert calls["delete"] == []

    async def test_other_principals_task_is_denied(
        self, test_env: None, fake_ctx: FakeContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        runtime = fake_ctx.lifespan_context["runtime"]
        await runtime.ownership_store.record(
            "modelark", "t-bob", AuthContext(principal_id="bob", tenant_id="t")
        )
        calls = _serve(monkeypatch, [_task("t-bob", "queued")])
        with pytest.raises(PermissionError):
            await gen_task_cancel_or_delete(
                GenTaskCancelOrDeleteInput(
                    task_id="t-bob", mode="cancel", expected_status="queued"
                ),
                fake_ctx,
            )
        assert calls["delete"] == []

    def test_mode_and_expected_status_must_agree(self) -> None:
        with pytest.raises(ValueError, match="Cancel mode requires"):
            GenTaskCancelOrDeleteInput(task_id="t", mode="cancel", expected_status="succeeded")
        with pytest.raises(ValueError, match="Delete mode requires"):
            GenTaskCancelOrDeleteInput(task_id="t", mode="delete", expected_status="queued")
