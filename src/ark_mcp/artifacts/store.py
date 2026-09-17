"""Artifact store protocol and types.

``ArtifactStore`` copies outputs into local storage for ``stdio`` or an
object store for remote deployments and returns ``seed-media://artifacts/{id}``.

Provider URLs live for 2 hours (audio) or 24 hours (image/video). The
``ArtifactStore`` persists outputs immediately so MCP resources remain
usable after provider URL expiry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.security.auth_context import AuthContext

ArtifactPersistenceErrorCode = Literal[
    "untrusted_output_host",
    "output_too_large",
    "invalid_output_mime",
    "source_expired",
    "download_failed",
    "storage_failed",
]


class ArtifactPersistenceError(ValueError):
    """Classified artifact failure safe to expose without provider URLs."""

    def __init__(
        self,
        code: ArtifactPersistenceErrorCode,
        safe_message: str,
        *,
        retryable: bool,
    ) -> None:
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable
        super().__init__(safe_message)


class Base64ArtifactInput(BaseModel):
    """Input for storing Base64-encoded media."""

    data: str = Field(..., description="Base64-encoded media data.")
    media_type: MediaType
    mime_type: str
    source_expires_at: str | None = None


class TrustedUrlArtifactInput(BaseModel):
    """Input for copying media from a trusted provider URL."""

    url: str = Field(..., description="Trusted HTTPS URL of the media.")
    media_type: MediaType
    mime_type: str
    source_expires_at: str | None = None


class StoredArtifact(BaseModel):
    """A stored artifact returned by ``ArtifactStore.get``."""

    data: bytes
    media_type: MediaType
    mime_type: str
    artifact_id: str

    model_config = {"arbitrary_types_allowed": True}


@dataclass(frozen=True, slots=True)
class ArtifactLocation:
    """On-disk location of a stored artifact plus its reference.

    ``path`` is ``None`` for object-storage backends, where no local file exists.
    """

    path: Path | None
    ref: ArtifactRef


class ArtifactMetadata(BaseModel):
    """Versioned ownership metadata stored beside an artifact."""

    schema_version: Literal[2] = 2
    ref: ArtifactRef
    principal_id: str
    tenant_id: str


@runtime_checkable
class ArtifactStore(Protocol):
    """Protocol for artifact persistence backends."""

    async def put_base64(
        self,
        data: str,
        media_type: MediaType,
        mime_type: str,
        source_expires_at: str | None = None,
        auth: AuthContext | None = None,
    ) -> ArtifactRef:
        """Store Base64-encoded media and return a durable artifact reference."""
        ...

    async def copy_from_trusted_url(
        self,
        url: str,
        media_type: MediaType,
        mime_type: str,
        source_expires_at: str | None = None,
        auth: AuthContext | None = None,
    ) -> ArtifactRef:
        """Download media from a trusted provider URL and store it."""
        ...

    async def get(self, artifact_id: str, auth: AuthContext | None = None) -> StoredArtifact:
        """Retrieve a stored artifact by ID."""
        ...

    async def locate(self, artifact_id: str, auth: AuthContext | None = None) -> ArtifactLocation:
        """Resolve the on-disk path and metadata of a stored artifact.

        Returns ``path=None`` for object-storage backends where no local file exists.
        """
        ...

    async def delete_expired(self, now: datetime) -> int:
        """Delete expired artifacts. Returns the number deleted."""
        ...

    async def close(self) -> None:
        """Release backend resources."""
        ...
