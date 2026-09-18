"""Cross-client background jobs over one shared local-auth HTTP server.

Regression guard for the multi-client workflow: one ``ark-mcp`` process serves
several MCP clients that share a single Docket queue, so a job submitted by one
client is pollable by another. Each client is an independent HTTP session; no
real sockets are used because the suite disables them globally.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastmcp import FastMCP

from ark_mcp.config.env import Settings
from ark_mcp.providers.modelark.schemas import ChatCompletionProviderResponse
from ark_mcp.providers.modelark.understanding import SeedUnderstandingService
from ark_mcp.server import create_server

_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Mcp-Protocol-Version": "2026-07-28",
}

_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {
        "extensions": {"io.modelcontextprotocol/tasks": {}}
    },
}


def _local_http_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        MCP_TRANSPORT="http",
        MCP_HOST="127.0.0.1",
        MCP_AUTH_MODE="local",
        MCP_ALLOWED_HOSTS="testserver",
        ARTIFACT_DIR=str(tmp_path / "artifacts"),
        BYTEPLUS_MODELARK_API_KEY="test-key",  # pragma: allowlist secret
    )


@asynccontextmanager
async def _shared_local_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[FastMCP, httpx.AsyncClient, httpx.AsyncClient]]:
    async def generate(
        _self: SeedUnderstandingService, _request: object
    ) -> tuple[ChatCompletionProviderResponse, None]:
        await asyncio.sleep(0.2)
        return (
            ChatCompletionProviderResponse.model_validate(
                {
                    "id": "shared-http-test",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": "shared completed"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                }
            ),
            None,
        )

    async def close(_self: SeedUnderstandingService) -> None:
        return None

    monkeypatch.setattr(SeedUnderstandingService, "generate", generate)
    monkeypatch.setattr(SeedUnderstandingService, "close", close)

    settings = _local_http_settings(tmp_path)
    server = create_server(settings)
    app = server.http_app(
        path="/mcp",
        stateless_http=True,
        json_response=True,
        host_origin_protection=True,
        allowed_hosts=settings.allowed_hosts,
        allowed_origins=settings.allowed_origins,
    )
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as first,
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as second,
    ):
        yield server, first, second


async def _call_tool(
    client: httpx.AsyncClient, name: str, arguments: dict[str, Any], request_id: int
) -> dict[str, Any]:
    response = await client.post(
        "/mcp",
        headers={**_HEADERS, "Mcp-Method": "tools/call", "Mcp-Name": name},
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments, "_meta": _META},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.asyncio
async def test_two_clients_share_one_http_server_job_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_env: None
) -> None:
    async with _shared_local_server(tmp_path, monkeypatch) as (_, submitter, poller):
        capabilities = await _call_tool(submitter, "ark_job_capabilities", {}, 1)
        targets = capabilities["result"]["structuredContent"]["targets"]
        assert any(target["tool_name"] == "seed_understand" for target in targets)

        submitted = await _call_tool(
            submitter,
            "ark_job_submit",
            {
                "input": {
                    "tool_name": "seed_understand",
                    "arguments": {"input": {"prompt": "Test"}},
                }
            },
            2,
        )
        job_id = submitted["result"]["structuredContent"]["job_id"]

        async with asyncio.timeout(10):
            while True:
                polled = await _call_tool(poller, "ark_job_get", {"input": {"job_id": job_id}}, 3)
                snapshot = polled["result"]["structuredContent"]
                if snapshot["status"] != "working":
                    break
                await asyncio.sleep(0.02)

    assert snapshot["status"] == "completed"
    assert snapshot["result"]["structured_content"]["choices"][0]["content"] == "shared completed"


@pytest.mark.asyncio
async def test_unknown_job_reports_server_instance_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _shared_local_server(tmp_path, monkeypatch) as (_, _submitter, poller):
        polled = await _call_tool(poller, "ark_job_get", {"input": {"job_id": "x" * 43}}, 1)

    result = polled["result"]
    assert result["isError"] is True
    assert "server instance" in result["content"][0]["text"]
