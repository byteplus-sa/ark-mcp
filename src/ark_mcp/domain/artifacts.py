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
    ] = Field(description="Failure category.")
    message: str = Field(description="Safe failure message (no credentials or URLs).")
    retryable: bool = Field(description="Whether retrying persistence later may succeed.")
    artifact_limit_bytes: int = Field(description="Maximum durable artifact size in bytes.")
    source_url_expires_at: str | None = Field(
        default=None,
        description="ISO-8601 expiry of the unpersisted provider URL (the artifact uri).",
    )


class ArtifactRef(BaseModel):
    """Stable reference to a persisted media artifact."""

    id: str = Field(..., description="Unique artifact identifier.")
    uri: str = Field(..., description="MCP resource URI (seed-media://artifacts/{id}).")
    media_type: MediaType = Field(..., description="image, audio, video or three_d.")
    mime_type: str = Field(..., description="MIME type, e.g. image/png.")
    bytes: int | None = Field(default=None, description="Size in bytes.")
    sha256: str | None = Field(default=None, description="SHA-256 hex digest.")
    created_at: str = Field(..., description="ISO-8601 timestamp of artifact creation.")
    expires_at: str | None = Field(
        default=None,
        description="ISO-8601 expiry of the stored artifact.",
    )
    source_expires_at: str | None = Field(
        default=None,
        description="ISO-8601 expiry of the provider URL.",
    )
    persistence_error: ArtifactPersistenceIssue | None = Field(
        default=None,
        description=(
            "Set when the output was billed but not stored. id is then 'provider-url' (uri is "
            "the temporary provider URL; re-persist it with seed_media_persist_url before "
            "source_expires_at) or 'inline-fallback' (bytes in fallback_data)."
        ),
    )
    fallback_data: str | None = Field(
        default=None,
        description="Base64 output bytes, only with id 'inline-fallback'.",
    )
    local_path: str | None = Field(
        default=None,
        description="Absolute path of the local copy written for output_path/output_dir.",
    )
    export_error: str | None = Field(
        default=None,
        description="Why the requested local copy was not written (the artifact is unaffected).",
    )
