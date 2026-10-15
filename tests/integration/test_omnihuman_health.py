"""OmniHuman appears in the health resource and the readiness probe like its siblings."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastmcp import Client

from ark_mcp.config.env import Settings
from ark_mcp.providers.base import BaseHttpGateway
from ark_mcp.server import create_server


def _settings(tmp_path: Path, *, enabled: bool) -> Settings:
    return Settings(
        _env_file=None,
        ARTIFACT_DIR=str(tmp_path / ".artifacts"),
        BYTEPLUS_CV_API_KEY="cv-test",  # pragma: allowlist secret
        BYTEPLUS_OMNIHUMAN_ENABLED=enabled,
        READINESS_CHECK_PROVIDERS=True,
    )


@pytest.mark.parametrize("enabled", [True, False])
async def test_health_resource_reports_omnihuman(tmp_path: Path, enabled: bool) -> None:
    server = create_server(_settings(tmp_path, enabled=enabled))

    async with Client(server) as client:
        content = await client.read_resource("seed-health://status")

    assert f"OmniHuman configured: {enabled}" in content[0].text


@pytest.mark.parametrize(("healthy", "status_code"), [(True, 200), (False, 503)])
async def test_ready_probes_vision_cv_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, healthy: bool, status_code: int
) -> None:
    async def health_check(self: BaseHttpGateway, *, timeout_seconds: float = 2.0) -> bool:
        return healthy

    monkeypatch.setattr(BaseHttpGateway, "health_check", health_check)
    server = create_server(_settings(tmp_path, enabled=True))
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        ready = await client.get("/ready")

    assert ready.status_code == status_code
    assert ready.json()["providers"]["vision_cv"] == ("reachable" if healthy else "unreachable")


async def test_ready_skips_vision_cv_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def health_check(self: BaseHttpGateway, *, timeout_seconds: float = 2.0) -> bool:
        return True

    monkeypatch.setattr(BaseHttpGateway, "health_check", health_check)
    server = create_server(_settings(tmp_path, enabled=False))
    app = server.http_app(path="/mcp", stateless_http=True, json_response=True)

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        ready = await client.get("/ready")

    assert "vision_cv" not in ready.json().get("providers", {})
