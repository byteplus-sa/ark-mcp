"""seed_media_get_artifact tool - retrieve persisted media by artifact ID."""

from __future__ import annotations

import base64
import hashlib

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field

from ark_mcp.domain.artifacts import MediaType
from ark_mcp.observability.logger import info as log_info
from ark_mcp.runtime import get_principal, get_runtime
from ark_mcp.tools._task_execution import context_log


class SeedMediaGetArtifactInput(BaseModel):
    artifact_id: str = Field(
        ...,
        min_length=1,
        description="Artifact ID (ArtifactRef.id) from a generation result.",
    )


class SeedMediaGetArtifactOutput(BaseModel):
    """Output model for ``seed_media_get_artifact``."""

    artifact_id: str = Field(..., description="Artifact identifier.")
    media_type: MediaType = Field(..., description="image, audio, video, or three_d.")
    mime_type: str = Field(..., description="MIME type of the stored content.")
    sha256: str = Field(..., description="SHA-256 hex digest of the stored content.")
    bytes: int = Field(..., description="Size of the stored content in bytes.")
    expires_at: str | None = Field(
        None, description="ISO-8601 expiry of the stored artifact, if any."
    )
    data: str = Field(..., description="Base64-encoded media data.")


async def seed_media_get_artifact(
    input: SeedMediaGetArtifactInput, ctx: Context
) -> SeedMediaGetArtifactOutput | ToolResult:
    """Fetch persisted media by artifact ID as Base64 plus metadata.

    Use after the provider URL has expired (2h audio, 24h image/video/3D). Large files cost
    context; prefer seed_media_export_artifact on stdio.
    """
    await context_log(ctx, "info", f"Fetching artifact {input.artifact_id}")

    runtime = get_runtime(ctx)
    auth = get_principal(ctx)

    stored = await runtime.artifact_store.get(input.artifact_id, auth=auth)

    sha256 = hashlib.sha256(stored.data).hexdigest()
    b64_data = base64.b64encode(stored.data).decode("ascii")

    log_info(
        "artifact_fetched",
        artifact_id=input.artifact_id,
        media_type=stored.media_type,
        mime_type=stored.mime_type,
        bytes=len(stored.data),
    )

    return SeedMediaGetArtifactOutput(
        artifact_id=input.artifact_id,
        media_type=stored.media_type,
        mime_type=stored.mime_type,
        sha256=sha256,
        bytes=len(stored.data),
        data=b64_data,
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
