"""Unit tests for the ``seed_media_export_artifact`` tool handler.

Covers locating the canonical store path, copying to a destination, the
missing-artifact error, the cross-owner permission check, and the stdio
transport gate.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from ark_mcp.artifacts.filesystem_store import FilesystemArtifactStore
from ark_mcp.artifacts.store import ArtifactLocation, StoredArtifact
from ark_mcp.domain.artifacts import ArtifactRef
from ark_mcp.security.auth_context import AuthContext
from ark_mcp.tools.seed_media_export_artifact import (
    SeedMediaExportArtifactInput,
    SeedMediaExportArtifactOutput,
    seed_media_export_artifact,
)
from tests.fixtures.fake_context import FakeContext

_OUTPUT_ROOTS: list[str] = []


@pytest.fixture(autouse=True)
def _output_root(tmp_path: Path) -> None:
    """Allow exports under this test's tmp_path (the OUTPUT_ROOTS policy)."""
    _OUTPUT_ROOTS[:] = [str(tmp_path)]


@pytest.fixture
def store(tmp_path: Path) -> FilesystemArtifactStore:
    return FilesystemArtifactStore(artifact_dir=str(tmp_path), ttl_seconds=3600)


def _ctx_for(
    store: FilesystemArtifactStore,
    principal_id: str,
    tenant_id: str,
    monkeypatch: pytest.MonkeyPatch,
    *,
    transport: str = "stdio",
) -> FakeContext:
    monkeypatch.setattr(
        "ark_mcp.tools.seed_media_export_artifact.get_runtime",
        lambda _ctx: SimpleNamespace(
            artifact_store=store,
            settings=SimpleNamespace(mcp_transport=transport, output_root_paths=_OUTPUT_ROOTS),
        ),
    )
    monkeypatch.setattr(
        "ark_mcp.tools.seed_media_export_artifact.get_principal",
        lambda _ctx: AuthContext(principal_id=principal_id, tenant_id=tenant_id),
    )
    return FakeContext()


