"""Domain artifact models.

``ArtifactRef`` is the stable cross-tool contract for persisted media. Every
successful tool result returns one or more ``ArtifactRef`` instances that
point to durable ``seed-media://artifacts/{id}`` resources, so MCP clients
can retrieve media long after the provider URL expires (2h for audio,
24h for image/video).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class MediaType(StrEnum):
    """Logical generated-media categories."""

    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    THREE_D = "three_d"


class ArtifactPersistenceIssue(BaseModel):
    """Safe explanation for a provider output that could not be persisted."""

    code: Literal[
        "untrusted_output_host",
        "output_too_large",
        "invalid_output_mime",
        "source_expired",
        "download_failed",
        "storage_failed",
    ] = Field(description="Stable persistence failure category.")
    message: str = Field(description="Credential- and URL-safe persistence failure message.")
    retryable: bool = Field(description="Whether persistence may succeed if attempted again later.")
    artifact_limit_bytes: int = Field(
        description="Maximum output size accepted by the durable artifact policy, in bytes."
    )
    source_url_expires_at: str | None = Field(
        default=None,
        description=(
            "ISO-8601 timestamp when the unpersisted provider URL (returned as the "
            "artifact's uri) expires. None when no provider URL is available."
        ),
    )


class ArtifactRef(BaseModel):
    """Stable reference to a persisted media artifact."""

    id: str = Field(..., description="Unique artifact identifier.")
    uri: str = Field(..., description="MCP resource URI (seed-media://artifacts/{id}).")
    media_type: MediaType = Field(..., description="Logical media type: image, audio, or video.")
    mime_type: str = Field(..., description="MIME type of the stored content (e.g. image/png).")
    bytes: int | None = Field(default=None, description="Size of the stored content in bytes.")
    sha256: str | None = Field(
        default=None, description="SHA-256 hex digest of the stored content."
    )
    created_at: str = Field(..., description="ISO-8601 timestamp of artifact creation.")
    expires_at: str | None = Field(
        default=None,
        description="ISO-8601 timestamp when the local artifact expires.",
    )
    source_expires_at: str | None = Field(
        default=None,
        description="ISO-8601 timestamp when the provider URL expires.",
    )
    persistence_error: ArtifactPersistenceIssue | None = Field(
        default=None,
        description=(
            "Set when the output was generated (and billed) but could not be stored durably. "
            "Then id is 'provider-url' and uri is the temporary provider URL (valid until "
            "source_expires_at), or id is 'inline-fallback' and the bytes are in fallback_data. "
            "Re-persist a provider URL with seed_media_persist_url before it expires."
        ),
    )
    fallback_data: str | None = Field(
        default=None,
        description=(
            "Base64-encoded output bytes, returned only when inline output could not be "
            "stored and is within ARTIFACT_INLINE_FALLBACK_MAX_BYTES. Otherwise None."
        ),
    )
    local_path: str | None = Field(
        default=None,
        description=(
            "Absolute local file path the artifact was written to when output_path or "
            "output_dir was requested (stdio only). None when no local copy was written."
        ),
    )
    export_error: str | None = Field(
        default=None,
        description=(
            "Why the requested local copy (output_path/output_dir) was not written. "
            "The durable artifact is unaffected. None when no copy was requested or it succeeded."
        ),
    )
