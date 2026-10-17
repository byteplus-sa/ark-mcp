from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, Mock

import pytest
from fastmcp import Client, FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import ToolResult
from fastmcp.utilities.tasks import TaskConfig
from fastmcp_tasks import TasksExtension, call_tool_task
from mcp.shared.exceptions import MCPError

from ark_mcp.observability import metrics


@pytest.fixture
def instruments(monkeypatch):
    counters = {
        name: Mock()
        for name in (
            "TOOL_REQUESTS",
            "TOOL_DURATION",
            "TOOL_ADMISSION_DURATION",
            "BACKGROUND_JOB_SUBMISSIONS",
        )
    }
    for name, counter in counters.items():
        monkeypatch.setattr(metrics, name, counter)
    return counters


@pytest.mark.parametrize("outcome", ["success", "error", "exception", "cancelled"])
async def test_real_worker_records_terminal_outcome_once(outcome, instruments):
    started = asyncio.Event()

    async def measured(value: int = 1) -> ToolResult:
        started.set()
        if outcome == "cancelled":
            await asyncio.Event().wait()
        await asyncio.sleep(0.06)
        if outcome == "exception":
            raise ValueError("pre-provider failure")
        return ToolResult(content=str(value), is_error=outcome == "error")

    wrapped = metrics.instrument_tool_execution(measured, tool_name="measured")
    assert inspect.signature(wrapped) == inspect.signature(measured, eval_str=True)
    server = FastMCP(middleware=[metrics.MetricsMiddleware()])
    server.add_extension(TasksExtension())
    server.tool(name="measured", task=True)(wrapped)
    async with Client(server) as client:
        task = await call_tool_task(client, "measured", {}, raise_on_error=False)
        await asyncio.wait_for(started.wait(), timeout=5)
        if outcome == "cancelled":
            await task.cancel()
            for _ in range(100):
                if instruments["TOOL_DURATION"].labels.return_value.observe.called:
                    break
                await asyncio.sleep(0.01)
        else:
            result = await task.result()
            assert result.is_error == (outcome in ("error", "exception"))
            await task.result()
            await task.status()
    labels = instruments["TOOL_REQUESTS"].labels.call_args_list
    assert [entry.kwargs["status"] for entry in labels].count("accepted") == 1
    assert [entry.kwargs["status"] for entry in labels].count(outcome) == 1
    instruments["TOOL_DURATION"].labels.return_value.observe.assert_called_once()
    if outcome != "cancelled":
        assert instruments["TOOL_DURATION"].labels.return_value.observe.call_args.args[0] >= 0.06
    instruments["TOOL_ADMISSION_DURATION"].labels.return_value.observe.assert_called_once()


async def test_foreground_wrapper_preserves_single_measurement(instruments):
    async def measured(value: int = 1) -> int:
        return value

    server = FastMCP(middleware=[metrics.MetricsMiddleware()])
    server.tool()(metrics.instrument_tool_execution(measured, tool_name="measured"))
    async with Client(server) as client:
        assert (await client.call_tool("measured", {"value": 9})).data == 9
    instruments["TOOL_REQUESTS"].labels.assert_called_once_with(tool="measured", status="success")
    instruments["TOOL_DURATION"].labels.return_value.observe.assert_called_once()
    instruments["TOOL_ADMISSION_DURATION"].labels.assert_not_called()


async def test_required_native_submission_rejection_is_recorded(instruments):
    async def seed_understand(value: int = 1) -> int:
        return value

    server = FastMCP(middleware=[metrics.MetricsMiddleware()])
    server.add_extension(TasksExtension())
    server.tool(name="seed_understand", task=TaskConfig(mode="required"))(seed_understand)

    async with Client(server, mode="legacy") as client:
        with pytest.raises(MCPError, match="requires the tasks extension"):
            await client.call_tool("seed_understand", {})

    instruments["BACKGROUND_JOB_SUBMISSIONS"].labels.assert_called_once_with(
        target="seed_understand",
        status="rejected",
        path="native",
    )
    instruments["BACKGROUND_JOB_SUBMISSIONS"].labels.return_value.inc.assert_called_once_with()
    instruments["TOOL_ADMISSION_DURATION"].labels.return_value.observe.assert_called_once()
    instruments["TOOL_DURATION"].labels.assert_not_called()


