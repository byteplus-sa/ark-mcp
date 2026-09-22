"""Shared HTTP gateway base for BytePlus provider clients.

Both provider gateways (ModelArk for Seedream/Seedance and Seed Speech for Seed
Audio) share the same lifecycle, transport, timeout, and connection-error
normalization. Subclasses provide provider-specific headers, the provider name,
request-ID extraction, and HTTP error normalization.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from time import perf_counter
from typing import Any, ClassVar

import httpx
from fastmcp.telemetry import get_tracer, record_span_error

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError, ProviderName
from ark_mcp.observability.logger import info as log_info
from ark_mcp.observability.metrics import PROVIDER_DURATION, PROVIDER_REQUESTS


class BaseHttpGateway(ABC):
    """Abstract base for authenticated BytePlus HTTP gateway clients."""

    PROVIDER: ClassVar[ProviderName]
    _api_key: str
    _base_url: str
    _timeout: float
    _connect_timeout: float
    _client: httpx.AsyncClient | None

    @abstractmethod
    def _headers(self) -> dict[str, str]:
        """Return the auth/content headers for a request."""

    @staticmethod
    def extract_request_id(response: httpx.Response) -> str | None:
        """Extract the provider request/diagnostic ID from a response.

        Default implementation returns ``None``. Providers that return a
        diagnostic ID in response headers (e.g. ModelArk's ``X-Request-Id``,
        Seed Speech's ``X-Tt-Logid``) override this. Providers that return
        the ID in the response body extract it from the parsed body in their
        service layer instead.
        """
        return None

    @classmethod
    @abstractmethod
    def normalize_error(cls, response: httpx.Response, operation: str) -> ProviderError:
        """Normalize an error HTTP response into a ``ProviderError``."""

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout, connect=self._connect_timeout),
                follow_redirects=False,
                trust_env=False,
            )
        return self._client

    @property
    def base_url(self) -> str:
        return self._base_url

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        client = await self._ensure_client()
        url = f"{self._base_url}{path}"
        log_info(f"{self.PROVIDER}_request", method=method, path=path)
        headers = kwargs.pop("headers", None) or self._headers()
        operation = method.lower()
        started = perf_counter()
        with get_tracer().start_as_current_span(f"provider.{self.PROVIDER}.{operation}") as span:
            span.set_attribute("provider.name", self.PROVIDER)
            span.set_attribute("http.request.method", method)
            try:
                response = await client.request(method, url, headers=headers, **kwargs)
            except Exception as exc:
                record_span_error(span, exc)
                PROVIDER_REQUESTS.labels(
                    provider=self.PROVIDER,
                    operation=operation,
                    status="exception",
                ).inc()
                raise
            else:
                span.set_attribute("http.response.status_code", response.status_code)
                status = "error" if response.status_code >= 400 else "success"
                PROVIDER_REQUESTS.labels(
                    provider=self.PROVIDER,
                    operation=operation,
                    status=status,
                ).inc()
                return response
            finally:
                PROVIDER_DURATION.labels(
                    provider=self.PROVIDER,
                    operation=operation,
                ).observe(perf_counter() - started)

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def health_check(self, *, timeout_seconds: float = 2.0) -> bool:
        """Return True if the provider base URL responds within the timeout."""
        client = await self._ensure_client()
        try:
            await client.get(
                self._base_url,
                timeout=httpx.Timeout(timeout_seconds, connect=timeout_seconds),
            )
        except Exception:
            return False
        return True

    @classmethod
    def normalize_timeout(cls, operation: str, *, side_effect: bool = True) -> ProviderError:
        """Normalize an HTTP timeout.

        ``side_effect=True`` (the default) is for mutations such as create,
        generate, cancel, and delete: the provider may have acted, so the error
        is ambiguous and must not be retried blindly. ``side_effect=False`` is
        for read-only queries and completions that create no provider state:
        the error is retryable and not ambiguous.
        """
        if side_effect:
            message = (
                f"Request timed out during '{operation}'. "
                "The operation may have succeeded. "
                "Do not retry blindly; reconcile using the task ID or request ID."
            )
        else:
            message = (
                f"Request timed out during '{operation}'. "
                "No provider-side state was created, so it is safe to retry."
            )
        return ProviderError(
            NormalizedProviderError(
                provider=cls.PROVIDER,
                operation=operation,
                http_status=None,
                code="TIMEOUT",
                message=message,
                request_id=None,
                retryable=not side_effect,
                ambiguous_completion=side_effect,
            )
        )

    @classmethod
    def normalize_connection_error(cls, operation: str, exc: Exception) -> ProviderError:
        return ProviderError(
            NormalizedProviderError(
                provider=cls.PROVIDER,
                operation=operation,
                http_status=None,
                code="CONNECTION_ERROR",
                message=f"Failed to connect to {cls.PROVIDER}: {exc}",
                request_id=None,
                retryable=True,
            )
        )

    @classmethod
    def normalize_transport_error(cls, operation: str, exc: Exception) -> ProviderError:
        return ProviderError(
            NormalizedProviderError(
                provider=cls.PROVIDER,
                operation=operation,
                http_status=None,
                code="TRANSPORT_ERROR",
                message=f"Transport error during '{operation}': {exc}",
                request_id=None,
                retryable=True,
            )
        )
