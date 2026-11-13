from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from fastmcp.server.auth import AccessToken, TokenVerifier

from ark_mcp.providers.vod_mediakit.enhancement import VodMediaKitEnhancementService
from ark_mcp.providers.vod_mediakit.schemas import EnhancementSubmission
from ark_mcp.server import create_server
from tests.integration.test_http_security import _jwt_settings


class TenantVerifier(TokenVerifier):
    def __init__(self, tenant_claim: str = "tenant_id", principal_source: str = "sub") -> None:
        super().__init__()
        self.tenant_claim = tenant_claim
        self.principal_source = principal_source

    async def verify_token(self, token: str) -> AccessToken:
        claims = {"sub": "alice"} if self.principal_source == "sub" else {}
        if token != "missing-tenant":
            claims[self.tenant_claim] = token
        return AccessToken(
            token=token,
            client_id="shared-client",
            scopes=[] if token == "no-scope" else ["vod:enhance"],
            claims=claims,
            subject="alice" if self.principal_source == "subject" else None,
        )


async def request_task(
    client: httpx.AsyncClient,
    token: str,
    method: str,
    params: dict[str, Any],
    *,
    task_capable: bool = True,
) -> dict[str, Any]:
    name = params.get("name", params.get("taskId", ""))
    response = await client.post(
        "/mcp",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
            "Mcp-Protocol-Version": "2026-07-28",
            "Mcp-Method": method,
            "Mcp-Name": name,
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": {
                **params,
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": (
                        {"extensions": {"io.modelcontextprotocol/tasks": {}}}
                        if task_capable
                        else {}
                    ),
                },
            },
        },
    )
    return response.json()


@asynccontextmanager
async def task_client(
    tmp_path: Path, tenant_claim: str = "tenant_id", principal_source: str = "sub"
):
    settings = _jwt_settings(tmp_path).model_copy(update={"mcp_tenant_claim": tenant_claim})
    server = create_server(settings, auth_provider=TenantVerifier(tenant_claim, principal_source))
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        yield client


async def submit(client: httpx.AsyncClient, token: str = "tenant-a") -> dict[str, Any]:
    return await request_task(
        client,
        token,
        "tools/call",
        {
            "name": "vod_enhance_video",
            "arguments": {
                "input": {"video_url": "https://example.com/input.mp4", "persist": False}
            },
        },
    )


async def compatibility_call(
    client: httpx.AsyncClient,
    token: str,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    return await request_task(
        client,
        token,
        "tools/call",
        {"name": name, "arguments": arguments},
        task_capable=False,
    )


async def compatibility_terminal(
    client: httpx.AsyncClient,
    token: str,
    job_id: str,
) -> dict[str, Any]:
    async with asyncio.timeout(5):
        while True:
            result = await compatibility_call(
                client,
                token,
                "ark_job_get",
                {"input": {"job_id": job_id}},
            )
            if result.get("result", {}).get("structuredContent", {}).get("status") != "working":
                return result
            await asyncio.sleep(0.01)


async def terminal(client: httpx.AsyncClient, task_id: str) -> dict[str, Any]:
    async with asyncio.timeout(5):
        while True:
            result = await request_task(client, "tenant-a", "tasks/get", {"taskId": task_id})
            if result.get("result", {}).get("status") != "working":
                return result
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("tenant_claim", ["tenant_id", "organization"])
@pytest.mark.parametrize("principal_source", ["sub", "subject", "client_id"])
async def test_task_results_enforce_configured_tenant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tenant_claim: str, principal_source: str
) -> None:
    provider = AsyncMock(
        return_value=EnhancementSubmission(
            status="succeeded", output_url="https://output.example.com/private.mp4"
        )
    )
    monkeypatch.setattr(VodMediaKitEnhancementService, "enhance", provider)
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", AsyncMock())
    async with task_client(tmp_path, tenant_claim, principal_source) as client:
        task_id = (await submit(client))["result"]["taskId"]
        result = await terminal(client, task_id)
        assert "result" in result, result
        assert result["result"]["result"]["isError"] is False
        assert result["result"]["result"]["structuredContent"]["source_url"].endswith("private.mp4")
        other = await request_task(client, "tenant-b", "tasks/get", {"taskId": task_id})
        assert "error" in other
        assert "private.mp4" not in str(other)
    provider.assert_awaited_once()


@pytest.mark.parametrize("method", ["tasks/get", "tasks/update", "tasks/cancel"])
async def test_other_tenant_cannot_access_running_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, method: str
) -> None:
    started, release = asyncio.Event(), asyncio.Event()

    async def enhance(*args: Any, **kwargs: Any) -> EnhancementSubmission:
        started.set()
        await release.wait()
        return EnhancementSubmission(
            status="succeeded", output_url="https://output.example.com/private.mp4"
        )

    monkeypatch.setattr(VodMediaKitEnhancementService, "enhance", enhance)
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", AsyncMock())
    async with task_client(tmp_path) as client:
        task_id = (await submit(client))["result"]["taskId"]
        await asyncio.wait_for(started.wait(), 5)
        try:
            params: dict[str, Any] = {"taskId": task_id}
            if method == "tasks/update":
                params["inputResponses"] = {}
            denied = await request_task(client, "tenant-b", method, params)
            assert "error" in denied
        finally:
            release.set()
        result = await terminal(client, task_id)
        assert "result" in result, result
        assert result["result"]["result"]["isError"] is False


