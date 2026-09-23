"""Output-root policy and TOCTOU-safe local file writes.

Every tool that writes to a caller-chosen local path (``save_to``,
``output_path``, ``output_dir``, ``destination_path``) goes through this
module:

- stdio transport only: the client and server must share a filesystem;
- the path must be absolute and resolve inside an allowed root: the client's
  MCP roots (``roots/list``) when it provides them, otherwise the
  ``OUTPUT_ROOTS`` setting; with neither, path writing is disabled;
- writes create parents one level at a time, re-checking containment, open
  the final directory with ``O_NOFOLLOW``, and publish atomically through
  ``dir_fd``: exclusively (``link``) unless ``overwrite`` is set;
- a destination that already holds identical bytes (same SHA-256) is a
  success, so repeated polls and re-exports stay idempotent.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import unquote, urlparse

from ark_mcp.observability.logger import debug as log_debug

try:  # POSIX only; Windows uses the portable fallback writer below.
    import fcntl
except ImportError:  # pragma: no cover - exercised on Windows only
    fcntl = None  # type: ignore[assignment]

_DIR_FD_SUPPORTED = (
    os.open in os.supports_dir_fd
    and os.link in os.supports_dir_fd
    and hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from fastmcp import Context

    from ark_mcp.config.env import Settings

_ROOTS_TIMEOUT_SECONDS = 2.0


class OutputPathError(ValueError):
    """A caller-supplied output path is not allowed or could not be written."""


@dataclass(frozen=True, slots=True)
class WriteOutcome:
    """Result of one local file write."""

    path: Path
    bytes: int
    status: Literal["written", "overwritten", "already_present"]


def require_stdio(settings: Settings, field: str) -> None:
    """Raise unless the server runs over stdio, where paths are meaningful."""
    if settings.mcp_transport != "stdio":
        raise OutputPathError(
            f"{field} is only supported in stdio transport mode; "
            "the client and server must share a filesystem."
        )


async def _client_roots(ctx: Context | None) -> list[Path]:
    """Return the client's ``file://`` MCP roots, or [] when unavailable."""
    if ctx is None:
        return []
    session = getattr(ctx, "session", None)
    list_roots = getattr(session, "list_roots", None)
    if list_roots is None:
        return []
    try:
        result = await asyncio.wait_for(list_roots(), timeout=_ROOTS_TIMEOUT_SECONDS)
    except Exception as exc:  # client without roots support, background task, timeout
        log_debug("mcp_roots_unavailable", error=type(exc).__name__)
        return []
    roots: list[Path] = []
    for root in getattr(result, "roots", []) or []:
        parsed = urlparse(str(root.uri))
        if parsed.scheme == "file" and parsed.path:
            roots.append(Path(unquote(parsed.path)))
    return roots


async def allowed_output_roots(ctx: Context | None, settings: Settings) -> list[Path]:
    """Resolve the allowed output roots: MCP roots first, then ``OUTPUT_ROOTS``."""
    raw = await _client_roots(ctx) or [Path(p) for p in settings.output_root_paths]
    roots: list[Path] = []
    for root in raw:
        expanded = root.expanduser()
        if expanded.is_absolute():
            roots.append(expanded.resolve())
    return roots


def _inside(path: Path, roots: Sequence[Path]) -> bool:
    return any(path == root or path.is_relative_to(root) for root in roots)


def resolve_output_path(
    raw: str,
    *,
    roots: Sequence[Path],
    kind: Literal["file", "dir"],
    field: str = "output_path",
) -> Path:
    """Validate ``raw`` against the output roots and return the resolved path.

    Raises ``OutputPathError`` for relative paths, paths outside every root,
    or when no root is configured. Call this before any billable provider
    call so a bad path never produces paid output that cannot be written.
    """
    if not roots:
        raise OutputPathError(
            f"{field} is disabled: no output roots are configured. Set OUTPUT_ROOTS to one "
            "or more absolute directories, or use an MCP client that advertises roots."
        )
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise OutputPathError(f"{field} must be an absolute path.")
    resolved = candidate.resolve()
    if not _inside(resolved, roots):
        raise OutputPathError(f"{field} must be inside an allowed output root.")
    if kind == "file":
        if raw.endswith(os.sep) or resolved.is_dir():
            raise OutputPathError(f"{field} must name a file, not a directory.")
        if resolved in roots:
            raise OutputPathError(f"{field} must name a file inside an output root.")
    return resolved


async def validate_output_target(
    raw: str | None,
    *,
    ctx: Context | None,
    settings: Settings,
    kind: Literal["file", "dir"],
    field: str,
) -> tuple[Path, list[Path]] | None:
    """Pre-flight check for a tool's output path. Returns (path, roots) or None."""
    if raw is None:
        return None
    require_stdio(settings, field)
    roots = await allowed_output_roots(ctx, settings)
    return resolve_output_path(raw, roots=roots, kind=kind, field=field), roots


