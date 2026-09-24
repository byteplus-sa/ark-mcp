"""ModelArk OpenAPI gateway — AK/SK-signed control-plane calls.

Used by the private asset library (``CreateAssetGroup``, ``CreateAsset``,
``GetAsset``...). Unlike the data-plane :class:`ModelArkGateway` (Bearer API
key, OpenAI-style bodies), this host requires BytePlus OpenAPI V4 signatures
and wraps every response in ``{"ResponseMetadata": {...}, "Result": {...}}``.

Implements ``plans/PLAN_MODELARK_ASSET_LIBRARY.md`` (Phase 1). The gateway
never logs or returns the secret key, session token, or signed headers.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any, ClassVar, cast

import httpx

from ark_mcp.config.env import get_settings
from ark_mcp.domain.errors import NormalizedProviderError, ProviderError, ProviderName
from ark_mcp.observability.logger import error as log_error
from ark_mcp.providers.base import BaseHttpGateway
from ark_mcp.providers.byteplus_openapi.signing import OpenApiCredentials, sign_request

SERVICE = "ark"
VERSION = "2024-01-01"

# Actions that create or change provider state. A 5xx or timeout on these is
# ambiguous (the provider may have acted), so they are never retried blindly.
MUTATING_ACTIONS: frozenset[str] = frozenset(
    {
        "CreateAssetGroup",
        "CreateAsset",
        "UpdateAssetGroup",
        "UpdateAsset",
        "DeleteAsset",
        "DeleteAssetGroup",
        "CreateVisualValidateSession",
    }
)

# Provider error codes that indicate throttling; retryable like HTTP 429.
_THROTTLE_CODES: frozenset[str] = frozenset(
    {"RequestLimitExceeded", "FlowLimitExceeded", "TooManyRequests", "Throttling"}
)


class _MinIntervalLimiter:
    """Process-wide spacing between calls (a simple QPM guard)."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def wait(self, per_minute: int) -> None:
        interval = 60.0 / per_minute
        async with self._lock:
            now = time.monotonic()
            delay = self._next_at - now
            self._next_at = max(now, self._next_at) + interval
        if delay > 0:
            await asyncio.sleep(delay)


CREATE_ASSET_LIMITER = _MinIntervalLimiter()


