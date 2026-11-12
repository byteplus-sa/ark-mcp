"""Cross-client background jobs over local-auth and shared-key HTTP servers.

Regression guard for the multi-client workflow: one ``ark-mcp`` process serves
several MCP clients that share a single Docket queue, so a job submitted by one
client is pollable by another. Each client is an independent HTTP session; no
real sockets are used because the suite disables them globally.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
import secrets
import shlex
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from fastmcp import FastMCP
from fastmcp.server.auth import require_scopes

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


def _http_settings(tmp_path: Path, token: str | None = None) -> Settings:
    return Settings(
        _env_file=None,
        MCP_TRANSPORT="http",
        MCP_HOST="127.0.0.1",
        MCP_AUTH_MODE="api_key" if token else "local",
        MCP_API_KEY_SHA256=hashlib.sha256(token.encode()).hexdigest() if token else "",
        MCP_ALLOWED_HOSTS="testserver",
        ARTIFACT_DIR=str(tmp_path / "artifacts"),
        BYTEPLUS_MODELARK_API_KEY="test-key",  # pragma: allowlist secret
    )


@asynccontextmanager
async def _shared_http_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, auth_mode: str = "local"
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

    token = secrets.token_hex(32) if auth_mode == "api_key" else None
    settings = _http_settings(tmp_path, token)
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
        httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {token}"} if token else {},
        ) as first,
        httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
            headers={"Authorization": f"Bearer {token}"} if token else {},
        ) as second,
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
@pytest.mark.parametrize("auth_mode", ["local", "api_key"])
async def test_two_clients_share_one_http_server_job_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, test_env: None, auth_mode: str
) -> None:
    async with _shared_http_server(tmp_path, monkeypatch, auth_mode=auth_mode) as (
        _,
        submitter,
        poller,
    ):
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
    async with _shared_http_server(tmp_path, monkeypatch) as (_, _submitter, poller):
        polled = await _call_tool(poller, "ark_job_get", {"input": {"job_id": "x" * 43}}, 1)

    result = polled["result"]
    assert result["isError"] is True
    assert "server instance" in result["content"][0]["text"]


async def test_shared_key_http_rejects_missing_wrong_and_query_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _shared_http_server(tmp_path, monkeypatch, auth_mode="api_key") as (_, client, _):
        original = client.headers.pop("Authorization")
        for authorization in (None, "Bearer wrong", "Basic wrong", "Bearer "):
            headers = dict(_HEADERS)
            if authorization is not None:
                headers["Authorization"] = authorization
            response = await client.post(
                "/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            )
            assert response.status_code == 401
        response = await client.post(
            "/mcp",
            params={"api_key": original.removeprefix("Bearer ")},
            headers=_HEADERS,
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        assert response.status_code == 401


async def test_container_ci_probe_reaches_bearer_authentication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workflow_path = Path(__file__).resolve().parents[2] / ".github/workflows/ci.yml"
    workflow = yaml.load(workflow_path.read_text(), Loader=yaml.BaseLoader)
    step = next(
        step
        for step in workflow["jobs"]["container"]["steps"]
        if step.get("name") == "Exercise health endpoint"
    )
    command = step["run"].replace("\\\n", "")
    match = re.search(r"\bcurl\s+([^\n]*?)\s+http://127\.0\.0\.1:3000/mcp", command)
    assert match is not None
    arguments = shlex.split(match.group(1))
    method = None
    headers = {}
    content = None
    for index, argument in enumerate(arguments):
        if argument in ("--request", "-X"):
            method = arguments[index + 1]
        elif argument in ("--header", "-H"):
            name, value = arguments[index + 1].split(":", 1)
            headers[name] = value.strip()
        elif argument in ("--data", "--data-raw", "-d"):
            content = arguments[index + 1]
    method = method or ("POST" if content is not None else "GET")
    async with _shared_http_server(tmp_path, monkeypatch, auth_mode="api_key") as (_, client, _):
        client.headers.pop("Authorization")
        response = await client.request(method, "/mcp", headers=headers, content=content)
        assert response.status_code == 401


async def test_shared_key_cannot_call_account_admin_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _shared_http_server(tmp_path, monkeypatch, auth_mode="api_key") as (
        server,
        client,
        _,
    ):

        @server.tool(auth=require_scopes("assets:write"))
        def account_admin_probe() -> str:
            """A denied operation must never execute."""
            raise AssertionError("Shared key reached an account-wide admin operation")

        response = await _call_tool(client, "account_admin_probe", {}, 1)
        assert response["result"]["isError"] is True
        assert "unknown tool" in response["result"]["content"][0]["text"].lower()


async def test_http_server_has_no_oauth_or_jwks_routes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _shared_http_server(tmp_path, monkeypatch, auth_mode="api_key") as (_, client, _):
        for path in (
            "/.well-known/oauth-protected-resource",
            "/.well-known/oauth-authorization-server",
            "/.well-known/jwks.json",
            "/authorize",
            "/token",
            "/register",
        ):
            assert (await client.get(path)).status_code == 404
