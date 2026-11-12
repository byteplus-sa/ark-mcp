"""Configuration-only health diagnostics through the MCP resource contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastmcp import Client

from ark_mcp.config.env import Settings
from ark_mcp.providers.base import BaseHttpGateway
from ark_mcp.server import create_server

MODEL = "dola-seedream-5-5-261028"
PRO_MODEL = "dola-seedream-5-0-pro-260628"


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "ARTIFACT_DIR": str(tmp_path / ".artifacts"),
        "OUTPUT_ROOTS": "",
        "BYTEPLUS_MODELARK_API_KEY": "modelark-test-key",  # pragma: allowlist secret
    }
    return Settings(**(values | overrides))


async def _health_text(settings: Settings, *, roots: list[str] | None = None) -> str:
    async with Client(create_server(settings), roots=roots) as client:
        content = await client.read_resource("seed-health://status")
    assert len(content) == 1
    assert isinstance(content[0].text, str)
    return content[0].text


@pytest.mark.parametrize(
    ("model", "family", "overrides"),
    [
        (MODEL, "seedream_5_5", {}),
        (PRO_MODEL, "pro", {"SEEDREAM_DEFAULT_MODEL": PRO_MODEL}),
        (
            "ep-custom-image",
            "lite",
            {"SEEDREAM_DEFAULT_MODEL": "ep-custom-image", "SEEDREAM_MODEL_FAMILY": "lite"},
        ),
        (
            "ep-bound-image",
            "4x",
            {
                "SEEDREAM_DEFAULT_MODEL": "ep-bound-image",
                "SEEDREAM_MODEL_FAMILY": "pro",
                "SEEDREAM_MODEL_BINDINGS": [
                    {"model_id": MODEL, "family": "seedream_5_5"},
                    {"model_id": "ep-bound-image", "family": "4x"},
                ],
            },
        ),
    ],
)
async def test_health_reports_effective_default_binding(
    tmp_path: Path, model: str, family: str, overrides: dict[str, Any]
) -> None:
    text = await _health_text(_settings(tmp_path, **overrides))

    assert f"Seedream default model: {model}\n" in text
    assert f"Seedream default family: {family}\n" in text
    assert "configuration only" in text
    assert "provider authorization is not checked" in text
    assert "ModelArk configured: True\n" in text
    assert "Build: " in text
    assert "installed:" in text
    assert "Status: healthy\n" in text


async def test_health_reports_defaults_even_without_credentials(tmp_path: Path) -> None:
    text = await _health_text(_settings(tmp_path, BYTEPLUS_MODELARK_API_KEY=""))

    assert "ModelArk configured: False\n" in text
    assert f"Seedream default model: {MODEL}\n" in text
    assert "Seedream default family: seedream_5_5\n" in text
    assert "provider authorization is not checked" in text


@pytest.mark.parametrize("output_roots", ["", " ,  , "])
async def test_background_export_without_roots_is_not_configured(
    tmp_path: Path, output_roots: str
) -> None:
    text = await _health_text(
        _settings(tmp_path, OUTPUT_ROOTS=output_roots), roots=[tmp_path.as_uri()]
    )

    assert "Background-job local export: not configured" in text
    assert "OUTPUT_ROOTS" in text
    assert "background jobs cannot use client MCP roots" in text
    assert str(tmp_path) not in text


@pytest.mark.parametrize("output_roots", ["", "/private/configured-export", "relative-root"])
async def test_http_disables_background_local_export(tmp_path: Path, output_roots: str) -> None:
    text = await _health_text(_settings(tmp_path, MCP_TRANSPORT="http", OUTPUT_ROOTS=output_roots))

    assert "Transport: http\n" in text
    assert "Background-job local export: disabled (stdio only)\n" in text
    if output_roots:
        assert output_roots not in text


@pytest.mark.parametrize(
    "output_roots",
    [
        "relative-root",
        "relative-root, another-relative-root",
        "~ark-mcp-health-nonexistent-user/export",
        "/private/invalid\0root",
    ],
)
async def test_invalid_root_configuration_does_not_break_health_or_leak_values(
    tmp_path: Path, output_roots: str
) -> None:
    text = await _health_text(_settings(tmp_path, OUTPUT_ROOTS=output_roots))

    assert "Status: healthy\n" in text
    assert "Background-job local export: not configured" in text
    assert "OUTPUT_ROOTS has no usable absolute roots" in text
    assert output_roots not in text


@pytest.mark.parametrize("root_kind", ["directory", "missing", "file", "mixed"])
async def test_absolute_roots_need_request_preflight_not_readiness(
    tmp_path: Path, root_kind: str
) -> None:
    root = tmp_path / "private-export"
    if root_kind == "directory":
        root.mkdir()
    elif root_kind == "file":
        root.write_bytes(b"not a directory")
    output_roots = f"relative-root, {root}" if root_kind == "mixed" else str(root)

    text = await _health_text(_settings(tmp_path, OUTPUT_ROOTS=output_roots))

    assert "Background-job local export: configured; needs request preflight\n" in text
    assert "filesystem readiness is not checked" in text
    assert str(root) not in text
    assert "relative-root" not in text
    if root_kind in {"missing", "mixed"}:
        assert not root.exists()


async def test_health_does_not_probe_providers_or_reveal_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(
        tmp_path,
        OUTPUT_ROOTS=str(tmp_path / "private-output"),
        BYTEPLUS_SEED_SPEECH_API_KEY="speech-test-key",  # pragma: allowlist secret
        BYTEPLUS_VOD_MEDIAKIT_API_KEY="vod-test-key",  # pragma: allowlist secret
        BYTEPLUS_CV_API_KEY="cv-test-key",  # pragma: allowlist secret
        BYTEPLUS_MODELARK_ACCESS_KEY="test-access-key",  # pragma: allowlist secret
        BYTEPLUS_MODELARK_SECRET_KEY="test-secret-key",  # pragma: allowlist secret
        BYTEPLUS_MODELARK_SESSION_TOKEN="test-session-token",  # pragma: allowlist secret
        BYTEPLUS_MODELARK_PRICING_REFRESH_SECONDS=0,
        READINESS_CHECK_PROVIDERS=True,
    )
    probe = AsyncMock(side_effect=AssertionError("Health must not probe providers"))
    monkeypatch.setattr(BaseHttpGateway, "health_check", probe)

    text = await _health_text(settings)

    probe.assert_not_awaited()
    for value in (
        settings.modelark_api_key,
        settings.seed_speech_api_key,
        settings.vod_mediakit_api_key,
        settings.cv_api_key,
        settings.modelark_access_key,
        settings.modelark_secret_key,
        settings.modelark_session_token,
        settings.artifact_dir,
        settings.output_roots,
    ):
        assert value not in text


async def test_health_does_not_probe_output_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, OUTPUT_ROOTS=str(tmp_path / "private-output"))
    async with Client(create_server(settings)) as client:
        with monkeypatch.context() as read_patch:
            read_patch.setattr("ark_mcp.server._build_stamp", lambda: "test-build")
            read_patch.setattr(
                Path, "resolve", lambda *args, **kwargs: pytest.fail("Filesystem resolution")
            )
            read_patch.setattr(Path, "stat", lambda *args, **kwargs: pytest.fail("Filesystem stat"))
            read_patch.setattr(
                "os.access", lambda *args, **kwargs: pytest.fail("Filesystem access")
            )
            content = await client.read_resource("seed-health://status")

    assert "Build: test-build\n" in content[0].text
    assert "Background-job local export: configured; needs request preflight\n" in content[0].text
