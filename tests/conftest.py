"""Global pytest fixtures that keep the suite deterministic and offline."""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

_SETTINGS_ENV_PREFIXES = (
    "BYTEPLUS_",
    "MCP_",
    "FASTMCP_",
    "LAS_",
    "SEEDREAM_",
    "SEEDANCE_",
    "TOS_",
    "S3_",
    "OBJECT_STORAGE_",
    "SEED_SPEECH_",
    "ARTIFACT_",
    "PROVIDER_",
    "PRINCIPAL_",
    "DAILY_",
    "MODELARK_",
    "ARK_",
    "PERSISTENCE_",
    "READINESS_",
    "RATE_LIMIT_",
)


@pytest.fixture(autouse=True)
def isolate_settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isolate Settings from the shell environment and the developer's ``.env``.

    pydantic-settings reads ``os.environ`` and, for ``get_settings()``, the
    project ``.env`` file. Clear the tracked prefixes from ``os.environ`` and
    pin the model-binding variables to empty lists so a developer's ``.env``
    cannot leak real model bindings (e.g. a Seedance 2.5 binding) or the
    Seedance 2.5 Premium flag into tests.
    Tests that need a value set it via ``monkeypatch.setenv`` after this
    fixture runs, which overrides these pins.
    """
    for key in list(os.environ):
        if key.startswith(_SETTINGS_ENV_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("SEEDREAM_MODEL_BINDINGS", "[]")
    monkeypatch.setenv("SEEDANCE_MODEL_BINDINGS", "[]")
    monkeypatch.setenv("SEED_UNDERSTANDING_MODEL_BINDINGS", "[]")
    # A developer .env with the whitelist-only Premium flag on would otherwise
    # register the Premium tools and bind the Premium model in every test.
    monkeypatch.setenv("BYTEPLUS_MODELARK_SEEDANCE_2_5_PREMIUM_ENABLED", "false")
    from ark_mcp.config.env import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def deterministic_public_dns(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Resolve test hostnames without consulting external DNS."""
    monkeypatch.setattr(
        "ark_mcp.security.url_policy.system_resolver",
        lambda _hostname, _port: ("93.184.216.34",),
    )
    yield


@pytest.fixture(autouse=True)
def block_external_sockets(socket_disabled: None) -> Iterator[None]:
    """Fail every test that attempts real network I/O."""
    yield
