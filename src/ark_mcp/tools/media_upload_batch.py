"""``media_upload_batch`` tool — upload many media files in one call.

Collapses N sequential ``media_upload`` calls into one. Each item is
validated and uploaded independently with the same rules as
``media_upload``; a failing item returns an error entry while the rest
succeed. The total decoded size of one call is capped by
``MEDIA_UPLOAD_BATCH_MAX_BYTES`` and checked before any upload starts.
"""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path
from typing import Literal

from fastmcp import Context
from pydantic import BaseModel, Field, model_validator

from ark_mcp.config.env import get_settings
from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.object_storage import make_object_storage_gateway
from ark_mcp.tools._task_execution import context_log
from ark_mcp.tools.media_upload import (
    MediaUploadInput,
    prepare_upload,
    require_object_storage,
    upload_one,
)


class MediaUploadBatchItemInput(BaseModel):
    """One file to upload in a batch."""

    media_type: Literal["image", "audio", "video"] = Field(
        ..., description="Logical media type: image, audio, or video."
    )
    mime_type: str | None = Field(
        None,
        description=(
            "MIME type (e.g. image/png, video/mp4). Optional for file_path items: inferred from "
            "the file extension when omitted. Required for data items."
        ),
    )
    data: str | None = Field(
        None, description="Base64-encoded media bytes. Mutually exclusive with file_path."
    )
    file_path: str | None = Field(
        None,
        description="Absolute path to a local file. stdio transport only. Mutually exclusive with data.",
    )
    key_prefix: str | None = Field(
        None,
        description=(
            "Optional object key prefix (default 'references'). Alphanumeric, '-', '_', '/' only."
        ),
    )

    @model_validator(mode="after")
    def _infer_mime(self) -> MediaUploadBatchItemInput:
        if self.mime_type is None:
            if self.file_path is None:
                raise ValueError("mime_type is required for data items.")
            guessed, _ = mimetypes.guess_type(self.file_path)
            if guessed is None:
                raise ValueError(
                    f"Could not infer mime_type from '{Path(self.file_path).name}'; set mime_type."
                )
            self.mime_type = guessed
        return self


class MediaUploadBatchInput(BaseModel):
    """Input model for ``media_upload_batch``."""

    items: list[MediaUploadBatchItemInput] = Field(
        ..., min_length=1, max_length=50, description="1-50 files to upload."
    )
    expires_in_seconds: int | None = Field(
        None,
        ge=60,
        le=604800,
        description=(
            "Presigned URL validity in seconds (60-604800) applied to every item. Defaults to the "
            "configured presign TTL. Use a long TTL (e.g. 3600) for URLs destined for vod_* tools."
        ),
    )
    max_concurrent: int = Field(
        4, ge=1, le=8, description="Maximum uploads running at the same time (1-8)."
    )


class MediaUploadBatchItem(BaseModel):
    """Per-item result within a batch upload."""

    index: int = Field(..., description="0-based position of this item in the request.")
    file_path: str | None = Field(
        None, description="The item's file_path as given, to match results to inputs."
    )
    url: str | None = Field(None, description="Presigned HTTPS GET URL (None if this item failed).")
    object_key: str | None = Field(
        None, description="Object key of the uploaded media, reusable with media_presign_batch."
    )
    expires_at: str | None = Field(None, description="ISO-8601 timestamp when url expires.")
    mime_type: str | None = Field(None, description="MIME type used for the upload.")
    bytes: int | None = Field(None, description="Uploaded byte count.")
    error: str | None = Field(None, description="Why this item failed (None on success).")
    retryable: bool | None = Field(
        None, description="Whether a failed item may succeed if uploaded again (None on success)."
    )


class MediaUploadBatchOutput(BaseModel):
    """Output model for ``media_upload_batch``."""

    items: list[MediaUploadBatchItem] = Field(..., description="Per-item results in request order.")
    succeeded: int = Field(..., description="Number of items uploaded.")
    failed: int = Field(..., description="Number of items that failed.")
    total_bytes: int = Field(..., description="Total bytes uploaded across successful items.")


async def media_upload_batch(input: MediaUploadBatchInput, ctx: Context) -> MediaUploadBatchOutput:
    """Upload several media files to object storage and return a presigned URL for each.

    Same rules as media_upload, for 1-50 items in one call: each item is
    Base64 data or a local file_path (stdio only), and each gets its own
    presigned HTTPS GET URL. Items fail independently. The total decoded size
    is capped by MEDIA_UPLOAD_BATCH_MAX_BYTES and checked before any upload.
    Requires MCP task-augmented execution (or ark_job_submit) because batches
    can be large.
    """
    await context_log(ctx, "info", f"Starting batch upload of {len(input.items)} items")
    settings = get_settings()
    require_object_storage(settings)

    # Validate every item and the aggregate size before uploading anything.
    prepared: list[tuple[MediaUploadInput, Path | None, bytes | None, int] | str] = []
    total = 0
    for item in input.items:
        try:
            single = MediaUploadInput(
                media_type=item.media_type,
                mime_type=item.mime_type or "",
                data=item.data,
                file_path=item.file_path,
                key_prefix=item.key_prefix,
                expires_in_seconds=input.expires_in_seconds,
            )
            path, raw, size = prepare_upload(single, settings)
        except ValueError as exc:
            prepared.append(str(exc))
            continue
        total += size
        prepared.append((single, path, raw, size))
    if total > settings.media_upload_batch_max_bytes:
        raise ValueError(
            f"Batch totals {total} bytes, above MEDIA_UPLOAD_BATCH_MAX_BYTES "
            f"({settings.media_upload_batch_max_bytes}). Split it into smaller batches."
        )
    await ctx.report_progress(progress=10, total=100)

    limiter = asyncio.Semaphore(input.max_concurrent)
    gateway = make_object_storage_gateway(settings)
    done = 0

    async def run(index: int) -> MediaUploadBatchItem:
        nonlocal done
        item = input.items[index]
        entry = prepared[index]
        base = MediaUploadBatchItem(index=index, file_path=item.file_path, mime_type=item.mime_type)
        if isinstance(entry, str):
            return base.model_copy(update={"error": entry, "retryable": False})
        single, path, raw, size = entry
        async with limiter:
            try:
                result = await upload_one(ctx, settings, gateway, single, path, raw, size)
            except ProviderError as exc:
                return base.model_copy(update={"error": exc.message, "retryable": exc.retryable})
            except (ValueError, OSError) as exc:
                return base.model_copy(update={"error": str(exc), "retryable": False})
            finally:
                done += 1
                await ctx.report_progress(
                    progress=10 + int(90 * done / len(input.items)), total=100
                )
        return base.model_copy(
            update={
                "url": result.url,
                "object_key": result.object_key,
                "expires_at": result.expires_at,
                "bytes": result.bytes,
            }
        )

    try:
        items = list(await asyncio.gather(*(run(i) for i in range(len(input.items)))))
    finally:
        await gateway.close()

    succeeded = sum(1 for item in items if item.error is None)
    uploaded = sum(item.bytes or 0 for item in items if item.error is None)
    log_info("media_upload_batch_complete", total=len(items), succeeded=succeeded)
    return MediaUploadBatchOutput(
        items=items,
        succeeded=succeeded,
        failed=len(items) - succeeded,
        total_bytes=uploaded,
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
