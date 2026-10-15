"""Bearer-authenticated HTTP gateway for BytePlus Vision AI."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, ClassVar

from ark_mcp.config.env import get_settings
from ark_mcp.domain.errors import NormalizedProviderError, ProviderError, ProviderName
from ark_mcp.observability.logger import error as log_error
from ark_mcp.providers.base import BaseHttpGateway
from ark_mcp.providers.vision_cv.schemas import (
    CONTENT_FILTER_CODE,
    INTERNAL_ERROR_CODES,
    THROTTLE_CODES,
    CvEnvelope,
)
from ark_mcp.providers.vod_mediakit.client import sanitize_provider_message

if TYPE_CHECKING:
    import httpx

_ECHOED_BODY_MARKER = ", body:"
_MESSAGE_MAX_CHARS = 300


def clean_provider_message(message: str, fallback: str) -> str:
    """Redact URLs, drop any echoed upstream response body, and bound the length."""
    head = message.split(_ECHOED_BODY_MARKER, 1)[0]
    cleaned = sanitize_provider_message(head, fallback)
    if len(cleaned) > _MESSAGE_MAX_CHARS:
        return f"{cleaned[: _MESSAGE_MAX_CHARS - 3].rstrip()}..."
    return cleaned


class VisionCvGateway(BaseHttpGateway):
    """Bearer-authenticated client for the Vision AI common v3 task API."""

    PROVIDER: ClassVar[ProviderName] = "byteplus-vision-cv"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        connect_timeout: float | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.cv_api_key
        self._base_url = (base_url or settings.cv_base_url).rstrip("/")
        self._timeout = timeout or settings.request_timeout_ms / 1000
        self._connect_timeout = connect_timeout or settings.connect_timeout_ms / 1000
        self._client: httpx.AsyncClient | None = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def post(self, path: str, json_body: dict[str, Any]) -> httpx.Response:
        """POST a Vision AI task endpoint and return the raw response."""
        return await self._request("POST", path, json=json_body)

    @staticmethod
    def extract_request_id(response: httpx.Response) -> str | None:
        """Extract the diagnostic ``x-tt-logid`` header."""
        value = response.headers.get("x-tt-logid")
        return str(value) if value is not None else None

    @classmethod
    def normalize_error(cls, response: httpx.Response, operation: str) -> ProviderError:
        """Normalize a non-2xx response, preferring the body's envelope when present."""
        envelope: CvEnvelope | None
        try:
            envelope = CvEnvelope.model_validate(response.json())
        except (json.JSONDecodeError, ValueError):
            envelope = None
        return cls.error_from_envelope(
            envelope,
            operation,
            http_status=response.status_code,
            fallback_request_id=cls.extract_request_id(response),
            mutation=False,
            retry_after=response.headers.get("Retry-After"),
        )

    @classmethod
    def error_from_envelope(
        cls,
        envelope: CvEnvelope | None,
        operation: str,
        *,
        http_status: int,
        fallback_request_id: str | None,
        mutation: bool,
        retry_after: str | None = None,
    ) -> ProviderError:
        """Classify a failed call; ``mutation`` marks operations that change provider state."""
        code = envelope.code if envelope is not None else None
        fallback = f"Vision AI returned HTTP {http_status} during '{operation}'."
        message = (
            clean_provider_message(envelope.message, fallback) if envelope is not None else fallback
        )
        request_id = (envelope.request_id if envelope is not None else None) or fallback_request_id

        throttled = http_status == 429 or code in THROTTLE_CODES
        server_failure = code in INTERNAL_ERROR_CODES or (
            http_status >= 500 and code != CONTENT_FILTER_CODE
        )
        ambiguous = mutation and server_failure and not throttled
        retryable = throttled or (server_failure and not mutation)
        try:
            retry_after_seconds = float(retry_after) if retry_after is not None else None
        except ValueError:
            retry_after_seconds = None

        normalized = NormalizedProviderError(
            provider=cls.PROVIDER,
            operation=operation,
            http_status=http_status,
            code=str(code) if code is not None else f"HTTP_{http_status}",
            message=message,
            request_id=request_id,
            retryable=retryable,
            ambiguous_completion=ambiguous,
            retry_after_seconds=retry_after_seconds,
        )
        log_error(
            "vision_cv_error",
            operation=operation,
            http_status=http_status,
            code=normalized.code,
            retryable=retryable,
            ambiguous_completion=ambiguous,
            request_id=request_id,
        )
        return ProviderError(normalized)

    @classmethod
    def invalid_response(
        cls, operation: str, *, http_status: int, request_id: str | None, mutation: bool
    ) -> ProviderError:
        """Build the error for a success response that violates the expected shape."""
        return ProviderError(
            NormalizedProviderError(
                provider=cls.PROVIDER,
                operation=operation,
                http_status=http_status,
                code="INVALID_RESPONSE",
                message="Vision AI returned a success response in an unexpected shape.",
                request_id=request_id,
                retryable=False,
                ambiguous_completion=mutation,
            )
        )

    @classmethod
    def ambiguous_transport_error(cls, operation: str, exc: Exception) -> ProviderError:
        """Normalize a post-dispatch transport failure of a mutation."""
        return ProviderError(
            NormalizedProviderError(
                provider=cls.PROVIDER,
                operation=operation,
                http_status=None,
                code="TRANSPORT_ERROR",
                message=(
                    f"Transport error during '{operation}': {type(exc).__name__}. "
                    "The operation may have succeeded; do not retry blindly."
                ),
                request_id=None,
                retryable=False,
                ambiguous_completion=True,
            )
        )