async def test_missing_tenant_rejected_before_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = AsyncMock()
    monkeypatch.setattr(VodMediaKitEnhancementService, "enhance", provider)
    async with task_client(tmp_path) as client:
        result = await submit(client, "missing-tenant")
        assert "error" in result or result.get("result", {}).get("isError") is True
        assert "taskId" not in result.get("result", {})
    provider.assert_not_awaited()


@pytest.mark.parametrize("token", ["tenant-a", "missing-tenant"])
async def test_unknown_task_is_denied(tmp_path: Path, token: str) -> None:
    async with task_client(tmp_path) as client:
        result = await request_task(client, token, "tasks/get", {"taskId": "unknown-task"})
        assert "error" in result


async def test_owner_can_cancel_running_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    started, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def enhance(*args: Any, **kwargs: Any) -> EnhancementSubmission:
        started.set()
        try:
            await release.wait()
        finally:
            cancelled.set()
        raise AssertionError("Provider wait unexpectedly completed")

    monkeypatch.setattr(VodMediaKitEnhancementService, "enhance", enhance)
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", AsyncMock())
    async with task_client(tmp_path) as client:
        task_id = (await submit(client))["result"]["taskId"]
        await asyncio.wait_for(started.wait(), 5)
        try:
            result = await asyncio.wait_for(
                request_task(client, "tenant-a", "tasks/cancel", {"taskId": task_id}), 5
            )
            assert result["result"]["resultType"] == "complete"
            await asyncio.wait_for(cancelled.wait(), 5)
            assert (await terminal(client, task_id))["result"]["status"] == "cancelled"
        finally:
            release.set()


async def test_compatibility_jobs_enforce_scope_and_tenant_ownership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = AsyncMock(
        return_value=EnhancementSubmission(
            status="succeeded", output_url="https://output.example.com/private.mp4"
        )
    )
    monkeypatch.setattr(VodMediaKitEnhancementService, "enhance", provider)
    monkeypatch.setattr(VodMediaKitEnhancementService, "close", AsyncMock())

    async with task_client(tmp_path) as client:
        no_scope_capabilities = await compatibility_call(
            client,
            "no-scope",
            "ark_job_capabilities",
            {},
        )
        assert no_scope_capabilities["result"]["structuredContent"]["targets"] == []

        authorized_capabilities = await compatibility_call(
            client,
            "tenant-a",
            "ark_job_capabilities",
            {},
        )
        authorized_targets = {
            target["tool_name"]
            for target in authorized_capabilities["result"]["structuredContent"]["targets"]
        }
        assert "vod_enhance_video" in authorized_targets
        assert "vod_get_task" not in authorized_targets

        missing_tenant = await compatibility_call(
            client,
            "missing-tenant",
            "ark_job_submit",
            {
                "input": {
                    "tool_name": "vod_enhance_video",
                    "arguments": {
                        "input": {
                            "video_url": "https://example.com/input.mp4",
                            "persist": False,
                        }
                    },
                }
            },
        )
        assert "error" in missing_tenant or missing_tenant["result"]["isError"] is True

        denied_scope = await compatibility_call(
            client,
            "no-scope",
            "ark_job_submit",
            {
                "input": {
                    "tool_name": "vod_enhance_video",
                    "arguments": {
                        "input": {
                            "video_url": "https://example.com/input.mp4",
                            "persist": False,
                        }
                    },
                }
            },
        )
        assert denied_scope["result"]["isError"] is True

        submitted = await compatibility_call(
            client,
            "tenant-a",
            "ark_job_submit",
            {
                "input": {
                    "tool_name": "vod_enhance_video",
                    "arguments": {
                        "input": {
                            "video_url": "https://example.com/input.mp4",
                            "persist": False,
                        }
                    },
                }
            },
        )
        job_id = submitted["result"]["structuredContent"]["job_id"]

        for operation in ("ark_job_get", "ark_job_cancel"):
            other_tenant = await compatibility_call(
                client,
                "tenant-b",
                operation,
                {"input": {"job_id": job_id}},
            )
            assert other_tenant["result"]["isError"] is True
            assert "private.mp4" not in str(other_tenant)

        completed = await compatibility_terminal(client, "tenant-a", job_id)

    assert completed["result"]["structuredContent"]["status"] == "completed"
    tool_result = completed["result"]["structuredContent"]["result"]
    assert tool_result["is_error"] is False
    assert tool_result["structured_content"]["source_url"].endswith("private.mp4")
    provider.assert_awaited_once()
