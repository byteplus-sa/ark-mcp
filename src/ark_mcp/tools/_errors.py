"""Shared error-result helpers for tool handlers."""

from __future__ import annotations

from fastmcp.tools import ToolResult

from ark_mcp.domain.errors import ProviderError


def format_provider_error_text(payload: dict[str, object]) -> str:
    """Render a normalized provider error as a human-readable text string.

    The leading ``"{provider} {operation} failed: {message}"`` prefix is kept
    stable so existing callers and assertions continue to match. The remaining
    normalized fields are appended as a bracketed suffix so they are not lost
    when structured content is omitted.
    """
    prefix = f"{payload['provider']} {payload['operation']} failed: {payload['message']}"

    meta: list[str] = []
    if payload.get("code"):
        meta.append(f"code={payload['code']}")
    if payload.get("http_status") is not None:
        meta.append(f"http_status={payload['http_status']}")
    if payload.get("request_id"):
        meta.append(f"request_id={payload['request_id']}")
    retryable = payload.get("retryable")
    if retryable is not None:
        meta.append(f"retryable={retryable}")
    ambiguous = payload.get("ambiguous_completion")
    if ambiguous is not None:
        meta.append(f"ambiguous_completion={ambiguous}")

    rendered = f"{prefix} [{', '.join(meta)}]" if meta else prefix
    if ambiguous is True:
        return (
            f"{rendered} The operation may have succeeded; inspect the resource or task status "
            "before retrying."
        )
    return rendered


def provider_error_result(exc: ProviderError) -> ToolResult:
    """Return a safe MCP error result as text-only content.

    The tool's declared output schema is a strict success shape, so the error
    result intentionally carries no ``structured_content``. Returning
    ``{"error": ...}`` here would violate the output schema under strict MCP
    clients (e.g. TRAE) that validate structured content against the declared
    output schema, masking the real provider error behind a schema-validation
    failure. The normalized error fields are embedded in the text content so
    they remain available to callers.
    """
    payload = exc.error.model_dump(mode="json")
    return ToolResult(
        content=format_provider_error_text(payload),
        is_error=True,
    )