class ModelArkOpenApiGateway(BaseHttpGateway):
    """Signature-authenticated client for the ModelArk OpenAPI (service ``ark``)."""

    PROVIDER: ClassVar[ProviderName] = "modelark-openapi"

    def __init__(
        self,
        *,
        credentials: OpenApiCredentials | None = None,
        region: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        connect_timeout: float | None = None,
    ) -> None:
        settings = get_settings()
        self._credentials = credentials or OpenApiCredentials(
            access_key_id=settings.modelark_access_key,
            secret_access_key=settings.modelark_secret_key,
            session_token=settings.modelark_session_token,
        )
        self._region = region or settings.modelark_region
        self._base_url = (base_url or settings.modelark_openapi_base_url).rstrip("/")
        self._timeout = timeout or min(settings.request_timeout_ms / 1000, 60.0)
        self._connect_timeout = connect_timeout or settings.connect_timeout_ms / 1000
        self._client: httpx.AsyncClient | None = None

    def _headers(self) -> dict[str, str]:
        return {"Accept": "application/json"}

    @staticmethod
    def extract_request_id(response: httpx.Response) -> str | None:
        value = response.headers.get("X-Tt-Logid") or response.headers.get("x-tt-logid")
        return str(value) if value is not None else None

    def _host(self) -> str:
        url = httpx.URL(self._base_url)
        host = url.host or "ark.ap-southeast-1.byteplusapi.com"
        if url.port and url.port != 443:
            host = f"{host}:{url.port}"
        return host

    async def call(self, action: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST one signed ``Action`` and return its unwrapped ``Result`` object.

        Raises ``ProviderError`` for transport failures, HTTP errors, and
        ``ResponseMetadata.Error`` payloads (which the provider may also return
        with HTTP 200).
        """
        query = {"Action": action, "Version": VERSION}
        payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        headers = sign_request(
            credentials=self._credentials,
            method="POST",
            host=self._host(),
            query=query,
            payload=payload,
            region=self._region,
            service=SERVICE,
            now=datetime.now(UTC),
        )
        headers["Accept"] = "application/json"
        mutation = action in MUTATING_ACTIONS
        try:
            response = await self._request(
                "POST",
                "/",
                params=query,
                headers=headers,
                content=payload,
            )
        except httpx.TimeoutException:
            raise self.normalize_timeout(action, side_effect=mutation) from None
        except httpx.ConnectError as exc:
            raise self.normalize_connection_error(action, exc) from exc
        except httpx.TransportError as exc:
            raise self.normalize_transport_error(action, exc) from exc

        body_json: Any
        try:
            body_json = response.json()
        except (json.JSONDecodeError, ValueError):
            body_json = None

        if response.status_code >= 400 or _metadata_error(body_json) is not None:
            raise self.normalize_error(response, action)

        if not isinstance(body_json, dict):
            raise ProviderError(
                NormalizedProviderError(
                    provider=self.PROVIDER,
                    operation=action,
                    http_status=response.status_code,
                    code="INVALID_RESPONSE",
                    message="ModelArk OpenAPI returned a non-JSON success response.",
                    request_id=self.extract_request_id(response),
                    retryable=False,
                    ambiguous_completion=mutation,
                )
            )
        result = body_json.get("Result")
        return cast("dict[str, Any]", result) if isinstance(result, dict) else {}

    @classmethod
    def normalize_error(cls, response: httpx.Response, operation: str) -> ProviderError:
        """Normalize an OpenAPI error (``ResponseMetadata.Error``) to ``ProviderError``."""
        status = response.status_code
        request_id = cls.extract_request_id(response)
        code: str | None = None
        message = f"ModelArk OpenAPI {operation} failed with HTTP {status}."
        try:
            parsed: Any = response.json()
        except (json.JSONDecodeError, ValueError):
            parsed = None
        error_obj = _metadata_error(parsed)
        if error_obj is not None:
            code = str(error_obj.get("Code") or "") or None
            if error_obj.get("Message"):
                message = str(error_obj["Message"])
            metadata = parsed.get("ResponseMetadata") if isinstance(parsed, dict) else None
            if isinstance(metadata, dict) and metadata.get("RequestId"):
                request_id = str(metadata["RequestId"])

        if status in {401, 403} or (code or "").startswith(("InvalidAccessKey", "InvalidSecret")):
            message = (
                f"{message} Check BYTEPLUS_MODELARK_ACCESS_KEY / _SECRET_KEY (and "
                "_SESSION_TOKEN for temporary AKTP... keys) and that the IAM user has "
                "ArkFullAccess on the project."
            )

        throttled = status == 429 or (code or "") in _THROTTLE_CODES
        retryable = throttled or status >= 500
        mutation = operation in MUTATING_ACTIONS
        retry_after = response.headers.get("Retry-After")
        try:
            retry_after_seconds = float(retry_after) if retry_after is not None else None
        except ValueError:
            retry_after_seconds = None

        normalized = NormalizedProviderError(
            provider=cls.PROVIDER,
            operation=operation,
            http_status=status,
            code=code,
            message=message,
            request_id=request_id,
            retryable=retryable,
            ambiguous_completion=mutation and status >= 500,
            retry_after_seconds=retry_after_seconds,
        )
        log_error(
            "modelark_openapi_error",
            operation=operation,
            http_status=status,
            code=code,
            retryable=retryable,
            request_id=request_id,
        )
        return ProviderError(normalized)


def _metadata_error(body: Any) -> dict[str, Any] | None:
    if not isinstance(body, dict):
        return None
    metadata = body.get("ResponseMetadata")
    if not isinstance(metadata, dict):
        return None
    error = metadata.get("Error")
    if isinstance(error, dict) and (error.get("Code") or error.get("Message")):
        return error
    return None
