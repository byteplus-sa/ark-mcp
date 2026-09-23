"""``seed_media_persist_url`` tool — persist a temporary provider URL durably.

Recovers outputs that were generated and billed but could not be stored at
generation time (``ArtifactRef.persistence_error`` with ``id='provider-url'``).
Only trusted BytePlus provider hosts are accepted: the URL goes through the
same SSRF-resistant downloader, host allowlist, redirect re-validation, and
size limits as generation-time persistence.
"""

from __future__ import annotations

from typing import Literal

from fastmcp import Context
from pydantic import BaseModel, Field

from ark_mcp.artifacts.store import ArtifactPersistenceError
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
        description=(
            "Temporary HTTPS provider URL to persist, typically the uri of an ArtifactRef whose "
            "id is 'provider-url'. Only trusted BytePlus provider hosts are accepted."
        ),
    )
    media_type: Literal["image", "audio", "video", "three_d"] = Field(
        ..., description="Logical media type of the output: image, audio, video, or three_d."
    )
    mime_type: str = Field(
        ...,
        min_length=3,
        max_length=128,
        description=(
            "Expected MIME type (e.g. image/jpeg, video/mp4, audio/wav, model/gltf-binary). "
            "A specific Content-Type returned by the provider takes precedence."
        ),
    )
    source_expires_at: str | None = Field(
        None,
        description=(
            "Optional ISO-8601 expiry of the provider URL, copied from the original "
            "ArtifactRef.source_expires_at, recorded on the new artifact."
        ),
    )


class SeedMediaPersistUrlOutput(BaseModel):
    """Output model for ``seed_media_persist_url``."""

    artifact: ArtifactRef = Field(
        ..., description="The durable artifact now stored for the provider output."
    )


async def seed_media_persist_url(
    input: SeedMediaPersistUrlInput, ctx: Context
) -> SeedMediaPersistUrlOutput:
    """Persist a temporary provider output URL as a durable media artifact.

    Use this when a generation result returned an artifact with id
    'provider-url' and a persistence_error, to store the output before the
    provider URL expires (2h for audio, 24h for image/video/3D). Only trusted
    BytePlus provider hosts are downloaded; any other URL is rejected. Returns
    the new durable ArtifactRef.
    """
    await context_log(ctx, "info", "Persisting provider output URL")
    runtime = get_runtime(ctx)
    try:
        ref = await runtime.artifact_store.copy_from_trusted_url(
            url=input.url,
            media_type=MediaType(input.media_type),
            mime_type=input.mime_type,
            source_expires_at=input.source_expires_at,
            auth=get_principal(ctx),
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
