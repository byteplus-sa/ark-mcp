"""Integration tests for ``ark_list_models`` through an MCP client."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastmcp import Client

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import refresh_capability_registry
from ark_mcp.server import create_server


@pytest.fixture
def configured(test_env: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(
        "SEEDANCE_MODEL_BINDINGS",
        '[{"model_id":"dreamina-seedance-2-0-260128","family":"standard"},'
        '{"model_id":"dreamina-seedance-2-5-260628","family":"seedance_2_5"}]',
    )
    get_settings.cache_clear()
    refresh_capability_registry()
    yield
    get_settings.cache_clear()
    refresh_capability_registry()


async def _list_models(arguments: dict[str, Any]) -> dict[str, Any]:
    async with Client(create_server(get_settings())) as client:
        result = await client.call_tool("ark_list_models", {"input": arguments})
    assert result.structured_content is not None
    return result.structured_content


def _by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {model["model_id"]: model for model in payload["models"]}


async def test_lists_every_configured_kind(configured: None) -> None:
    models = _by_id(await _list_models({}))
    assert {model["kind"] for model in models.values()} == {"video", "image", "understanding"}
    seedance_2_5 = models["dreamina-seedance-2-5-260628"]
    assert seedance_2_5["tool"] == "seedance_create_task"
    assert seedance_2_5["video"]["duration_seconds"] == [4, 30]
    assert seedance_2_5["video"]["supports_draft"] is True
    assert seedance_2_5["video"]["task_types"] == ["auto", "reference", "edit", "extend"]
    seedance_2 = models["dreamina-seedance-2-0-260128"]
    assert seedance_2["is_default"] is True
    assert seedance_2["video"]["task_types"] == []
    assert seedance_2["video"]["resolutions"][-1] == "4k"


async def test_kind_filter_limits_results(configured: None) -> None:
    payload = await _list_models({"kind": "image"})
    assert [model["kind"] for model in payload["models"]] == ["image"]
    assert payload["models"][0]["image"]["supports_transparent_background"] is True


async def test_understanding_models_report_modalities_and_defaults(configured: None) -> None:
    models = _by_id(await _list_models({"kind": "understanding"}))
    audio_model = models["seed-2-0-lite-260428"]
    assert audio_model["understanding"]["modalities"] == ["image", "video", "audio"]
    assert audio_model["is_default"] is True
    turbo = models["dola-seed-2-1-turbo-260628"]
    assert "audio" not in turbo["understanding"]["modalities"]
    assert turbo["tool"] == "seed_understand"


async def test_disabled_services_explain_how_to_enable_them(configured: None) -> None:
    services = {item["service"]: item for item in (await _list_models({}))["services"]}
    assert services["modelark"] == {"service": "modelark", "enabled": True, "reason": None}
    premium = services["seedance_2_5_premium"]
    assert premium["enabled"] is False
    assert "BYTEPLUS_MODELARK_SEEDANCE_2_5_PREMIUM_ENABLED" in premium["reason"]
    assert services["seed3d"]["enabled"] is False


async def test_reasons_never_contain_secret_values(configured: None) -> None:
    payload = await _list_models({})
    rendered = str(payload)
    assert "sk-test-modelark" not in rendered
    assert "ak-test-tos" not in rendered


async def test_no_models_without_modelark_key(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    refresh_capability_registry()
    try:
        payload = await _list_models({})
        assert payload["models"] == []
        services = {item["service"]: item for item in payload["services"]}
        assert services["modelark"]["enabled"] is False
        assert services["modelark"]["reason"] == "Set BYTEPLUS_MODELARK_API_KEY."
    finally:
        get_settings.cache_clear()
        refresh_capability_registry()


async def test_invalid_kind_is_rejected(configured: None) -> None:
    async with Client(create_server(get_settings())) as client:
        result = await client.call_tool(
            "ark_list_models", {"input": {"kind": "audio"}}, raise_on_error=False
        )
    assert result.is_error
