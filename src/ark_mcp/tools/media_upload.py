"""``media_upload`` tool — upload media to object storage, return presigned URLs.

Accepts Base64-encoded bytes or local file paths (stdio transport only),
uploads each item to a configured TOS or S3 bucket, and returns a presigned
HTTPS GET URL per item that can be passed to other tools (e.g.
``seedance_create_task`` video references). This is the integrated path for
media that cannot be inlined as Base64, most notably Seedance video
references, which are URL-only. Items are validated and uploaded
independently; a failing item returns an error entry while the rest succeed.
"""

from __future__ import annotations

import asyncio
import mimetypes
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastmcp import Context
from pydantic import BaseModel, Field, model_validator

from ark_mcp.config.env import Settings, get_settings
from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.object_storage import ObjectStorageGateway, make_object_storage_gateway
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import billed_provider_slot, get_principal, get_runtime
from ark_mcp.security.media_policy import (
    MediaLimits,
    check_base64_size,
    decode_base64_safely,
    get_media_limits,
    validate_audio_mime,
    validate_image_mime,
    validate_video_mime,
)
from ark_mcp.tools._task_execution import context_log


class UploadRequest(BaseModel):
    """One validated upload, built from a ``MediaUploadItem``."""

    media_type: Literal["image", "audio", "video"] = Field(
        ..., description="Media category for MIME and size validation."
    )
    mime_type: str = Field(
        ...,
        description="MIME type of the media (e.g. 'video/mp4', 'image/png', 'audio/wav').",
    )
    data: str | None = Field(
        None,
        description="Base64-encoded media bytes. Mutually exclusive with file_path.",
    )
    file_path: str | None = Field(
        None,
        description=(
            "Absolute path to a local file. stdio transport only. Mutually exclusive with data."
        ),
    )
    key_prefix: str | None = Field(
        None,
        description=(
            "Optional object key prefix (default 'references'). Alphanumeric, '-', '_', '/' only."
        ),
    )
    expires_in_seconds: int | None = Field(
        None,
        ge=60,
        le=604800,
        description=(
            "Presigned URL validity in seconds (60-604800). Defaults to the configured presign "
            "TTL. VOD tools fetch source URLs asynchronously, so use a long TTL (e.g. 3600) for "
            "uploads destined for VOD enhancement, transcoding, subtitle, or audio-separation tools."
        ),
    )

    @model_validator(mode="after")
    def _validate_input(self) -> UploadRequest:
        if bool(self.data) == bool(self.file_path):
            raise ValueError("Provide exactly one of 'data' (Base64) or 'file_path'.")
        self._validate_mime_type()
        if self.data is not None:
            limits = get_media_limits()
            max_bytes = _max_bytes(limits, self.media_type)
            check_base64_size(self.data, max_bytes, label=self.media_type)
        if self.key_prefix:
            self._validate_key_prefix()
        return self

    def _validate_mime_type(self) -> None:
        match self.media_type:
            case "image":
                validate_image_mime(self.mime_type)
            case "audio":
                validate_audio_mime(self.mime_type)
            case "video":
                validate_video_mime(self.mime_type)

    def _validate_key_prefix(self) -> None:
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_/")
        if self.key_prefix and not set(self.key_prefix) <= allowed:
            raise ValueError(
                "key_prefix may only contain alphanumeric characters, '-', '_', and '/'."
            )


class UploadResult(BaseModel):
    """Result of one successful upload."""

    url: str = Field(..., description="Presigned HTTPS GET URL for the uploaded object.")
    expires_at: str = Field(..., description="ISO-8601 timestamp when the URL expires.")
    object_key: str = Field(..., description="Object key of the uploaded media.")
    bytes: int = Field(..., description="Uploaded byte count.")


def _max_bytes(limits: MediaLimits, media_type: str) -> int:
    return {
        "image": limits.image_max_bytes,
        "audio": limits.audio_max_bytes,
        "video": limits.video_max_bytes,
    }[media_type]


