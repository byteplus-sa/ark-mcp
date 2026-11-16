"""Wire contracts for the signed moderation lookup."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.byteplus_openapi.signing import OpenApiCredentials
from ark_mcp.providers.modelark.moderation import ModerationService
from ark_mcp.providers.modelark.openapi import ModelArkOpenApiGateway

BASE = "https://moderation.test.example.com"


def service() -> ModerationService:
    return ModerationService(
        ModelArkOpenApiGateway(
            credentials=OpenApiCredentials("test-access", "test-secret", "test-session"),
            base_url=BASE,
        )
    )


@pytest.mark.parametrize("kind", ["task_id", "asset_id", "request_id"])
@respx.mock
async def test_signed_lookup_preserves_provider_reasons(kind: str) -> None:
    reasons = [
        {"label": label, "sub_label": label, "detail": detail}
        for label, detail in [
            ("Safety", None),
            ("Copyright", ""),
            ("Celebrity", "Synthetic explanation"),
            ("Deepfake", None),
            ("FutureCategory", "Future explanation"),
        ]
    ]
    route = respx.post(f"{BASE}/").respond(
        200, json={"Result": {"block_reasons": reasons, "future_field": "ignored"}}
    )
    adapter = service()
    try:
        result = await adapter.get_result(id="opaque-example", type=kind)
    finally:
        await adapter.close()
    assert result.model_dump() == {"id": "opaque-example", "type": kind, "block_reasons": reasons}
    request = route.calls.last.request
    assert dict(request.url.params) == {"Action": "GetModerationResult", "Version": "2024-01-01"}
    assert json.loads(request.content) == {"Id": "opaque-example", "Type": kind}
    assert request.headers["Authorization"].startswith("HMAC-SHA256 ")
    assert "/ap-southeast-1/ark/request" in request.headers["Authorization"]
    assert request.headers["X-Security-Token"] == "test-session"
    assert route.call_count == 1


@pytest.mark.parametrize("reasons", [[], [{"label": "Safety", "sub_label": "Safety"}]])
@respx.mock
async def test_empty_list_and_omitted_detail_are_valid(reasons: list[dict]) -> None:
    respx.post(f"{BASE}/").respond(200, json={"Result": {"block_reasons": reasons}})
    adapter = service()
    try:
        result = await adapter.get_result(id="example", type="task_id")
    finally:
        await adapter.close()
    assert len(result.block_reasons) == len(reasons)
    if reasons:
        assert result.block_reasons[0].detail is None


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"Result": None},
        {"Result": []},
        {"Result": {}},
        {"Result": {"block_reasons": None}},
        {"Result": {"block_reasons": {}}},
        {"Result": {"block_reasons": [None]}},
        {"Result": {"block_reasons": [{"label": "Safety"}]}},
        {"Result": {"block_reasons": [{"label": "", "sub_label": "Safety"}]}},
        {"Result": {"block_reasons": [{"label": 5, "sub_label": "Safety"}]}},
        {"Result": {"block_reasons": [{"label": "Safety", "sub_label": None}]}},
        {"Result": {"block_reasons": [{"label": "Safety", "sub_label": "Safety", "detail": {}}]}},
    ],
)
@respx.mock
async def test_invalid_response_is_safe_error(body: dict) -> None:
    route = respx.post(f"{BASE}/").respond(200, json=body)
    adapter = service()
    try:
        with pytest.raises(ProviderError) as error:
            await adapter.get_result(id="example", type="asset_id")
    finally:
        await adapter.close()
    assert error.value.code == "INVALID_RESPONSE"
    assert not error.value.retryable
    assert "input_value" not in error.value.message
    assert route.call_count == 1


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (404, "NotFound.Id", False),
        (401, "InvalidAccessKey", False),
        (403, "AccessDenied", False),
        (200, "MissingParameter.Id", False),
        (429, "Throttling", True),
        (503, "InternalError", True),
    ],
)
@respx.mock
async def test_errors_preserve_code_and_request_id_without_retries(
    status: int, code: str, retryable: bool
) -> None:
    route = respx.post(f"{BASE}/").respond(
        status,
        json={
            "ResponseMetadata": {
                "RequestId": "diagnostic-example",
                "Error": {
                    "Code": code,
                    "Message": "Synthetic error",
                },
            }
        },
        headers={"Retry-After": "-1"},
    )
    adapter = service()
    try:
        with pytest.raises(ProviderError) as error:
            await adapter.get_result(id="example", type="task_id")
    finally:
        await adapter.close()
    assert error.value.code == code
    assert error.value.error.request_id == "diagnostic-example"
    assert error.value.retryable is retryable
    assert error.value.error.retry_after_seconds is None
    assert route.call_count == 1
    if status in {401, 403}:
        assert "GetModerationResult permission" in error.value.message
        assert "whitelist" in error.value.message
        assert "ArkFullAccess" not in error.value.message


@pytest.mark.parametrize("failure", [httpx.ReadTimeout, httpx.ConnectError, httpx.ReadError])
@respx.mock
async def test_transport_errors_are_retryable_but_not_retried(failure: type[Exception]) -> None:
    route = respx.post(f"{BASE}/").mock(side_effect=failure("synthetic failure"))
    adapter = service()
    try:
        with pytest.raises(ProviderError) as error:
            await adapter.get_result(id="example", type="task_id")
    finally:
        await adapter.close()
    assert error.value.retryable
    assert not error.value.ambiguous_completion
    assert route.call_count == 1


@respx.mock
async def test_non_json_success_is_invalid() -> None:
    respx.post(f"{BASE}/").respond(200, text="not json")
    adapter = service()
    try:
        with pytest.raises(ProviderError, match="non-JSON") as error:
            await adapter.get_result(id="example", type="request_id")
    finally:
        await adapter.close()
    assert error.value.code == "INVALID_RESPONSE"
