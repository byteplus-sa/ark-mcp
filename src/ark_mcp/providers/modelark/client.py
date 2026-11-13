"""ModelArk HTTP gateway — shared client for Seedream (image) and Seedance (video).

Both products use the ModelArk data-plane host with ``Authorization: Bearer``
authentication. The gateway handles request/response, error normalization, and
request ID capture.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, ClassVar

from ark_mcp.config.env import get_settings
from ark_mcp.domain.errors import NormalizedProviderError, ProviderError, ProviderName
from ark_mcp.observability.logger import error as log_error
from ark_mcp.providers.base import BaseHttpGateway

if TYPE_CHECKING:
    import httpx


class ModelArkGateway(BaseHttpGateway):
    """Authenticated HTTP client for ModelArk API calls."""

    PROVIDER: ClassVar[ProviderName] = "modelark"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        connect_timeout: float | None = None,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key or settings.modelark_api_key
        self._base_url = (base_url or settings.modelark_base_url).rstrip("/")
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
        """POST to ModelArk and return the raw response."""
        return await self._request("POST", path, json=json_body)

    async def post_multipart(self, path: str, fields: dict[str, str]) -> httpx.Response:
        """POST ``multipart/form-data`` text fields (the Files API rejects other encodings)."""
        headers = {key: value for key, value in self._headers().items() if key != "Content-Type"}
        return await self._request(
            "POST",
            path,
            headers=headers,
            files={name: (None, value) for name, value in fields.items()},
        )

    async def get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """GET from ModelArk and return the raw response."""
        return await self._request("GET", path, params=params)

    async def delete(self, path: str) -> httpx.Response:
        """DELETE on ModelArk and return the raw response."""
        return await self._request("DELETE", path)

    @staticmethod
    def extract_request_id(response: httpx.Response) -> str | None:
        """Extract the ModelArk request ID from response headers."""
        value = response.headers.get("X-Request-Id") or response.headers.get("x-request-id")
        return str(value) if value is not None else None

    @classmethod
    def normalize_error(cls, response: httpx.Response, operation: str) -> ProviderError:
        """Normalize an error HTTP response into a ``ProviderError``."""
        request_id = cls.extract_request_id(response)
        status = response.status_code

        try:
            body = response.json()
        except json.JSONDecodeError:
            body = {"error": {"message": response.text}}

        error_obj = body.get("error", body) if isinstance(body, dict) else {}
        code = str(error_obj.get("code", "")) if isinstance(error_obj, dict) else ""
        message = error_obj.get("message", str(body)) if isinstance(error_obj, dict) else str(body)

        retryable = status == 429 or status >= 500
        mutation = operation in {
            "generate_image",
            "create_task",
            "delete_task",
            "chat_completion",
            "upload_file",
        }
        retry_after = response.headers.get("Retry-After")
        try:
            retry_after_seconds = float(retry_after) if retry_after is not None else None
        except ValueError:
            retry_after_seconds = None

        normalized = NormalizedProviderError(
            provider=cls.PROVIDER,
            operation=operation,
            http_status=status,
            code=code or None,
            message=message,
            request_id=request_id,
            retryable=retryable,
            ambiguous_completion=mutation and status >= 500,
            retry_after_seconds=retry_after_seconds,
        )
        log_error(
            "modelark_error",
            operation=operation,
            http_status=status,
            code=normalized.code,
            retryable=retryable,
            request_id=request_id,
        )
        return ProviderError(normalized)
