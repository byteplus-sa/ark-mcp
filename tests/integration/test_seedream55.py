from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from fastmcp import Client

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import refresh_capability_registry
from ark_mcp.server import create_server

MODEL = "dola-seedream-5-5-261028"
ARK_BASE = "https://ark.test.example.com/api/v3"


@pytest.fixture
def configured(test_env: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("SEEDREAM_DEFAULT_MODEL")
    get_settings.cache_clear()
    refresh_capability_registry()
    yield
    get_settings.cache_clear()
    refresh_capability_registry()


async def _image_job(client: Client, arguments: dict[str, Any]) -> dict[str, Any]:
    submitted = await client.call_tool(
        "ark_job_submit",
        {"input": {"tool_name": "seedream_generate_image", "arguments": {"input": arguments}}},
    )
    assert submitted.structured_content is not None
    job_id = submitted.structured_content["job_id"]
    async with asyncio.timeout(15):
        while True:
            result = await client.call_tool("ark_job_get", {"input": {"job_id": job_id}})
            assert result.structured_content is not None
            state = result.structured_content
            if state["status"] != "working":
                assert state["status"] == "completed", state
                return state["result"]
            await asyncio.sleep(0.02)


async def test_seedream55_discovery_reports_default_and_transparency(configured: None) -> None:
    async with Client(create_server(get_settings())) as client:
        result = await client.call_tool("ark_list_models", {"input": {"kind": "image"}})
    assert result.structured_content is not None
    model = result.structured_content["models"][0]
    assert model["model_id"] == MODEL
    assert model["is_default"]
    assert model["family"] == "seedream_5_5"
    assert model["tool"] == "seedream_generate_image"
    assert model["image"]["supports_transparent_generation"]
    assert not model["image"]["supports_prompt_optimization"]


async def test_seedream55_transparent_dry_run_matches_example(configured: None) -> None:
    with respx.mock(assert_all_called=False) as router:
        async with Client(create_server(get_settings())) as client:
            result = await _image_job(
                client,
                {
                    "model": MODEL,
                    "prompt": "Embroidered heart sticker",
                    "size": "2K",
                    "response_format": "url",
                    "output_format": "png",
                    "background": "transparent",
                    "watermark": True,
                    "dry_run": True,
                },
            )
    assert not router.calls
    assert not result["is_error"]
    data = result["structured_content"]
    assert data["dry_run"]
    assert data["preview"]["requests"] == [
        {
            "model": MODEL,
            "prompt": "Embroidered heart sticker",
            "size": "2K",
            "response_format": "url",
            "output_format": "png",
            "background": "transparent",
            "watermark": True,
            "stream": False,
        }
    ]


@pytest.mark.parametrize("variations", [1, 2])
@pytest.mark.parametrize("persist", [True, False])
async def test_seedream55_job_keeps_png_metadata_when_format_is_omitted(
    configured: None, variations: int, persist: bool
) -> None:
    image_bytes = b"test-png-output"
    item = (
        {"b64_json": base64.b64encode(image_bytes).decode()}
        if persist
        else {"url": "https://example.com/output.png"}
    )
    with respx.mock:
        route = respx.post(f"{ARK_BASE}/images/generations").mock(
            return_value=httpx.Response(200, json={"created": 1721400000, "data": [item]})
        )
        async with Client(create_server(get_settings())) as client:
            result = await _image_job(
                client,
                {
                    "prompt": "Embroidered heart sticker",
                    "size": "2K",
                    "background": "transparent",
                    "persist": persist,
                    "variations": variations,
                },
            )
            assert not result["is_error"]
            data = result["structured_content"]
            assert data["model"] == MODEL
            artifacts = (
                data["artifacts"]
                if variations == 1
                else [item["artifact"] for item in data["summary"]["variations"]]
            )
            assert len(artifacts) == variations
            assert all(artifact["mime_type"] == "image/png" for artifact in artifacts)
            if persist:
                for artifact in artifacts:
                    resource = await client.read_resource(artifact["uri"])
                    assert base64.b64decode(resource[0].blob) == image_bytes
    assert route.call_count == variations
    for call in route.calls:
        body = json.loads(call.request.content)
        assert body["model"] == MODEL
        assert body["output_format"] == "png"
        assert body["background"] == "transparent"
        assert "image" not in body
        assert "optimize_prompt_options" not in body


async def test_seedream55_invalid_transparent_jpeg_never_calls_provider(configured: None) -> None:
    with respx.mock(assert_all_called=False) as router:
        async with Client(create_server(get_settings())) as client:
            result = await _image_job(
                client,
                {"prompt": "test", "background": "transparent", "output_format": "jpeg"},
            )
    assert not router.calls
    assert result["is_error"]
    assert "requires output_format" in str(result["content"])


async def test_seedream55_provider_access_error_is_not_retried(configured: None) -> None:
    with respx.mock:
        route = respx.post(f"{ARK_BASE}/images/generations").mock(
            return_value=httpx.Response(
                403,
                json={"error": {"code": "AccessDenied", "message": "Early access required"}},
                headers={"X-Request-Id": "req-seedream55-denied"},
            )
        )
        async with Client(create_server(get_settings())) as client:
            result = await _image_job(client, {"prompt": "Embroidered heart sticker"})
    assert route.call_count == 1
    assert result["is_error"]
    assert "req-seedream55-denied" in str(result["content"])
