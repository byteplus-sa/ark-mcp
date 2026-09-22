"""Write persisted artifacts to caller-chosen local paths.

Shared by ``seed_media_export_artifact`` and the ``output_path`` /
``output_dir`` options on generation and task-retrieval tools. Bytes come
from the local store file when the backend keeps one, otherwise from
``ArtifactStore.get`` (object storage). Inline-fallback artifacts are written
from their ``fallback_data``; provider-URL artifacts cannot be written
locally and get an ``export_error`` instead.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import TYPE_CHECKING

from ark_mcp.artifacts.filesystem_store import _mime_to_ext
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.security.output_paths import (
    OutputPathError,
    WriteOutcome,
    validate_output_target,
    write_output_file,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastmcp import Context

    from ark_mcp.artifacts.store import ArtifactStore
    from ark_mcp.config.env import Settings
    from ark_mcp.domain.artifacts import ArtifactRef
    from ark_mcp.security.auth_context import AuthContext

_PROVIDER_URL_ID = "provider-url"
_INLINE_FALLBACK_ID = "inline-fallback"


class LocalExportTarget:
    """A validated ``output_path``/``output_dir`` destination for one tool call."""

    def __init__(self, path: Path, roots: list[Path], *, is_dir: bool, overwrite: bool) -> None:
        self.path = path
        self.roots = roots
        self.is_dir = is_dir
        self.overwrite = overwrite

    def destination_for(self, ref: ArtifactRef, *, suffix: str = "") -> Path:
        """Return the file path for ``ref`` (``suffix`` distinguishes siblings)."""
        ext = _mime_to_ext(ref.mime_type)
        if self.is_dir:
            stem = ref.id if ref.id not in {_PROVIDER_URL_ID, _INLINE_FALLBACK_ID} else "output"
            return self.path / f"{stem}{suffix}{ext}"
        if not suffix:
            return self.path
        return self.path.with_name(f"{self.path.stem}{suffix}{self.path.suffix or ext}")


async def prepare_local_export(
    *,
    output_path: str | None,
    output_dir: str | None = None,
    overwrite: bool,
    ctx: Context | None,
    settings: Settings,
    require_dir: bool = False,
) -> LocalExportTarget | None:
    """Validate ``output_path``/``output_dir`` before any billable call.

    ``output_path`` names a file, or a directory when it ends with a path
    separator. ``require_dir`` rejects a file path when the call can produce
    more than one artifact.
    """
    if output_path is not None and output_dir is not None:
        raise OutputPathError("Provide at most one of output_path and output_dir.")
    raw = output_dir if output_dir is not None else output_path
    if raw is None:
        return None
    field = "output_dir" if output_dir is not None else "output_path"
    is_dir = output_dir is not None or raw.endswith(("/", "\\"))
    if require_dir and not is_dir:
        raise OutputPathError(
            f"{field} must be a directory (ending with '/') because this call can produce "
            "more than one artifact."
        )
    validated = await validate_output_target(
        raw, ctx=ctx, settings=settings, kind="dir" if is_dir else "file", field=field
    )
    assert validated is not None
    path, roots = validated
    return LocalExportTarget(path, roots, is_dir=is_dir, overwrite=overwrite)


async def artifact_bytes(store: ArtifactStore, ref: ArtifactRef, auth: AuthContext | None) -> bytes:
    """Read a persisted artifact's bytes from the local file or the store."""
    location = await store.locate(ref.id, auth=auth)
    if location.path is not None:
        return Path(location.path).read_bytes()
    stored = await store.get(ref.id, auth=auth)
    return stored.data


async def write_artifact(
    store: ArtifactStore,
    ref: ArtifactRef,
    dest: Path,
    *,
    roots: Sequence[Path],
    overwrite: bool,
    auth: AuthContext | None,
) -> WriteOutcome:
    """Write one artifact to ``dest``; raises on failure."""
    if ref.id == _PROVIDER_URL_ID:
        raise OutputPathError(
            "Output was not persisted, so no local copy was written; persist it with "
            "seed_media_persist_url first."
        )
    if ref.id == _INLINE_FALLBACK_ID:
        if not ref.fallback_data:
            raise OutputPathError("Inline fallback output has no data to write.")
        data = base64.b64decode(ref.fallback_data)
    else:
        data = await artifact_bytes(store, ref, auth)
    return write_output_file(dest, data, roots=roots, overwrite=overwrite)


async def export_ref(
    store: ArtifactStore,
    ref: ArtifactRef | None,
    target: LocalExportTarget | None,
    *,
    auth: AuthContext | None,
    suffix: str = "",
) -> ArtifactRef | None:
    """Return ``ref`` with ``local_path`` or ``export_error`` set. Never raises.

    The durable artifact is unaffected by a failed local write, so a failure
    is reported on the reference instead of failing the (already billed) call.
    """
    if ref is None or target is None:
        return ref
    try:
        outcome = await write_artifact(
            store,
            ref,
            target.destination_for(ref, suffix=suffix),
            roots=target.roots,
            overwrite=target.overwrite,
            auth=auth,
        )
    except (OutputPathError, OSError, FileNotFoundError, PermissionError) as exc:
        log_warning("artifact_local_export_failed", artifact_id=ref.id, error=type(exc).__name__)
        return ref.model_copy(update={"local_path": None, "export_error": str(exc)})
    return ref.model_copy(update={"local_path": str(outcome.path), "export_error": None})


async def export_refs(
    store: ArtifactStore,
    refs: Sequence[ArtifactRef],
    target: LocalExportTarget | None,
    *,
    auth: AuthContext | None,
) -> list[ArtifactRef]:
    """Export several artifacts; file targets get ``-2``, ``-3`` ... suffixes."""
    if target is None:
        return list(refs)
    exported: list[ArtifactRef] = []
    for index, ref in enumerate(refs):
        suffix = "" if index == 0 or target.is_dir else f"-{index + 1}"
        result = await export_ref(store, ref, target, auth=auth, suffix=suffix)
        assert result is not None
        exported.append(result)
    return exported
