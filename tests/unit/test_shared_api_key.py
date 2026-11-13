"""Shared bearer credentials retain a team identity without storing raw keys."""

from __future__ import annotations

import hashlib
import secrets
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from ark_mcp.config.env import Settings
from ark_mcp.security.http_auth import build_auth_provider
from ark_mcp.server import create_server

KEYGEN = Path(__file__).resolve().parents[2] / "scripts/generate_mcp_key.py"


def shared_settings(token: str, **overrides: object) -> Settings:
    return Settings(
        _env_file=None,
        **{
            "MCP_TRANSPORT": "http",
            "MCP_HOST": "0.0.0.0",
            "MCP_AUTH_MODE": "api_key",
            "MCP_API_KEY_SHA256": hashlib.sha256(token.encode()).hexdigest(),
            **overrides,
        },
    )


async def test_shared_key_uses_fixed_team_claims() -> None:
    token = secrets.token_hex(32)
    settings = shared_settings(token, MCP_TENANT_CLAIM="organization")
    provider = build_auth_provider(settings)
    assert provider is not None
    verified = await provider.verify_token(token)
    assert verified is not None
    assert verified.subject == "shared-team"
    assert verified.claims == {"sub": "shared-team", "tenant_id": "shared-team"}
    assert "seedream:generate" in verified.scopes
    assert "artifacts:read" in verified.scopes
    assert not any(scope.startswith(("assets:", "moderation:")) for scope in verified.scopes)
    assert token not in repr(settings)
    assert token not in settings.model_dump_json()
    assert settings.mcp_api_key_sha256.get_secret_value() not in repr(settings)


@pytest.mark.parametrize("invalid", ["", "short", "a" * 64, "x" * 10000, "\u2603" * 64])
async def test_invalid_shared_key_is_rejected(invalid: str) -> None:
    provider = build_auth_provider(shared_settings(secrets.token_hex(32)))
    assert provider is not None
    assert await provider.verify_token(invalid) is None


async def test_rotation_rejects_old_key_without_changing_identity() -> None:
    previous, current = secrets.token_hex(32), secrets.token_hex(32)
    old_provider = build_auth_provider(shared_settings(previous))
    new_provider = build_auth_provider(shared_settings(current))
    assert old_provider is not None and new_provider is not None
    old_identity = await old_provider.verify_token(previous)
    new_identity = await new_provider.verify_token(current)
    assert old_identity is not None and new_identity is not None
    assert old_identity.client_id == new_identity.client_id
    assert old_identity.claims == new_identity.claims
    assert old_identity.scopes == new_identity.scopes
    assert await new_provider.verify_token(previous) is None


@pytest.mark.parametrize("digest", ["", "short", "g" * 64, "a" * 63, "A" * 64, "a" * 65])
def test_api_key_mode_requires_sha256_digest(digest: str) -> None:
    with pytest.raises(ValidationError, match="MCP_API_KEY_SHA256"):
        shared_settings(secrets.token_hex(32), MCP_API_KEY_SHA256=digest)


def test_jwt_mode_is_no_longer_supported() -> None:
    with pytest.raises(ValidationError, match="MCP_AUTH_MODE"):
        shared_settings(secrets.token_hex(32), MCP_AUTH_MODE="jwt")


def test_settings_do_not_expose_retired_identity_provider_configuration() -> None:
    aliases = {
        field.validation_alias
        for field in Settings.model_fields.values()
        if isinstance(field.validation_alias, str)
    }
    assert not any(alias.startswith("MCP_JWT_") for alias in aliases)
    assert "MCP_TENANT_CLAIM" not in aliases
    assert "MCP_PUBLIC_BASE_URL" not in aliases


def test_shared_key_redis_requires_snapshot_encryption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FASTMCP_DOCKET_URL", "redis://localhost:6379/0")
    with pytest.raises(ValueError, match="FASTMCP_TASKS_ENCRYPTION_KEY"):
        create_server(shared_settings(secrets.token_hex(32), ARTIFACT_DIR=str(tmp_path)))


def test_shared_key_warns_when_spend_guardrails_are_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings: list[dict[str, object]] = []
    monkeypatch.setattr(
        "ark_mcp.server.log_warning", lambda event, **fields: warnings.append(fields)
    )
    create_server(shared_settings(secrets.token_hex(32), ARTIFACT_DIR=str(tmp_path)))
    assert any(item.get("disabled") == ["DAILY_BUDGET_USD", "RATE_LIMIT_RPM"] for item in warnings)


def test_keygen_writes_private_key_and_outputs_only_digest(tmp_path: Path) -> None:
    path = tmp_path / "team.key"
    result = subprocess.run(
        [sys.executable, str(KEYGEN), str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    token = path.read_text().strip()
    assert len(token) == 64
    assert bytes.fromhex(token)
    assert path.stat().st_mode & 0o777 == 0o600
    assert token not in result.stdout + result.stderr
    assert (
        result.stdout.strip() == f"MCP_API_KEY_SHA256={hashlib.sha256(token.encode()).hexdigest()}"
    )


@pytest.mark.parametrize("symlink", [False, True])
def test_keygen_refuses_to_overwrite_existing_file(tmp_path: Path, symlink: bool) -> None:
    target = tmp_path / "existing"
    target.write_text("keep")
    path = tmp_path / "team.key" if symlink else target
    if symlink:
        path.symlink_to(target)
    result = subprocess.run(
        [sys.executable, str(KEYGEN), str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert target.read_text() == "keep"
    assert "Cannot create" in result.stderr
