"""Normalized provider error model.

Provider validation, moderation, access, quota, and execution failures are
returned as tool results with ``isError: true``, structured content, and a
concise correction path. This module provides both a Pydantic model
(``NormalizedProviderError``) for structured output and an exception class
(``ProviderError``) that wraps it for raising within the server.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ProviderName = Literal[
    "modelark",
    "modelark-openapi",
    "seed-speech",
    "byteplus-vod-mediakit",
    "tos",
    "s3",
]


class NormalizedProviderError(BaseModel):
    """Provider error normalized to a stable, cross-product shape."""

    provider: ProviderName = Field(..., description="Which provider produced the error.")
    operation: str = Field(..., description="Logical operation name (e.g. 'create_task').")
    http_status: int | None = Field(
        default=None, description="HTTP status code if the error was HTTP-based."
    )
    code: str | None = Field(
        default=None,
        description="Provider-specific error code, if available.",
    )
    message: str = Field(
        ..., description="Human-readable error description with a correction path."
    )
    request_id: str | None = Field(
        default=None,
        description="Provider request ID for reconciliation (Seed Audio X-Api-Request-Id or ModelArk request ID).",
    )
    retryable: bool = Field(..., description="Whether the operation can be safely retried.")
    ambiguous_completion: bool | None = Field(
        default=None,
        description="True if a mutation failed after dispatch or returned 5xx; it may have succeeded.",
    )
    retry_after_seconds: float | None = Field(
        default=None,
        ge=0,
        description="Provider-requested retry delay when supplied by Retry-After.",
    )


class ProviderError(Exception):
    """Exception wrapping a ``NormalizedProviderError``.

    This allows provider errors to be raised as exceptions within the
    server while still carrying structured error data. Tool handlers
    catch this and convert it to an MCP error result.
    """

    def __init__(self, error: NormalizedProviderError) -> None:
        self.error = error
        super().__init__(error.message)

    @property
    def normalized(self) -> NormalizedProviderError:
        return self.error

    @property
    def provider(self) -> ProviderName:
        return self.error.provider

    @property
    def operation(self) -> str:
        return self.error.operation

    @property
    def http_status(self) -> int | None:
        return self.error.http_status

    @property
    def code(self) -> str | None:
        return self.error.code

    @property
    def message(self) -> str:
        return self.error.message

    @property
    def request_id(self) -> str | None:
        return self.error.request_id

    @property
    def retryable(self) -> bool:
        return self.error.retryable

    @property
    def ambiguous_completion(self) -> bool | None:
        return self.error.ambiguous_completion
