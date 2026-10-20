"""Contract tests for the ModelArk pricing catalog adapter (``ListModelChargeItems``)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.byteplus_openapi.signing import OpenApiCredentials
from ark_mcp.providers.modelark.openapi import ModelArkOpenApiGateway
from ark_mcp.providers.modelark.pricing import PricingService

BASE = "https://ark-openapi.test.example.com"


def _service() -> PricingService:
    return PricingService(
        ModelArkOpenApiGateway(
            credentials=OpenApiCredentials(
                "AKLTtest", "secret-test", "sts-token"
            ),  # pragma: allowlist secret
            region="ap-southeast-1",
            base_url=BASE,
            timeout=5,
            connect_timeout=5,
        )
    )


def _item(name: str, *, state: str = "Available") -> dict:
    return {
        "FoundationModelName": name,
        "DisplayName": name.title(),
        "State": state,
        "ChargeItems": [
            {
                "Type": "InferencePrompt",
                "Price": 0.0004,
                "OriginalPrice": 0.0005,
                "UnitCode": "K tokens",
            },
            {
                "Type": "InferenceCompletion",
                "Price": 0.002,
                "OriginalPrice": 0.0025,
                "UnitCode": "K tokens",
            },
            {"Type": "Ignored", "OriginalPrice": 1},
        ],
    }


def _envelope(items: list[dict], total: int | None = None) -> dict:
    return {
        "ResponseMetadata": {"RequestId": "req-1", "Action": "ListModelChargeItems"},
        "Result": {
            "Items": items,
            "PageNumber": 1,
            "PageSize": 100,
            "TotalCount": len(items) if total is None else total,
        },
    }


@respx.mock
async def test_fetch_sends_filter_and_parses_prices() -> None:
    route = respx.post(f"{BASE}/").mock(
        return_value=httpx.Response(200, json=_envelope([_item("dola-seed-2-1-turbo")]))
    )
    service = _service()
    try:
        prices = await service.fetch(["dola-seed-2-1-turbo", "dola-seed-2-1-turbo"])
    finally:
        await service.close()

    request = route.calls.last.request
    assert request.url.params["Action"] == "ListModelChargeItems"
    assert request.url.params["Version"] == "2024-01-01"
    assert request.headers["Authorization"].startswith("HMAC-SHA256 Credential=AKLTtest/")
    body = json.loads(request.content)
    assert body["Filter"] == {"FoundationModelNames": ["dola-seed-2-1-turbo"]}
    assert body["PageNumber"] == 1

    pricing = prices["dola-seed-2-1-turbo"]
    assert pricing.available is True
    prompt = pricing.charge_item("InferencePrompt")
    assert prompt is not None
    assert prompt.price == 0.0004
    assert prompt.list_price == 0.0005
    assert prompt.price_per_million_tokens == pytest.approx(0.4)
    assert pricing.charge_item("Ignored") is None


@respx.mock
async def test_fetch_follows_pagination_until_total_reached() -> None:
    route = respx.post(f"{BASE}/").mock(
        side_effect=[
            httpx.Response(200, json=_envelope([_item("model-a")], total=2)),
            httpx.Response(200, json=_envelope([_item("model-b")], total=2)),
        ]
    )
    service = _service()
    try:
        prices = await service.fetch(["model-a", "model-b"])
    finally:
        await service.close()

    assert set(prices) == {"model-a", "model-b"}
    assert route.call_count == 2
    assert json.loads(route.calls.last.request.content)["PageNumber"] == 2


@respx.mock
async def test_fetch_marks_unavailable_models() -> None:
    respx.post(f"{BASE}/").mock(
        return_value=httpx.Response(200, json=_envelope([_item("model-a", state="Unavailable")]))
    )
    service = _service()
    try:
        prices = await service.fetch(["model-a"])
    finally:
        await service.close()

    assert prices["model-a"].available is False


@respx.mock
async def test_fetch_without_names_makes_no_request() -> None:
    route = respx.post(f"{BASE}/").mock(return_value=httpx.Response(200, json=_envelope([])))
    service = _service()
    try:
        assert await service.fetch([]) == {}
    finally:
        await service.close()

    assert route.call_count == 0


@respx.mock
async def test_fetch_returns_empty_when_catalog_has_no_match() -> None:
    respx.post(f"{BASE}/").mock(return_value=httpx.Response(200, json=_envelope([])))
    service = _service()
    try:
        assert await service.fetch(["unknown-model"]) == {}
    finally:
        await service.close()


@respx.mock
async def test_fetch_surfaces_auth_errors() -> None:
    respx.post(f"{BASE}/").mock(
        return_value=httpx.Response(
            400,
            json={
                "ResponseMetadata": {
                    "RequestId": "req-1",
                    "Error": {"Code": "InvalidAuthorization", "Message": "bad header"},
                }
            },
        )
    )
    service = _service()
    try:
        with pytest.raises(ProviderError) as info:
            await service.fetch(["model-a"])
    finally:
        await service.close()

    assert info.value.error.code == "InvalidAuthorization"
