"""``media_upload`` tool — upload media to object storage, return a presigned URL.

Accepts Base64-encoded bytes or a local file path (stdio transport only),
uploads to a configured TOS or S3 bucket, and returns a presigned HTTPS GET URL
that can be passed to other tools (e.g. ``seedance_create_task`` video
references).  This is the integrated path for media that cannot be inlined
as Base64 — most notably Seedance video references, which are URL-only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastmcp import Context
from fastmcp.tools import ToolResult
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
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._task_execution import context_log


class MediaUploadInput(BaseModel):
    """Input model for ``media_upload``."""

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
    def _validate_input(self) -> MediaUploadInput:
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


class MediaUploadOutput(BaseModel):
    """Output model for ``media_upload``."""

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
    input: MediaUploadInput, settings: Settings
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
    input: MediaUploadInput,
    path: Path | None,
    raw: bytes | None,
    raw_size: int,
) -> MediaUploadOutput:
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
    return MediaUploadOutput(url=url, expires_at=expires_at, object_key=key, bytes=raw_size)


def require_object_storage(settings: Settings) -> None:
    """Raise when no object storage backend is configured."""
    if not settings.has_object_storage:
        raise ValueError(
            "Object storage is not configured. Set TOS_* or S3_* credentials and "
            "OBJECT_STORAGE_BACKEND (tos|s3)."
        )


async def media_upload(input: MediaUploadInput, ctx: Context) -> MediaUploadOutput | ToolResult:
    """Upload media to object storage (TOS or S3) and return a presigned HTTPS GET URL.

    The returned URL can be passed directly to tools that accept media URLs,
    such as ``seedance_create_task`` (video references).  Video references are
    URL-only — this tool is the integrated upload path for them. Requires MCP
    task-augmented execution because uploads can contain up to 200 MiB of video.
    For several files, use media_upload_batch.
    """
    await context_log(ctx, "info", "Starting media upload")
    await ctx.report_progress(progress=10, total=100)

    settings = get_settings()
    require_object_storage(settings)
    path, raw, raw_size = prepare_upload(input, settings)

    await ctx.report_progress(progress=30, total=100)

    gateway = make_object_storage_gateway(settings)
    try:
        result = await upload_one(ctx, settings, gateway, input, path, raw, raw_size)
    except ProviderError as exc:
        await context_log(ctx, "error", f"Media upload failed: {exc.message}")
        return provider_error_result(exc)
    finally:
        await gateway.close()

    await ctx.report_progress(progress=100, total=100)
    return result


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