class TestSeedMediaExportArtifact:
    async def test_locate_returns_canonical_path_without_copy(
        self,
        store: FilesystemArtifactStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        raw = b"\x00\x01fake audio payload\xff"
        ref = await store.put_base64(
            data=base64.b64encode(raw).decode(),
            media_type="audio",
            mime_type="audio/wav",
            auth=AuthContext(principal_id="alice", tenant_id="tenant-a"),
        )
        ctx = _ctx_for(store, "alice", "tenant-a", monkeypatch)

        result = await seed_media_export_artifact(
            SeedMediaExportArtifactInput(artifact_id=ref.id),
            ctx,
        )

        expected = (tmp_path / ref.id[:2] / f"{ref.id}.wav").resolve()
        assert isinstance(result, SeedMediaExportArtifactOutput)
        assert result.path == str(expected)
        assert result.copied is False
        assert result.media_type == "audio"
        assert result.mime_type == "audio/wav"
        assert result.bytes == len(raw)
        assert result.sha256 == ref.sha256
        assert Path(result.path).read_bytes() == raw

    async def test_copy_to_destination_writes_file(
        self,
        store: FilesystemArtifactStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        raw = b"owned by alice image bytes"
        ref = await store.put_base64(
            data=base64.b64encode(raw).decode(),
            media_type="image",
            mime_type="image/png",
            auth=AuthContext(principal_id="alice", tenant_id="tenant-a"),
        )
        destination = tmp_path / "out" / "copy.png"
        ctx = _ctx_for(store, "alice", "tenant-a", monkeypatch)

        result = await seed_media_export_artifact(
            SeedMediaExportArtifactInput(
                artifact_id=ref.id,
                destination_path=str(destination),
            ),
            ctx,
        )

        assert result.copied is True
        assert result.path == str(destination.resolve())
        assert destination.read_bytes() == raw

    async def test_missing_artifact_raises_file_not_found(
        self,
        store: FilesystemArtifactStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ctx = _ctx_for(store, "alice", "tenant-a", monkeypatch)

        with pytest.raises(FileNotFoundError):
            await seed_media_export_artifact(
                SeedMediaExportArtifactInput(artifact_id=str(uuid.uuid4())),
                ctx,
            )

    async def test_cross_owner_raises_permission_error(
        self,
        store: FilesystemArtifactStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ref = await store.put_base64(
            data=base64.b64encode(b"owned by alice").decode(),
            media_type="video",
            mime_type="video/mp4",
            auth=AuthContext(principal_id="alice", tenant_id="tenant-a"),
        )
        ctx = _ctx_for(store, "bob", "tenant-a", monkeypatch)

        with pytest.raises(PermissionError):
            await seed_media_export_artifact(
                SeedMediaExportArtifactInput(artifact_id=ref.id),
                ctx,
            )

    async def test_non_stdio_transport_raises_value_error(
        self,
        store: FilesystemArtifactStore,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ref = await store.put_base64(
            data=base64.b64encode(b"owned by alice").decode(),
            media_type="image",
            mime_type="image/png",
            auth=AuthContext(principal_id="alice", tenant_id="tenant-a"),
        )
        ctx = _ctx_for(store, "alice", "tenant-a", monkeypatch, transport="http")

        with pytest.raises(ValueError):
            await seed_media_export_artifact(
                SeedMediaExportArtifactInput(artifact_id=ref.id),
                ctx,
            )

    async def test_directory_destination_raises_value_error(
        self,
        store: FilesystemArtifactStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ref = await store.put_base64(
            data=base64.b64encode(b"owned by alice").decode(),
            media_type="image",
            mime_type="image/png",
            auth=AuthContext(principal_id="alice", tenant_id="tenant-a"),
        )
        ctx = _ctx_for(store, "alice", "tenant-a", monkeypatch)

        with pytest.raises(ValueError):
            await seed_media_export_artifact(
                SeedMediaExportArtifactInput(
                    artifact_id=ref.id,
                    destination_path=str(tmp_path),
                ),
                ctx,
            )


class _FakeObjectStorageStore:
    def __init__(self, ref: ArtifactRef, data: bytes) -> None:
        self._ref = ref
        self._data = data

    async def locate(self, artifact_id: str, auth: AuthContext | None = None) -> ArtifactLocation:
        return ArtifactLocation(path=None, ref=self._ref)

    async def get(self, artifact_id: str, auth: AuthContext | None = None) -> StoredArtifact:
        return StoredArtifact(
            data=self._data,
            media_type=self._ref.media_type,
            mime_type=self._ref.mime_type,
            artifact_id=artifact_id,
        )


def _object_storage_ref(raw: bytes) -> ArtifactRef:
    artifact_id = str(uuid.uuid4())
    return ArtifactRef(
        id=artifact_id,
        uri=f"seed-media://artifacts/{artifact_id}",
        media_type="video",
        mime_type="video/mp4",
        bytes=len(raw),
        sha256=hashlib.sha256(raw).hexdigest(),
        created_at="2026-09-17T00:00:00+00:00",
    )


def _ctx_for_object_storage(
    store: _FakeObjectStorageStore,
    monkeypatch: pytest.MonkeyPatch,
) -> FakeContext:
    monkeypatch.setattr(
        "ark_mcp.tools.seed_media_export_artifact.get_runtime",
        lambda _ctx: SimpleNamespace(
            artifact_store=store,
            settings=SimpleNamespace(mcp_transport="stdio", output_root_paths=_OUTPUT_ROOTS),
        ),
    )
    monkeypatch.setattr(
        "ark_mcp.tools.seed_media_export_artifact.get_principal",
        lambda _ctx: AuthContext(principal_id="alice", tenant_id="tenant-a"),
    )
    return FakeContext()


class TestSeedMediaExportArtifactObjectStorage:
    async def test_locate_without_destination_raises_value_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        raw = b"object-storage media bytes"
        ref = _object_storage_ref(raw)
        store = _FakeObjectStorageStore(ref, raw)
        ctx = _ctx_for_object_storage(store, monkeypatch)

        with pytest.raises(ValueError):
            await seed_media_export_artifact(
                SeedMediaExportArtifactInput(artifact_id=ref.id),
                ctx,
            )

    async def test_copy_to_destination_writes_bytes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        raw = b"object-storage media bytes"
        ref = _object_storage_ref(raw)
        store = _FakeObjectStorageStore(ref, raw)
        destination = tmp_path / "out" / "copy.mp4"
        ctx = _ctx_for_object_storage(store, monkeypatch)

        result = await seed_media_export_artifact(
            SeedMediaExportArtifactInput(
                artifact_id=ref.id,
                destination_path=str(destination),
            ),
            ctx,
        )

        assert result.copied is True
        assert result.path == str(destination.resolve())
        assert destination.read_bytes() == raw


class TestExportOutputRootPolicy:
    async def test_destination_outside_roots_rejected(
        self,
        store: FilesystemArtifactStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ref = await store.put_base64(
            data=base64.b64encode(b"x").decode(),
            media_type="image",
            mime_type="image/png",
            auth=AuthContext(principal_id="alice", tenant_id="tenant-a"),
        )
        _OUTPUT_ROOTS[:] = [str(tmp_path / "allowed")]
        ctx = _ctx_for(store, "alice", "tenant-a", monkeypatch)
        with pytest.raises(ValueError, match="inside an allowed output root"):
            await seed_media_export_artifact(
                SeedMediaExportArtifactInput(
                    artifact_id=ref.id, destination_path=str(tmp_path / "elsewhere.png")
                ),
                ctx,
            )

    async def test_relative_destination_rejected(
        self, store: FilesystemArtifactStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ref = await store.put_base64(
            data=base64.b64encode(b"x").decode(),
            media_type="image",
            mime_type="image/png",
            auth=AuthContext(principal_id="alice", tenant_id="tenant-a"),
        )
        ctx = _ctx_for(store, "alice", "tenant-a", monkeypatch)
        with pytest.raises(ValueError, match="absolute"):
            await seed_media_export_artifact(
                SeedMediaExportArtifactInput(artifact_id=ref.id, destination_path="copy.png"),
                ctx,
            )

    async def test_existing_different_file_needs_overwrite_but_same_is_ok(
        self,
        store: FilesystemArtifactStore,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ref = await store.put_base64(
            data=base64.b64encode(b"new-bytes").decode(),
            media_type="image",
            mime_type="image/png",
            auth=AuthContext(principal_id="alice", tenant_id="tenant-a"),
        )
        destination = tmp_path / "copy.png"
        destination.write_bytes(b"old-bytes")
        ctx = _ctx_for(store, "alice", "tenant-a", monkeypatch)
        with pytest.raises(ValueError, match="overwrite"):
            await seed_media_export_artifact(
                SeedMediaExportArtifactInput(artifact_id=ref.id, destination_path=str(destination)),
                ctx,
            )
        result = await seed_media_export_artifact(
            SeedMediaExportArtifactInput(
                artifact_id=ref.id, destination_path=str(destination), overwrite=True
            ),
            ctx,
        )
        assert destination.read_bytes() == b"new-bytes"
        again = await seed_media_export_artifact(
            SeedMediaExportArtifactInput(artifact_id=ref.id, destination_path=str(destination)),
            ctx,
        )
        assert result.copied and again.copied
