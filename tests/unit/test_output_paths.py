"""Output-root policy and safe local writes."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ark_mcp.security.output_paths import (
    OutputPathError,
    allowed_output_roots,
    resolve_output_path,
    write_output_file,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    path = tmp_path / "root"
    path.mkdir()
    return path.resolve()


def test_relative_path_rejected(root: Path) -> None:
    with pytest.raises(OutputPathError, match="absolute"):
        resolve_output_path("out/file.png", roots=[root], kind="file")


def test_path_outside_root_rejected(root: Path, tmp_path: Path) -> None:
    with pytest.raises(OutputPathError, match="inside an allowed output root"):
        resolve_output_path(str(tmp_path / "other.png"), roots=[root], kind="file")


def test_symlink_escape_rejected(root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside)
    with pytest.raises(OutputPathError, match="inside an allowed output root"):
        resolve_output_path(str(root / "link" / "a.png"), roots=[root], kind="file")


def test_no_roots_disables_writing() -> None:
    with pytest.raises(OutputPathError, match="disabled"):
        resolve_output_path("/tmp/a.png", roots=[], kind="file")


def test_directory_rejected_for_file_kind(root: Path) -> None:
    with pytest.raises(OutputPathError, match="file"):
        resolve_output_path(str(root) + os.sep, roots=[root], kind="file")


def test_write_creates_parents_and_is_exclusive(root: Path) -> None:
    dest = resolve_output_path(str(root / "a" / "b" / "c.bin"), roots=[root], kind="file")
    outcome = write_output_file(dest, b"one", roots=[root], overwrite=False)
    assert outcome.status == "written"
    assert dest.read_bytes() == b"one"

    with pytest.raises(OutputPathError, match="already exists"):
        write_output_file(dest, b"two", roots=[root], overwrite=False)
    assert dest.read_bytes() == b"one"


def test_identical_rewrite_is_already_present(root: Path) -> None:
    dest = root / "same.bin"
    write_output_file(dest, b"same", roots=[root], overwrite=False)
    outcome = write_output_file(dest, b"same", roots=[root], overwrite=False)
    assert outcome.status == "already_present"


def test_overwrite_replaces(root: Path) -> None:
    dest = root / "x.bin"
    write_output_file(dest, b"old", roots=[root], overwrite=False)
    outcome = write_output_file(dest, b"new", roots=[root], overwrite=True)
    assert outcome.status == "overwritten"
    assert dest.read_bytes() == b"new"
    assert not [p for p in root.iterdir() if p.name.startswith(".tmp_")]


def test_parent_swapped_for_symlink_after_validation_is_refused(root: Path, tmp_path: Path) -> None:
    dest = resolve_output_path(str(root / "sub" / "f.bin"), roots=[root], kind="file")
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "sub").symlink_to(outside)  # swapped in after validation
    with pytest.raises(OutputPathError):
        write_output_file(dest, b"data", roots=[root], overwrite=False)
    assert not (outside / "f.bin").exists()


def test_existing_symlink_target_is_not_followed(root: Path, tmp_path: Path) -> None:
    victim = tmp_path / "victim.txt"
    victim.write_text("keep")
    (root / "f.bin").symlink_to(victim)
    with pytest.raises(OutputPathError):
        write_output_file(root / "f.bin", b"data", roots=[root], overwrite=False)
    assert victim.read_text() == "keep"


async def test_mcp_roots_take_precedence(root: Path, tmp_path: Path) -> None:
    env_root = tmp_path / "env"
    env_root.mkdir()

    async def list_roots() -> Any:
        return SimpleNamespace(roots=[SimpleNamespace(uri=root.as_uri())])

    ctx = SimpleNamespace(session=SimpleNamespace(list_roots=list_roots))
    settings = SimpleNamespace(output_root_paths=[str(env_root)])
    assert await allowed_output_roots(ctx, settings) == [root]  # type: ignore[arg-type]


async def test_falls_back_to_output_roots_when_client_has_none(tmp_path: Path) -> None:
    async def list_roots() -> Any:
        raise RuntimeError("roots not supported")

    ctx = SimpleNamespace(session=SimpleNamespace(list_roots=list_roots))
    settings = SimpleNamespace(output_root_paths=[str(tmp_path)])
    assert await allowed_output_roots(ctx, settings) == [tmp_path.resolve()]  # type: ignore[arg-type]
