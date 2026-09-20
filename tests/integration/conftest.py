"""Shared test fixtures for integration tests.

Provides a fake MCP Context, a temp artifact store, and env var isolation
so tool handlers can be tested end-to-end without real credentials or network.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast

import pytest

from ark_mcp.artifacts.store import ArtifactStore
from ark_mcp.runtime import RuntimeServices, close_runtime_services, create_runtime_services
from tests.fixtures.fake_context import FakeContext


@pytest.fixture
def test_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Set test environment variables with fake credentials."""
    monkeypatch.setenv("BYTEPLUS_MODELARK_API_KEY", "sk-test-modelark")
    monkeypatch.setenv("BYTEPLUS_SEED_SPEECH_API_KEY", "sk-test-speech")
    monkeypatch.setenv("BYTEPLUS_VOD_MEDIAKIT_API_KEY", "test-mediakit-key")
    monkeypatch.setenv("BYTEPLUS_MODELARK_BASE_URL", "https://ark.test.example.com/api/v3")
    monkeypatch.setenv("BYTEPLUS_SEED_AUDIO_BASE_URL", "https://voice.test.example.com")
    monkeypatch.setenv("BYTEPLUS_VOD_MEDIAKIT_BASE_URL", "https://mediakit.test.example.com/api/v1")
    monkeypatch.setenv("SEED_SPEECH_ASR_BASE_URL", "https://voice.test.example.com")
    monkeypatch.setenv("SEEDREAM_DEFAULT_MODEL", "dola-seedream-5-0-pro-260628")
    # Pinned, not inherited: settings read the working directory's .env, so a
    # developer who configures "fast" locally must not fail a suite that
    # asserts the shipped default.
    monkeypatch.setenv("SEEDREAM_PROMPT_OPTIMIZATION_MODE", "standard")
    monkeypatch.setenv("SEEDANCE_DEFAULT_MODEL", "dreamina-seedance-2-0-260128")
    monkeypatch.setenv("SEED_UNDERSTANDING_DEFAULT_MODEL", "dola-seed-2-1-turbo-260628")
    monkeypatch.setenv("ARTIFACT_DIR", str(tmp_path / ".artifacts"))
    monkeypatch.setenv("ARTIFACT_BACKEND", "filesystem")
    monkeypatch.setenv("TOS_ACCESS_KEY", "ak-test-tos")
    monkeypatch.setenv("TOS_SECRET_KEY", "sk-test-tos")
    monkeypatch.setenv("TOS_BUCKET", "test-bucket")

    # Clear cached settings and capability registry.
    from ark_mcp.config.env import get_settings
    from ark_mcp.config.model_capabilities import refresh_capability_registry

    get_settings.cache_clear()
    refresh_capability_registry()

    yield

    get_settings.cache_clear()
    refresh_capability_registry()


@pytest.fixture
async def fake_ctx(test_env: None) -> AsyncIterator[FakeContext]:
    from ark_mcp.config.env import get_settings
    from tests.fixtures.fake_context import FakeContext

    runtime = await create_runtime_services(get_settings())
    try:
        yield FakeContext(lifespan_context={"runtime": runtime})
    finally:
        await close_runtime_services(runtime)


@pytest.fixture
def temp_store(fake_ctx: FakeContext) -> ArtifactStore:
    """Return a temp filesystem artifact store."""
    runtime = cast("RuntimeServices", fake_ctx.lifespan_context["runtime"])
    return runtime.artifact_store
