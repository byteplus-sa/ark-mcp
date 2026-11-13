"""``media_presign`` tool — generate fresh presigned URLs for existing objects.

Accepts object keys returned by prior ``media_upload`` calls and returns a
fresh presigned HTTPS GET URL for each without re-uploading. This avoids
redundant uploads when the same reference media is used across several
generation calls spread over time (e.g. several Seedance tasks in a day).

Failures are captured per key rather than failing the whole call: a key that
is malformed, not owned by the caller, or fails provider-side presign returns
an error entry while the remaining keys succeed.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from functools import partial

from fastmcp import Context
from pydantic import BaseModel, Field

from ark_mcp.config.env import get_settings
from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.object_storage import make_object_storage_gateway
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import billed_provider_slot, get_principal, get_runtime
from ark_mcp.tools._task_execution import context_log

_OBJECT_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-_/]*$")


def validate_object_key(v: str) -> str:
    """Validate an object key for presign/upload reuse.

    Keys must contain only alphanumeric characters, ``-``, ``_``, and ``/``;
    must not start with ``/`` or ``-``; and must not contain empty path
    segments (``//`` or a trailing ``/``).
    """
    if not v or not _OBJECT_KEY_PATTERN.match(v):
        raise ValueError(
            "object_key must contain only alphanumeric characters, '-', '_', and '/', "
            "and must not start with '/' or '-'."
        )
    if "//" in v or v.endswith("/"):
        raise ValueError("object_key must not contain empty path segments.")
    return v


class MediaPresignInput(BaseModel):
    """Input model for ``media_presign``."""

    object_keys: list[str] = Field(
        ...,
        min_length=1,
        max_length=100,
        description="object_key values from media_upload (1-100), e.g. 'references/video/<uuid>'.",
    )
    expires_in_seconds: int | None = Field(
        None,
        ge=60,
        le=604800,
        description=(
            "Presigned URL validity in seconds for every key; defaults to the server presign "
            "TTL. Use >= 3600 for URLs destined for vod_* tools, which fetch asynchronously."
        ),
    )


class MediaPresignItem(BaseModel):
    """Per-key presign result."""

    object_key: str = Field(..., description="The requested object key.")
    url: str | None = Field(None, description="Fresh presigned HTTPS GET URL; null if failed.")
    expires_at: str | None = Field(None, description="ISO-8601 expiry of url; null if failed.")
    code: str | None = Field(
        None,
        description="Error code if failed: INVALID_KEY, NOT_OWNED, INTERNAL, or a provider code.",
    )
    error: str | None = Field(None, description="Error message if failed.")
    request_id: str | None = Field(None, description="Provider request ID, if available.")


class MediaPresignOutput(BaseModel):
    """Output model for ``media_presign``."""

    items: list[MediaPresignItem] = Field(..., description="Per-key results in request order.")
    succeeded: int = Field(..., description="Number of keys that produced a URL.")
    failed: int = Field(..., description="Number of keys that failed; see items[].error.")


async def media_presign(input: MediaPresignInput, ctx: Context) -> MediaPresignOutput:
    """Mint fresh presigned HTTPS GET URLs for 1-100 object keys from earlier media_upload calls.

    Use when an uploaded file's URL has expired or is about to; nothing is re-uploaded. Only keys
    you uploaded work. Keys fail independently: check failed and items[].error.
    """
    await context_log(
        ctx, "info", f"Generating presigned URLs for {len(input.object_keys)} object keys"
    )
    await ctx.report_progress(progress=10, total=100)

    settings = get_settings()
    if not settings.has_object_storage:
        raise ValueError(
            "Object storage is not configured. Set TOS_* or S3_* credentials and "
            "OBJECT_STORAGE_BACKEND (tos|s3)."
        )

    await ctx.report_progress(progress=30, total=100)

    gateway = make_object_storage_gateway(settings)
    ttl = input.expires_in_seconds or settings.presign_ttl_seconds
    items: list[MediaPresignItem] = []

    try:
        async with billed_provider_slot(
            ctx,
            provider=settings.object_storage_backend,
            product="presign",
            estimated_cost_usd=0.0,
        ):
            principal = get_principal(ctx)

            async def _presign(key: str, expires: int | None) -> str:
                if expires is not None:
                    return await gateway.presign_get(key=key, expires=expires)
                return await gateway.presign_get(key=key)

            for key in input.object_keys:
                try:
                    validate_object_key(key)
                    await get_runtime(ctx).object_key_ownership_store.require_owner(key, principal)
                    url = await call_with_retry(partial(_presign, key, input.expires_in_seconds))
                    expires_at = (datetime.now(UTC) + timedelta(seconds=ttl)).isoformat()
                    items.append(MediaPresignItem(object_key=key, url=url, expires_at=expires_at))
                except ValueError as exc:
                    items.append(
                        MediaPresignItem(object_key=key, code="INVALID_KEY", error=str(exc))
                    )
                except PermissionError as exc:
                    items.append(MediaPresignItem(object_key=key, code="NOT_OWNED", error=str(exc)))
                except ProviderError as exc:
                    items.append(
                        MediaPresignItem(
                            object_key=key,
                            code=exc.code,
                            error=exc.message,
                            request_id=exc.request_id,
                        )
                    )
                except Exception as exc:
                    items.append(MediaPresignItem(object_key=key, code="INTERNAL", error=str(exc)))
    finally:
        await gateway.close()

    succeeded = sum(1 for item in items if item.url is not None)
    failed = len(items) - succeeded

    await ctx.report_progress(progress=100, total=100)
    log_info("media_presign_complete", total=len(items), succeeded=succeeded, failed=failed)

    return MediaPresignOutput(items=items, succeeded=succeeded, failed=failed)


TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