def _ensure_parents(directory: Path, roots: Sequence[Path]) -> None:
    """Create missing directories one level at a time, re-checking containment."""
    missing: list[Path] = []
    current = directory
    while not current.exists():
        missing.append(current)
        if current.parent == current:
            break
        current = current.parent
    for path in reversed(missing):
        with suppress(FileExistsError):
            path.mkdir()
        if not _inside(path.resolve(), roots):
            raise OutputPathError("Output directory escaped the allowed output root.")


def _fd_directory(dir_fd: int, fallback: Path) -> Path:
    """Return the real location of an open directory descriptor.

    Uses ``F_GETPATH`` (macOS) or ``/proc/self/fd`` (Linux) so the containment
    check applies to the directory actually opened, not to a path that may
    have been swapped since.
    """
    get_path = getattr(fcntl, "F_GETPATH", None) if fcntl is not None else None
    if get_path is not None:
        raw = fcntl.fcntl(dir_fd, get_path, b"\0" * 1024)
        return Path(os.fsdecode(raw.split(b"\0", 1)[0])).resolve()
    proc_link = f"/proc/self/fd/{dir_fd}"
    if os.path.exists(proc_link):
        return Path(os.readlink(proc_link)).resolve()
    return Path(os.path.realpath(fallback))


def _sha256_of(dir_fd: int, name: str) -> str | None:
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
    except (FileNotFoundError, OSError):
        return None
    digest = hashlib.sha256()
    with os.fdopen(fd, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_output_file(
    dest: Path,
    data: bytes,
    *,
    roots: Sequence[Path],
    overwrite: bool,
) -> WriteOutcome:
    """Atomically write ``data`` to ``dest`` inside ``roots``.

    The final directory is opened with ``O_NOFOLLOW`` and containment is
    re-checked after opening, so a symlink swapped in after validation cannot
    redirect the write. Without ``overwrite`` the publish is exclusive
    (``link`` fails if the name exists); an existing file with identical bytes
    is reported as ``already_present`` instead of an error.
    """
    parent = dest.parent
    _ensure_parents(parent, roots)
    if not _DIR_FD_SUPPORTED:  # pragma: no cover - Windows
        return _write_output_file_portable(dest, data, roots=roots, overwrite=overwrite)
    try:
        dir_fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    except OSError as exc:
        raise OutputPathError("Output directory could not be opened safely.") from exc
    try:
        if not _inside(_fd_directory(dir_fd, parent), roots):
            raise OutputPathError("Output directory escaped the allowed output root.")
        name = dest.name
        expected = hashlib.sha256(data).hexdigest()
        existing = _sha256_of(dir_fd, name)
        if existing == expected:
            return WriteOutcome(path=dest, bytes=len(data), status="already_present")
        if existing is not None and not overwrite:
            raise OutputPathError(
                f"{dest} already exists with different content; pass overwrite=true to replace it."
            )

        tmp_name = f".tmp_{secrets.token_hex(8)}"
        fd = os.open(
            tmp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o644,
            dir_fd=dir_fd,
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            if overwrite:
                os.replace(tmp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
                status: Literal["written", "overwritten"] = (
                    "overwritten" if existing is not None else "written"
                )
            else:
                try:
                    os.link(tmp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
                except FileExistsError as exc:
                    raise OutputPathError(
                        f"{dest} was created concurrently; pass overwrite=true to replace it."
                    ) from exc
                status = "written"
        finally:
            with suppress(FileNotFoundError):
                os.unlink(tmp_name, dir_fd=dir_fd)
    finally:
        os.close(dir_fd)
    return WriteOutcome(path=dest, bytes=len(data), status=status)


def _write_output_file_portable(  # pragma: no cover - Windows
    dest: Path,
    data: bytes,
    *,
    roots: Sequence[Path],
    overwrite: bool,
) -> WriteOutcome:
    """Best-effort atomic write for platforms without ``dir_fd`` support."""
    parent = Path(os.path.realpath(dest.parent))
    if not _inside(parent, roots):
        raise OutputPathError("Output directory escaped the allowed output root.")
    target = parent / dest.name
    expected = hashlib.sha256(data).hexdigest()
    existing: str | None = None
    if target.is_file() and not target.is_symlink():
        existing = hashlib.sha256(target.read_bytes()).hexdigest()
    if existing == expected:
        return WriteOutcome(path=target, bytes=len(data), status="already_present")
    if target.exists() and not overwrite:
        raise OutputPathError(
            f"{target} already exists with different content; pass overwrite=true to replace it."
        )
    tmp = parent / f".tmp_{secrets.token_hex(8)}"
    try:
        with open(tmp, "xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    finally:
        with suppress(FileNotFoundError):
            tmp.unlink()
    return WriteOutcome(
        path=target,
        bytes=len(data),
        status="overwritten" if existing is not None else "written",
    )
