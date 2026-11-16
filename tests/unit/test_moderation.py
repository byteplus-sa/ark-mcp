"""Moderation contract and rate-limit tests."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastmcp import Client
from pydantic import ValidationError

from ark_mcp.config.env import Settings
from ark_mcp.providers.modelark.moderation_limits import ModerationQueryLimiter
from ark_mcp.providers.modelark.openapi import _parse_retry_after
from ark_mcp.server import create_server
from ark_mcp.tools.ark_list_models import describe_services
from ark_mcp.tools.ark_moderation_get import ModerationGetInput


@pytest.mark.parametrize("kind", ["task_id", "asset_id", "request_id"])
def test_input_accepts_bare_ids_and_trims(kind: str) -> None:
    value = ModerationGetInput(id="  opaque-example  ", type=kind)
    assert value.id == "opaque-example"
    assert value.type == kind


@pytest.mark.parametrize(
    "value",
    [
        "",
        "  ",
        None,
        123,
        "x" * 257,
        "a b",
        "a\nb",
        "a\x00b",
        "a\x7fb",
        "https://example.com",
        "asset://example",
        "file:example",
        "a\u200bb",
    ],
)
def test_input_rejects_invalid_ids(value: object) -> None:
    with pytest.raises(ValidationError):
        ModerationGetInput(id=value, type="task_id")


def test_input_boundary_and_extra_fields() -> None:
    assert len(ModerationGetInput(id="x" * 256, type="task_id").id) == 256
    for body in [
        {"id": "example"},
        {"id": "example", "type": "job_id"},
        {"id": "example", "type": None},
        {"id": "example", "type": "task_id", "model": "example"},
    ]:
        with pytest.raises(ValidationError):
            ModerationGetInput.model_validate(body)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("keys", [False, True])
def test_gate_needs_flag_and_complete_keys(enabled: bool, keys: bool) -> None:
    settings = Settings(
        _env_file=None,
        BYTEPLUS_MODELARK_API_KEY="",
        BYTEPLUS_MODELARK_MODERATION_ENABLED=enabled,
        BYTEPLUS_MODELARK_ACCESS_KEY="test-access" if keys else "",
        BYTEPLUS_MODELARK_SECRET_KEY="test-secret" if keys else "",  # pragma: allowlist secret
    )
    assert settings.has_modelark_moderation is (enabled and keys)


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("keys", [False, True])
async def test_registration_and_discovery_use_the_same_gate(enabled: bool, keys: bool) -> None:
    settings = Settings(
        _env_file=None,
        BYTEPLUS_MODELARK_API_KEY="",
        BYTEPLUS_MODELARK_MODERATION_ENABLED=enabled,
        BYTEPLUS_MODELARK_ACCESS_KEY="test-access" if keys else "",
        BYTEPLUS_MODELARK_SECRET_KEY="test-secret" if keys else "",  # pragma: allowlist secret
    )
    names = {tool.name for tool in await create_server(settings).list_tools()}
    expected = enabled and keys
    assert ("ark_moderation_get" in names) is expected
    status = next(
        service for service in describe_services(settings) if service.service == "moderation"
    )
    assert status.enabled is expected
    if not expected:
        assert status.reason
        assert "BYTEPLUS_MODELARK_MODERATION_ENABLED" in status.reason
        assert "BYTEPLUS_MODELARK_ACCESS_KEY" in status.reason
        assert "test-secret" not in status.reason


def test_bearer_key_does_not_enable_moderation() -> None:
    settings = Settings(
        _env_file=None,
        BYTEPLUS_MODELARK_API_KEY="synthetic-bearer",  # pragma: allowlist secret
        BYTEPLUS_MODELARK_MODERATION_ENABLED=True,
    )
    assert settings.has_modelark
    assert not settings.has_modelark_moderation


def test_orphan_session_token_remains_a_startup_error() -> None:
    with pytest.raises(ValidationError, match="SESSION_TOKEN"):
        Settings(
            _env_file=None,
            BYTEPLUS_MODELARK_MODERATION_ENABLED=True,
            BYTEPLUS_MODELARK_SESSION_TOKEN="synthetic-session",
        )


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("keys", [False, True])
def test_startup_recognizes_signed_openapi_configuration(
    enabled: bool, keys: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    create_server(
        Settings(
            _env_file=None,
            BYTEPLUS_MODELARK_MODERATION_ENABLED=enabled,
            BYTEPLUS_MODELARK_ACCESS_KEY="test-access" if keys else "",
            BYTEPLUS_MODELARK_SECRET_KEY="test-secret" if keys else "",  # pragma: allowlist secret
        )
    )
    captured = capsys.readouterr()
    assert ("no_provider_credentials_configured" in captured.err) is (not keys)


@pytest.mark.parametrize("enabled", [False, True])
async def test_mcp_discovery_and_health_report_configuration_without_provider_calls(
    enabled: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden_request(*args: object, **kwargs: object) -> None:
        raise AssertionError("Discovery must not call providers")

    monkeypatch.setattr("httpx.AsyncClient.request", forbidden_request)
    settings = Settings(
        _env_file=None,
        BYTEPLUS_MODELARK_MODERATION_ENABLED=enabled,
        BYTEPLUS_MODELARK_ACCESS_KEY="synthetic-access",
        BYTEPLUS_MODELARK_SECRET_KEY="synthetic-secret",  # pragma: allowlist secret
        BYTEPLUS_MODELARK_PRICING_REFRESH_SECONDS=0,
        ARTIFACT_DIR=str(tmp_path / "artifacts"),
    )
    async with Client(create_server(settings)) as client:
        discovery = await client.call_tool("ark_list_models", {"input": {}})
        assert discovery.structured_content is not None
        assert discovery.structured_content["models"] == []
        status = next(
            service
            for service in discovery.structured_content["services"]
            if service["service"] == "moderation"
        )
        assert status["enabled"] is enabled
        health = await client.read_resource("seed-health://status")
    text = str(health)
    assert f"Moderation lookup configured: {enabled}" in text
    assert "provider authorization is not checked" in text
    assert "synthetic-access" not in text + str(discovery.structured_content)
    assert "synthetic-secret" not in text + str(discovery.structured_content)


@pytest.mark.parametrize("key", ["BYTEPLUS_MODELARK_ACCESS_KEY", "BYTEPLUS_MODELARK_SECRET_KEY"])
@pytest.mark.parametrize("enabled", [False, True])
def test_partial_keys_remain_startup_errors(key: str, enabled: bool) -> None:
    with pytest.raises(ValidationError, match="must both be set"):
        Settings(_env_file=None, BYTEPLUS_MODELARK_MODERATION_ENABLED=enabled, **{key: "test"})


async def test_quota_is_shared_atomic_and_expires_at_boundary() -> None:
    now = 100.0
    limiter = ModerationQueryLimiter(clock=lambda: now)
    results = await asyncio.gather(*(limiter.try_reserve() for _ in range(30)))
    assert results.count(0.0) == 10
    assert results.count(60.0) == 20
    now = 159.5
    assert await limiter.try_reserve() == 0.5
    now = 160.0
    assert await limiter.try_reserve() == 0.0
    assert await ModerationQueryLimiter(clock=lambda: now).try_reserve() == 0.0


async def test_quota_uses_rolling_window() -> None:
    now = 0.0
    limiter = ModerationQueryLimiter(clock=lambda: now)
    for _ in range(5):
        assert await limiter.try_reserve() == 0.0
    now = 30.0
    for _ in range(5):
        assert await limiter.try_reserve() == 0.0
    now = 60.0
    for _ in range(5):
        assert await limiter.try_reserve() == 0.0
    assert await limiter.try_reserve() == 30.0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("garbage", None),
        ("-1", None),
        ("NaN", None),
        ("inf", None),
        ("1e999", None),
        ("0", 0.0),
        ("1.5", 1.5),
        ("Fri, 30 Oct 2026 00:00:05 GMT", 5.0),
        ("Thu, 29 Oct 2026 00:00:00 GMT", 0.0),
        ("Fri, 30 Oct 2026 00:00:05", None),
    ],
)
def test_retry_after_is_safe(value: str | None, expected: float | None) -> None:
    assert _parse_retry_after(value, now=datetime(2026, 10, 30, tzinfo=UTC)) == expected


@pytest.mark.parametrize("mode", [[], ["--offline"]])
def test_offline_smoke_ignores_inherited_environment(mode: list[str], tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "MCP_AUTH_MODE=invalid\nBYTEPLUS_MODELARK_SECRET_KEY=synthetic-sentinel\n"  # pragma: allowlist secret
    )
    environment = os.environ | {
        "ARK_MCP_ENV_FILE": str(dotenv),
        "MCP_AUTH_MODE": "invalid",
        "BYTEPLUS_MODELARK_SECRET_KEY": "synthetic-sentinel",  # pragma: allowlist secret
        "FASTMCP_DOCKET_URL": "redis://invalid.example",
    }
    command = [sys.executable, str(Path(__file__).parents[2] / "scripts/smoke_moderation.py")]
    result = subprocess.run(
        command + mode, env=environment, cwd=tmp_path, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout) == {"status": "pass", "mode": "offline", "count": 1}
    assert "synthetic-sentinel" not in result.stdout + result.stderr
    assert result.stderr == ""


def test_smoke_live_requires_explicit_configuration(tmp_path: Path) -> None:
    command = [
        sys.executable,
        str(Path(__file__).parents[2] / "scripts/smoke_moderation.py"),
        "--live",
    ]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"status": "fail", "code": "CONFIGURATION_REQUIRED"}
    assert result.stderr == ""


def test_smoke_rejects_conflicting_modes() -> None:
    command = [
        sys.executable,
        str(Path(__file__).parents[2] / "scripts/smoke_moderation.py"),
        "--live",
        "--offline",
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2
