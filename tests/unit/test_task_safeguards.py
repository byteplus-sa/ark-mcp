from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastmcp import Context
from fastmcp.exceptions import ToolError
from fastmcp_tasks.encryption import SnapshotDecryptionError, snapshot_codec
from fastmcp_tasks.settings import tasks_settings
from pydantic import SecretStr

from ark_mcp.config.env import Settings
from ark_mcp.security.tasks import TenantTasksExtension, guard_task_execution
from ark_mcp.server import create_server
from tests.integration.test_http_security import _http_settings


@pytest.mark.parametrize("backend", ["redis://localhost:6379/0", "rediss://localhost:6379/0"])
async def test_authenticated_redis_requires_encrypted_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    monkeypatch.setenv("FASTMCP_DOCKET_URL", backend)
    monkeypatch.setattr(tasks_settings, "encryption_key", None)
    with pytest.raises(ValueError, match="FASTMCP_TASKS_ENCRYPTION_KEY"):
        TenantTasksExtension(_http_settings(tmp_path))


async def test_encrypted_snapshot_round_trip_and_wrong_key_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FASTMCP_DOCKET_URL", "redis://localhost:6379/0")
    monkeypatch.setattr(
        tasks_settings, "encryption_key", SecretStr("synthetic-test-encryption-material-one")
    )
    extension = TenantTasksExtension(_http_settings(tmp_path))
    assert extension.docket_settings.url == "redis://localhost:6379/0"
    payload = '{"access_token":{"token":"synthetic-token"},"tenant_id":"tenant-a"}'
    stored = snapshot_codec().encode(payload)
    assert "synthetic-token" not in stored
    assert snapshot_codec().decode(stored) == payload
    monkeypatch.setattr(
        tasks_settings, "encryption_key", SecretStr("synthetic-test-encryption-material-two")
    )
    with pytest.raises(SnapshotDecryptionError):
        snapshot_codec().decode(stored)
    monkeypatch.setattr(tasks_settings, "encryption_key", None)
    with pytest.raises(SnapshotDecryptionError):
        snapshot_codec().decode(stored)


async def test_restarted_execution_does_not_repeat_accepted_provider_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "ark_mcp.security.tasks.get_task_context",
        lambda: SimpleNamespace(task_id="recovered-task"),
    )
    settings = Settings(_env_file=None, ARTIFACT_DIR=str(tmp_path))
    provider = AsyncMock()

    async def operation(ctx: Context) -> None:
        await provider()
        raise asyncio.CancelledError

    guarded = guard_task_execution(operation)
    server = create_server(settings)
    from fastmcp import Client

    async with Client(server), Context(server):
        with pytest.raises(asyncio.CancelledError):
            await guarded(Context(server))
    restarted = create_server(settings)
    async with Client(restarted), Context(restarted):
        with pytest.raises(ToolError, match="will not be replayed"):
            await guarded(Context(restarted))
    provider.assert_awaited_once()


async def test_foreground_handler_does_not_require_execution_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ark_mcp.security.tasks.get_task_context", lambda: None)
    handler = AsyncMock(return_value="completed")
    assert await guard_task_execution(handler)() == "completed"
    handler.assert_awaited_once()
