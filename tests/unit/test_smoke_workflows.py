import sys
from pathlib import Path

import pytest
from fastmcp import Client, FastMCP
from fastmcp.server import Context
from fastmcp_tasks import TasksExtension
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from _smoke_context import SmokeClient


class Request(BaseModel):
    persist_output: bool = False


class Result(BaseModel):
    task_id: str


@pytest.mark.asyncio
async def test_smoke_client_uses_real_worker_and_preserves_provider_ids(tmp_path):
    server = FastMCP("smoke-test")
    server.add_extension(TasksExtension())
    calls = []

    @server.tool(task=True)
    async def submit(input: Request, ctx: Context) -> Result:
        assert ctx.task_id is not None
        calls.append("submit")
        return Result(task_id="provider-123")

    @server.tool
    async def status(input: Request) -> Result:
        raise ValueError("poll interrupted")

    async with Client(server) as client:
        smoke = SmokeClient(client)
        result = await smoke.call("submit", Request(), Result)
        smoke.save_provider_ids(tmp_path, [result.task_id])
        with pytest.raises(Exception, match="poll interrupted"):
            await smoke.call("status", Request(), Result, background=False)
    assert calls == ["submit"]
    assert "provider-123" in (tmp_path / "smoke_provider_tasks.jsonl").read_text()


@pytest.mark.asyncio
@pytest.mark.parametrize("variation", [False, True])
@pytest.mark.parametrize("poll_failure", [False, True])
async def test_video_workflows_persist_after_status_and_never_resubmit(
    tmp_path, monkeypatch, variation, poll_failure
):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import live_smoke_test as single
    import live_smoke_test_variations as multiple

    module = multiple if variation else single
    monkeypatch.setattr(module, "ARTIFACTS_DIR", tmp_path)
    artifact = SimpleNamespace(
        id="video", uri="seed-media://video", media_type="video", mime_type="video/mp4", bytes=3
    )
    store = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(data=b"abc", mime_type="image/png"))
    )
    runtime = SimpleNamespace(artifact_store=store)
    calls = []
    smoke = SmokeClient(None)

    async def call(name, params, output_type, *, background=True):
        calls.append((name, background, getattr(params, "persist_output", None)))
        if name == "seedream_generate_image":
            return SimpleNamespace(artifacts=[artifact])
        if name == "seedance_create_task":
            return SimpleNamespace(
                task_id="provider-123", status="queued", recommended_poll_after_ms=1
            )
        if name == "seedance_create_task_variations":
            return SimpleNamespace(
                summary=SimpleNamespace(
                    total=1,
                    succeeded=1,
                    failed=0,
                    variations=[SimpleNamespace(task_id="provider-123", index=0)],
                )
            )
        assert "provider-123" in (tmp_path / "smoke_provider_tasks.jsonl").read_text()
        if name == "gen_task_list":
            return SimpleNamespace(tasks=[SimpleNamespace(task_id="provider-123")])
        if poll_failure:
            raise RuntimeError("poll interrupted")
        output = SimpleNamespace(role="video", artifact=artifact if params.persist_output else None)
        task = SimpleNamespace(
            status="succeeded",
            outputs=[output],
            error=None,
            model="test",
            created_at=None,
            updated_at=None,
        )
        return SimpleNamespace(tasks=[task])

    monkeypatch.setattr(smoke, "call", call)
    workflow = (
        multiple.test_seedance_variations(smoke, runtime, artifact)
        if variation
        else single.test_video_generation_with_image(smoke, runtime)
    )
    if poll_failure:
        with pytest.raises(RuntimeError, match="poll interrupted"):
            await workflow
    else:
        result = await workflow
        assert result == (True if variation else {"video": "video"})
        assert [entry[1:] for entry in calls if entry[0] == "gen_task_get"] == [
            (False, False),
            (True, True),
        ]
        assert list(tmp_path.glob("*.mp4"))
    assert sum(name.startswith("seedance_create_task") for name, _, _ in calls) == 1


@pytest.mark.asyncio
async def test_smoke_session_persists_video_through_real_server(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock

    from _smoke_context import smoke_session

    import ark_mcp.runtime as runtime_module
    from ark_mcp.config.env import Settings
    from ark_mcp.providers.modelark.generation_tasks import GenerationTaskService
    from ark_mcp.providers.modelark.schemas import (
        GenerationTaskListResponse,
        GenerationTaskResponse,
    )
    from ark_mcp.security.safe_downloader import DownloadedMedia, SafeDownloader
    from ark_mcp.tools.gen_task_get import GenTaskGetInput, GenTaskGetOutput

    create_runtime = AsyncMock(wraps=runtime_module.create_runtime_services)
    monkeypatch.setattr(runtime_module, "create_runtime_services", create_runtime)

    settings = Settings(
        _env_file=None, artifact_dir=str(tmp_path), BYTEPLUS_MODELARK_API_KEY="test-key"
    )
    from fastmcp.server.dependencies import get_context

    task_contexts = []

    async def list_tasks(*args, **kwargs):
        task_contexts.append(get_context().task_id)
        task = GenerationTaskResponse(
            id="provider-123",
            model="dreamina-seedance-2-0-260128",
            status="succeeded",
            created_at=1721400000,
            updated_at=1721400000,
            content={"video_url": "https://example.com/video.mp4"},
        )
        return GenerationTaskListResponse(data=[task]), None

    monkeypatch.setattr(GenerationTaskService, "list_tasks", list_tasks)
    download = AsyncMock(
        return_value=DownloadedMedia(
            body=b"video", content_type="video/mp4", final_url="https://example.com/video.mp4"
        )
    )
    monkeypatch.setattr(SafeDownloader, "download", download)
    async with smoke_session(settings) as (smoke, runtime):
        status = await smoke.call(
            "gen_task_get",
            GenTaskGetInput(task_ids=["provider-123"]),
            GenTaskGetOutput,
            background=False,
        )
        assert status.tasks[0].status == "succeeded"
        assert status.tasks[0].outputs[0].artifact is None
        result = await smoke.call(
            "gen_task_get",
            GenTaskGetInput(task_ids=["provider-123"], persist_output=True),
            GenTaskGetOutput,
        )
        video = result.tasks[0].outputs[0].artifact
        assert video is not None
        assert (await runtime.artifact_store.get(video.id)).data == b"video"
    download.assert_awaited_once()
    assert task_contexts[0] is None
    assert task_contexts[1] is not None
    create_runtime.assert_awaited_once()