async def test_optional_native_submission_rejection_is_recorded(instruments, monkeypatch):
    async def gen_task_get() -> str:
        return "unused"

    create_failure = AsyncMock(
        side_effect=MCPError(code=-32603, message="task backend unavailable")
    )
    monkeypatch.setattr("fastmcp_tasks.extension.create_task", create_failure)
    server = FastMCP(middleware=[metrics.MetricsMiddleware()])
    server.add_extension(TasksExtension())
    server.tool(name="gen_task_get", task=TaskConfig(mode="optional"))(gen_task_get)

    async with Client(server) as client:
        with pytest.raises(MCPError, match="task backend unavailable"):
            await call_tool_task(client, "gen_task_get", {})

    create_failure.assert_awaited_once()
    instruments["BACKGROUND_JOB_SUBMISSIONS"].labels.assert_called_once_with(
        target="gen_task_get",
        status="rejected",
        path="native",
    )
    instruments["BACKGROUND_JOB_SUBMISSIONS"].labels.return_value.inc.assert_called_once_with()
    instruments["TOOL_ADMISSION_DURATION"].labels.return_value.observe.assert_called_once()
    instruments["TOOL_DURATION"].labels.assert_not_called()


async def test_native_argument_validation_rejection_is_recorded(instruments):
    async def seed_understand(value: int) -> int:
        return value

    server = FastMCP(middleware=[metrics.MetricsMiddleware()])
    server.add_extension(TasksExtension())
    server.tool(name="seed_understand", task=TaskConfig(mode="required"))(seed_understand)

    async with Client(server) as client:
        with pytest.raises(ToolError, match="did not run as a task"):
            await call_tool_task(client, "seed_understand", {"value": "invalid"})

    instruments["BACKGROUND_JOB_SUBMISSIONS"].labels.assert_called_once_with(
        target="seed_understand",
        status="rejected",
        path="native",
    )
    instruments["BACKGROUND_JOB_SUBMISSIONS"].labels.return_value.inc.assert_called_once_with()
    instruments["TOOL_ADMISSION_DURATION"].labels.return_value.observe.assert_called_once()
    instruments["TOOL_DURATION"].labels.assert_not_called()


async def test_registered_server_worker_records_pre_provider_failure(
    instruments, monkeypatch, tmp_path
):
    from ark_mcp.config.env import get_settings
    from ark_mcp.config.model_capabilities import refresh_capability_registry
    from ark_mcp.providers.modelark.understanding import SeedUnderstandingService
    from ark_mcp.server import create_server

    monkeypatch.setenv("BYTEPLUS_MODELARK_API_KEY", "test-placeholder")
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("ARTIFACT_BACKEND", "filesystem")
    get_settings.cache_clear()
    refresh_capability_registry()
    monkeypatch.setattr(
        SeedUnderstandingService,
        "build_request",
        Mock(side_effect=ValueError("pre-provider regression sentinel")),
    )
    try:
        server = create_server(get_settings())
        async with Client(server) as client:
            task = await call_tool_task(
                client,
                "seed_understand",
                {"input": {"prompt": "test"}},
                raise_on_error=False,
            )
            result = await task.result()
            assert result.is_error
            assert "pre-provider regression sentinel" in result.content[0].text
            await task.result()
            await task.status()
        labels = instruments["TOOL_REQUESTS"].labels.call_args_list
        assert sorted((entry.kwargs for entry in labels), key=lambda item: item["status"]) == [
            {"tool": "seed_understand", "status": "accepted"},
            {"tool": "seed_understand", "status": "exception"},
        ]
        instruments["TOOL_DURATION"].labels.return_value.observe.assert_called_once()
        instruments["TOOL_ADMISSION_DURATION"].labels.return_value.observe.assert_called_once()
        instruments["BACKGROUND_JOB_SUBMISSIONS"].labels.assert_called_once_with(
            target="seed_understand",
            status="accepted",
            path="native",
        )
        instruments["BACKGROUND_JOB_SUBMISSIONS"].labels.return_value.inc.assert_called_once_with()
    finally:
        get_settings.cache_clear()
        refresh_capability_registry()


def test_background_submission_metric_accepts_only_allowlisted_targets(monkeypatch):
    counter = Mock()
    monkeypatch.setattr(metrics, "BACKGROUND_JOB_SUBMISSIONS", counter)

    metrics.record_background_job_submission(
        target="seed_understand",
        status="accepted",
        path="compatibility",
    )
    metrics.record_background_job_submission(
        target="ark_job_submit",
        status="rejected",
        path="compatibility",
    )

    counter.labels.assert_called_once_with(
        target="seed_understand",
        status="accepted",
        path="compatibility",
    )
    counter.labels.return_value.inc.assert_called_once_with()
