"""``seed_media_export_artifact`` tool — locate or copy a persisted artifact.

Returns the absolute on-disk path of a persisted artifact so MCP clients can
copy the file directly instead of streaming its Base64 bytes through the
context window. Stdio transport only: the client and server must share a
filesystem for the returned path to be meaningful. Copies go through the
shared output-root policy (``security/output_paths.py``): the destination
must be absolute and inside an allowed output root, and an existing file is
only replaced when ``overwrite`` is set (identical content always succeeds).
"""

from __future__ import annotations

from fastmcp import Context
from pydantic import BaseModel, Field

from ark_mcp.artifacts.export import write_artifact
from ark_mcp.domain.artifacts import MediaType
from ark_mcp.observability.logger import info as log_info
from ark_mcp.runtime import get_principal, get_runtime
from ark_mcp.security.output_paths import validate_output_target
from ark_mcp.tools._task_execution import context_log


class SeedMediaExportArtifactInput(BaseModel):
    """Input model for ``seed_media_export_artifact``."""

    artifact_id: str = Field(
        ...,
        min_length=1,
        description="Artifact ID (ArtifactRef.id) from a generation result.",
    )
    destination_path: str | None = Field(
        None,
        description=(
            "Absolute path for a copy of the artifact, inside an allowed output root (client "
            "roots or OUTPUT_ROOTS). Omit to get the store path instead."
        ),
    )
    overwrite: bool = Field(
        False,
        description="Allow replacing an existing destination file with different content.",
    )


class SeedMediaExportArtifactOutput(BaseModel):
    """Output model for ``seed_media_export_artifact``."""

    artifact_id: str = Field(..., description="Artifact identifier.")
    path: str = Field(..., description="Absolute on-disk path of the file.")
    media_type: MediaType = Field(..., description="image, audio, video, or three_d.")
    mime_type: str = Field(..., description="MIME type of the stored content.")
    bytes: int = Field(..., description="Size in bytes.")
    sha256: str | None = Field(None, description="SHA-256 hex digest.")
    copied: bool = Field(
        ...,
        description="True when path is a copy at destination_path; false when it is the store location.",
    )


async def seed_media_export_artifact(
    input: SeedMediaExportArtifactInput, ctx: Context
) -> SeedMediaExportArtifactOutput:
    """Get the local file path of a persisted artifact, or copy it to destination_path.

    Avoids streaming Base64 through context. Stdio transport only (client and server share a
    filesystem).
    """
    await context_log(ctx, "info", f"Exporting artifact {input.artifact_id}")

    runtime = get_runtime(ctx)
    auth = get_principal(ctx)
    if runtime.settings.mcp_transport != "stdio":
        raise ValueError(
            "seed_media_export_artifact is only supported in stdio transport mode; "
            "the client and server must share a filesystem."
        )

    target = await validate_output_target(
        input.destination_path,
        ctx=ctx,
        settings=runtime.settings,
        kind="file",
        field="destination_path",
    )
    location = await runtime.artifact_store.locate(input.artifact_id, auth=auth)
    ref = location.ref

    if target is not None:
        destination, roots = target
        outcome = await write_artifact(
            runtime.artifact_store,
            ref,
            destination,
            roots=roots,
            overwrite=input.overwrite,
            auth=auth,
        )
        exported_path = str(outcome.path)
        byte_count: int | None = outcome.bytes
        copied = True
    else:
        if location.path is None:
            raise ValueError(
                "The artifact store does not keep artifacts on the local filesystem; "
                "provide destination_path to export a copy."
            )
        exported_path = str(location.path)
        byte_count = ref.bytes if ref.bytes is not None else location.path.stat().st_size
        copied = False

    log_info(
        "artifact_exported",
        artifact_id=input.artifact_id,
        path=exported_path,
        copied=copied,
    )

    return SeedMediaExportArtifactOutput(
        artifact_id=input.artifact_id,
        path=exported_path,
        media_type=ref.media_type,
        mime_type=ref.mime_type,
        bytes=byte_count or 0,
        sha256=ref.sha256,
        copied=copied,
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
