"""``seed_media_export_artifact`` tool — locate or copy a persisted artifact.

Returns the absolute on-disk path of a persisted artifact so MCP clients can
copy the file directly instead of streaming its Base64 bytes through the
context window. Stdio transport only: the client and server must share a
filesystem for the returned path to be meaningful.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from fastmcp import Context
from pydantic import BaseModel, Field

from ark_mcp.domain.artifacts import MediaType
from ark_mcp.observability.logger import info as log_info
from ark_mcp.runtime import get_principal, get_runtime
from ark_mcp.tools._task_execution import context_log


class SeedMediaExportArtifactInput(BaseModel):
    """Input model for ``seed_media_export_artifact``."""

    artifact_id: str = Field(
        ...,
        min_length=1,
        description="The artifact ID returned by a previous generation call.",
    )
    destination_path: str | None = Field(
        None,
        description=(
            "Optional absolute path where the server writes an atomic copy of the artifact. "
            "When omitted, the tool returns the canonical on-disk path of a filesystem-backed "
            "artifact instead."
        ),
    )


class SeedMediaExportArtifactOutput(BaseModel):
    """Output model for ``seed_media_export_artifact``."""

    artifact_id: str = Field(..., description="Unique artifact identifier.")
    path: str = Field(..., description="Absolute on-disk path of the exported media file.")
    media_type: MediaType = Field(
        ..., description="Logical media type: image, audio, video, or three_d."
    )
    mime_type: str = Field(..., description="MIME type of the stored content (e.g. image/png).")
    bytes: int = Field(..., description="Size of the stored content in bytes.")
    sha256: str | None = Field(None, description="SHA-256 hex digest of the stored content.")
    copied: bool = Field(
        ...,
        description=(
            "True when the artifact was copied to destination_path; False when path is the "
            "canonical store location."
        ),
    )


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "wb") as file_obj:
            file_obj.write(data)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _atomic_copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=dst.parent, prefix=".tmp_")
    os.close(fd)
    try:
        shutil.copyfile(src, tmp_path)
        os.replace(tmp_path, dst)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


async def seed_media_export_artifact(
    input: SeedMediaExportArtifactInput, ctx: Context
) -> SeedMediaExportArtifactOutput:
    """Locate or copy a persisted media artifact on the local filesystem.

    Returns the absolute on-disk path of a persisted artifact instead of
    streaming its Base64 bytes through the MCP context. With ``destination_path``
    the server writes an atomic copy of the artifact to that path; otherwise it
    returns the canonical store path for a filesystem-backed artifact store.
    Only available over stdio transport, where the client and server share a
    filesystem.
    """
    await context_log(ctx, "info", f"Exporting artifact {input.artifact_id}")

    runtime = get_runtime(ctx)
    auth = get_principal(ctx)
    if runtime.settings.mcp_transport != "stdio":
        raise ValueError(
            "seed_media_export_artifact is only supported in stdio transport mode; "
            "the client and server must share a filesystem."
        )

    location = await runtime.artifact_store.locate(input.artifact_id, auth=auth)
    ref = location.ref

    if input.destination_path is not None:
        destination = Path(input.destination_path).expanduser().resolve()
        if location.path is not None:
            _atomic_copy_file(location.path, destination)
        else:
            stored = await runtime.artifact_store.get(input.artifact_id, auth=auth)
            _atomic_write_bytes(destination, stored.data)
        exported_path = str(destination)
        copied = True
    else:
        if location.path is None:
            raise ValueError(
                "The artifact store does not keep artifacts on the local filesystem; "
                "provide destination_path to export a copy."
            )
        exported_path = str(location.path)
        copied = False

    byte_count = ref.bytes
    if byte_count is None and location.path is not None:
        byte_count = location.path.stat().st_size

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
