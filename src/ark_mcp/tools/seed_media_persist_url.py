"""``seed_media_persist_url`` tool — persist a temporary provider URL durably.

Recovers outputs that were generated and billed but could not be stored at
generation time (``ArtifactRef.persistence_error`` with ``id='provider-url'``).
Only trusted BytePlus provider hosts are accepted: the URL goes through the
same SSRF-resistant downloader, host allowlist, redirect re-validation, and
size limits as generation-time persistence.
"""

from __future__ import annotations

from fastmcp import Context
from pydantic import BaseModel, Field, model_validator

from ark_mcp.artifacts.store import MAX_AUDIO_URL_DOWNLOAD_BYTES, ArtifactPersistenceError
from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.observability.logger import info as log_info
from ark_mcp.runtime import get_principal, get_runtime
from ark_mcp.tools._task_execution import context_log


class SeedMediaPersistUrlInput(BaseModel):
    """Input model for ``seed_media_persist_url``."""

    url: str = Field(
        ...,
        min_length=1,
        max_length=8192,
        description="Provider output URL, usually the uri of an ArtifactRef with id 'provider-url'. BytePlus hosts only.",
    )
    media_type: MediaType = Field(
        ..., description="Media type of the output: image, audio, or video."
    )
    mime_type: str = Field(
        ...,
        min_length=3,
        max_length=128,
        description="Expected MIME type (e.g. video/mp4); a specific provider Content-Type takes precedence.",
    )
    source_expires_at: str | None = Field(
        None,
        description="ISO-8601 URL expiry, from ArtifactRef.source_expires_at; recorded on the new artifact.",
    )
    max_download_bytes: int | None = Field(
        None,
        ge=1,
        le=MAX_AUDIO_URL_DOWNLOAD_BYTES,
        description="Audio only. Optional byte cap (up to 90 MiB). Use the artifact's persistence_error.artifact_limit_bytes for a retry.",
    )

    @model_validator(mode="after")
    def validate_download_limit(self) -> SeedMediaPersistUrlInput:
        if self.max_download_bytes is not None and self.media_type != MediaType.AUDIO:
            raise ValueError("max_download_bytes is supported only for audio.")
        return self


class SeedMediaPersistUrlOutput(BaseModel):
    """Output model for ``seed_media_persist_url``."""

    artifact: ArtifactRef = Field(..., description="The durable artifact.")


async def seed_media_persist_url(
    input: SeedMediaPersistUrlInput, ctx: Context
) -> SeedMediaPersistUrlOutput:
    """Persist a provider output URL as a durable artifact and return its ArtifactRef.

    Use when a generation result has an artifact with id 'provider-url', before the URL expires.
    This tool accepts trusted BytePlus provider hosts.
    """
    await context_log(ctx, "info", "Persisting provider output URL")
    runtime = get_runtime(ctx)
    try:
        ref = await runtime.artifact_store.copy_from_trusted_url(
            url=input.url,
            media_type=input.media_type,
            mime_type=input.mime_type,
            source_expires_at=input.source_expires_at,
            auth=get_principal(ctx),
            max_download_bytes=input.max_download_bytes,
        )
    except ArtifactPersistenceError as exc:
        raise ValueError(
            f"Could not persist provider output ({exc.code}, retryable={exc.retryable}): "
            f"{exc.safe_message}"
        ) from exc
    log_info("artifact_persisted_from_url", artifact_id=ref.id, media_type=input.media_type)
    return SeedMediaPersistUrlOutput(artifact=ref)


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