def prepare_upload(
    input: UploadRequest, settings: Settings
) -> tuple[Path | None, bytes | None, int]:
    """Validate one upload's source and return ``(path, raw, size_bytes)``.

    Exactly one of ``path`` (stdio file upload) or ``raw`` (decoded Base64)
    is set. Raises ``ValueError`` for disallowed, missing, or oversized input.
    """
    max_bytes = _max_bytes(get_media_limits(), input.media_type)
    if input.file_path is not None:
        if settings.mcp_transport != "stdio":
            raise ValueError(
                "file_path input is only supported in stdio transport mode for security."
            )
        path = Path(input.file_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError("File not found.")
        file_size = path.stat().st_size
        if file_size > max_bytes:
            raise ValueError(
                f"{input.media_type} file size ({file_size} bytes) exceeds "
                f"limit ({max_bytes} bytes)."
            )
        return path, None, file_size
    if input.data is None:
        raise ValueError("data is required when file_path is not provided.")
    raw = decode_base64_safely(input.data, max_bytes, label=input.media_type)
    return None, raw, len(raw)


async def upload_one(
    ctx: Context,
    settings: Settings,
    gateway: ObjectStorageGateway,
    input: UploadRequest,
    path: Path | None,
    raw: bytes | None,
    raw_size: int,
) -> UploadResult:
    """Upload one prepared item and presign it. Raises ``ProviderError`` on failure."""
    prefix = input.key_prefix or "references"
    key = f"{prefix}/{input.media_type}/{uuid4()}"
    async with billed_provider_slot(
        ctx,
        provider=settings.object_storage_backend,
        product="upload",
        estimated_cost_usd=0.0,
    ):
        if path is not None:
            file_path_str = str(path)
            await call_with_retry(
                lambda: gateway.upload_file(
                    key=key, file_path=file_path_str, mime_type=input.mime_type
                )
            )
        else:
            if raw is None:
                raise ValueError("data is required for Base64 upload.")
            data_bytes = raw
            await call_with_retry(
                lambda: gateway.upload_bytes(key=key, data=data_bytes, mime_type=input.mime_type)
            )
        if input.expires_in_seconds is not None:
            url = await gateway.presign_get(key=key, expires=input.expires_in_seconds)
        else:
            url = await gateway.presign_get(key=key)
        await get_runtime(ctx).object_key_ownership_store.record(key, get_principal(ctx))

    ttl = input.expires_in_seconds or settings.presign_ttl_seconds
    expires_at = (datetime.now(UTC) + timedelta(seconds=ttl)).isoformat()
    log_info(
        "media_upload_complete",
        object_key=key,
        media_type=input.media_type,
        bytes=raw_size,
    )
    return UploadResult(url=url, expires_at=expires_at, object_key=key, bytes=raw_size)


def require_object_storage(settings: Settings) -> None:
    """Raise when no object storage backend is configured."""
    if not settings.has_object_storage:
        raise ValueError(
            "Object storage is not configured. Set TOS_* or S3_* credentials and "
            "OBJECT_STORAGE_BACKEND (tos|s3)."
        )


class MediaUploadItem(BaseModel):
    """One file to upload."""

    media_type: Literal["image", "audio", "video"] = Field(
        ..., description="Media category used for validation."
    )
    mime_type: str | None = Field(
        None,
        description="MIME type (e.g. image/png). Inferred from file_path when omitted; required with data.",
    )
    data: str | None = Field(
        None, description="Base64-encoded bytes. Exactly one of data or file_path."
    )
    file_path: str | None = Field(
        None, description="Absolute local file path (stdio only). Exactly one of data or file_path."
    )
    key_prefix: str | None = Field(
        None,
        description="Object key prefix, default 'references'. Only alphanumerics, '-', '_', '/'.",
    )

    @model_validator(mode="after")
    def _infer_mime(self) -> MediaUploadItem:
        if self.mime_type is None:
            if self.file_path is None:
                raise ValueError("mime_type is required for data items.")
            self.mime_type = mimetypes.guess_type(self.file_path)[0]
        return self


class MediaUploadInput(BaseModel):
    """Input model for ``media_upload``."""

    items: list[MediaUploadItem] = Field(
        ..., min_length=1, max_length=50, description="1-50 files to upload."
    )
    expires_in_seconds: int | None = Field(
        None,
        ge=60,
        le=604800,
        description=(
            "Presigned URL validity in seconds for every item; defaults to the server presign "
            "TTL. Use >= 3600 for URLs destined for vod_* tools, which fetch asynchronously."
        ),
    )
    max_concurrent: int = Field(4, ge=1, le=8, description="Concurrent uploads.")


class MediaUploadItemResult(BaseModel):
    """Per-item upload result."""

    index: int = Field(..., description="0-based position in the request.")
    file_path: str | None = Field(None, description="The item's file_path as given.")
    url: str | None = Field(None, description="Presigned HTTPS GET URL; null if the item failed.")
    object_key: str | None = Field(
        None, description="Object key, reusable with media_presign and ark_asset_create."
    )
    expires_at: str | None = Field(None, description="ISO-8601 expiry of url.")
    mime_type: str | None = Field(None, description="MIME type used for the upload.")
    bytes: int | None = Field(None, description="Uploaded byte count.")
    error: str | None = Field(None, description="Failure reason; null on success.")
    retryable: bool | None = Field(
        None, description="Whether retrying the upload may succeed; null on success."
    )


class MediaUploadOutput(BaseModel):
    """Output model for ``media_upload``."""

    items: list[MediaUploadItemResult] = Field(
        ..., description="Per-item results in request order."
    )
    succeeded: int = Field(..., description="Number of items uploaded.")
    failed: int = Field(..., description="Number of items that failed; see items[].error.")
    total_bytes: int = Field(..., description="Total bytes uploaded across successful items.")


async def media_upload(input: MediaUploadInput, ctx: Context) -> MediaUploadOutput:
    """Upload 1-50 files (Base64 or local file_path, stdio only) to object storage and return presigned URLs.

    Use the URLs wherever a tool needs a media URL (e.g. Seedance video references, which are
    URL-only). Items fail independently: check failed and items[].error. Requires background
    execution (ark_job_submit).
    """
    await context_log(ctx, "info", f"Starting upload of {len(input.items)} items")
    settings = get_settings()
    require_object_storage(settings)

    prepared: list[tuple[UploadRequest, Path | None, bytes | None, int] | str] = []
    total = 0
    for item in input.items:
        if item.mime_type is None:
            name = Path(item.file_path or "").name
            prepared.append(f"Could not infer mime_type from '{name}'; set mime_type.")
            continue
        try:
            single = UploadRequest(
                media_type=item.media_type,
                mime_type=item.mime_type,
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
        if total > settings.media_upload_batch_max_bytes:
            raise ValueError(
                f"Upload exceeds MEDIA_UPLOAD_BATCH_MAX_BYTES "
                f"({settings.media_upload_batch_max_bytes} bytes) at item {len(prepared)}. "
                "Split it into smaller calls."
            )
        prepared.append((single, path, raw, size))
    await ctx.report_progress(progress=10, total=100)

    limiter = asyncio.Semaphore(input.max_concurrent)
    gateway = make_object_storage_gateway(settings)
    done = 0

    async def run(index: int) -> MediaUploadItemResult:
        nonlocal done
        item = input.items[index]
        entry = prepared[index]
        base = MediaUploadItemResult(
            index=index, file_path=item.file_path, mime_type=item.mime_type
        )
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
    log_info("media_upload_request_complete", total=len(items), succeeded=succeeded)
    return MediaUploadOutput(
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
