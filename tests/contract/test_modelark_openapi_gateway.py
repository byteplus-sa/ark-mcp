"""Contract tests for the AK/SK-signed ModelArk OpenAPI gateway (asset library)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.byteplus_openapi.signing import OpenApiCredentials
from ark_mcp.providers.modelark.openapi import ModelArkOpenApiGateway

BASE = "https://ark-openapi.test.example.com"


def _gateway(token: str = "") -> ModelArkOpenApiGateway:
    return ModelArkOpenApiGateway(
        credentials=OpenApiCredentials(
            "AKLTtest", "secret-test", token
        ),  # pragma: allowlist secret
        region="ap-southeast-1",
        base_url=BASE,
        timeout=5,
        connect_timeout=5,
    )


def _envelope(action: str, *, result: dict | None = None, error: dict | None = None) -> dict:
    metadata: dict = {"RequestId": "req-body-1", "Action": action, "Version": "2024-01-01"}
    if error is not None:
        metadata["Error"] = error
    body: dict = {"ResponseMetadata": metadata}
    if result is not None:
        body["Result"] = result
    return body


@respx.mock
async def test_call_signs_request_and_unwraps_result() -> None:
    route = respx.post(f"{BASE}/").mock(
        return_value=httpx.Response(200, json=_envelope("CreateAsset", result={"Id": "asset-1"}))
    )
    gateway = _gateway(token="sts-token")  # pragma: allowlist secret
    try:
        result = await gateway.call("CreateAsset", {"GroupId": "group-1", "URL": "https://x/a.png"})
    finally:
        await gateway.close()

    assert result == {"Id": "asset-1"}
    request = route.calls.last.request
    assert request.url.params["Action"] == "CreateAsset"
    assert request.url.params["Version"] == "2024-01-01"
    assert request.headers["Authorization"].startswith("HMAC-SHA256 Credential=AKLTtest/")
    assert "/ap-southeast-1/ark/request" in request.headers["Authorization"]
    assert request.headers["X-Security-Token"] == "sts-token"  # pragma: allowlist secret
    assert json.loads(request.content) == {"GroupId": "group-1", "URL": "https://x/a.png"}


@respx.mock
async def test_auth_error_is_not_retryable_and_explains_credentials() -> None:
    respx.post(f"{BASE}/").mock(
        return_value=httpx.Response(
            401,
            json=_envelope(
                "ListAssetGroups",
                error={"CodeN": 100009, "Code": "InvalidAccessKey", "Message": "invalid key"},
            ),
            headers={"X-Tt-Logid": "log-1"},
        )
    )
    gateway = _gateway()
    with pytest.raises(ProviderError) as info:
        await gateway.call("ListAssetGroups", {"Filter": {"GroupType": "AIGC"}})
    await gateway.close()

    error = info.value.error
    assert error.provider == "modelark-openapi"
    assert error.code == "InvalidAccessKey"
    assert error.retryable is False
    assert error.request_id == "req-body-1"
    assert "BYTEPLUS_MODELARK_ACCESS_KEY" in error.message


@respx.mock
async def test_metadata_error_with_http_200_still_raises() -> None:
    respx.post(f"{BASE}/").mock(
        return_value=httpx.Response(
            200,
            json=_envelope(
                "ListAssetGroups",
                error={"Code": "MissingParameter.Filter", "Message": "Filter is required"},
            ),
        )
    )
    gateway = _gateway()
    with pytest.raises(ProviderError) as info:
        await gateway.call("ListAssetGroups", {})
    await gateway.close()
    assert info.value.error.code == "MissingParameter.Filter"


@respx.mock
async def test_create_asset_5xx_is_ambiguous_but_reads_are_retryable() -> None:
    respx.post(f"{BASE}/").mock(return_value=httpx.Response(503, json={}))
    gateway = _gateway()
    with pytest.raises(ProviderError) as create_info:
        await gateway.call("CreateAsset", {})
    with pytest.raises(ProviderError) as get_info:
        await gateway.call("GetAsset", {"Id": "asset-1"})
    await gateway.close()

    assert create_info.value.error.ambiguous_completion is True
    assert get_info.value.error.retryable is True
    assert get_info.value.error.ambiguous_completion is False


@pytest.mark.parametrize("action", ["DeleteAsset", "DeleteAssetGroup"])
@respx.mock
async def test_delete_5xx_is_ambiguous(action: str) -> None:
    route = respx.post(f"{BASE}/").mock(return_value=httpx.Response(503, json={}))
    gateway = _gateway()
    try:
        with pytest.raises(ProviderError) as info:
            await gateway.call(action, {"Id": "test-id", "ProjectName": "default"})
    finally:
        await gateway.close()

    assert info.value.error.ambiguous_completion is True
    assert route.call_count == 1


@respx.mock
async def test_throttle_code_is_retryable() -> None:
    respx.post(f"{BASE}/").mock(
        return_value=httpx.Response(
            429,
            json=_envelope(
                "CreateAsset", error={"Code": "RequestLimitExceeded", "Message": "slow down"}
            ),
            headers={"Retry-After": "2"},
        )
    )
    gateway = _gateway()
    with pytest.raises(ProviderError) as info:
        await gateway.call("CreateAsset", {})
    await gateway.close()
    assert info.value.error.retryable is True
    assert info.value.error.retry_after_seconds == 2.0
