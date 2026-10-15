"""Unit tests for filesystem artifact store."""

from __future__ import annotations

import base64
import uuid
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ark_mcp.artifacts.filesystem_store import FilesystemArtifactStore, _is_trusted_host
from ark_mcp.artifacts.store import ArtifactPersistenceError
from ark_mcp.security.auth_context import AuthContext
from ark_mcp.security.safe_downloader import (
    DownloadedMedia,
    HostPolicy,
    SafeDownloadError,
)

if TYPE_CHECKING:
    from pathlib import Path


class StubDownloader:
    def __init__(
        self,
        result: DownloadedMedia | None = None,
        error: SafeDownloadError | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.trusted_hosts: HostPolicy | None = None

    async def download(
        self,
        _url: str,
        *,
        trusted_hosts: HostPolicy,
        max_bytes: int,
        max_redirects: int = 5,
    ) -> DownloadedMedia:
        del max_bytes, max_redirects
        self.trusted_hosts = trusted_hosts
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise AssertionError("StubDownloader requires a result or error")
        return self.result

    async def close(self) -> None:
        return None


@pytest.fixture
def store(tmp_path: Path) -> FilesystemArtifactStore:
    """Create a filesystem artifact store in a temp directory."""
    return FilesystemArtifactStore(artifact_dir=str(tmp_path), ttl_seconds=3600)


class TestFilesystemArtifactStore:
    """Tests for the filesystem artifact store."""

    async def test_put_base64_and_get(self, store: FilesystemArtifactStore) -> None:
        raw = b"fake audio data"
        data = base64.b64encode(raw).decode()
        ref = await store.put_base64(data=data, media_type="audio", mime_type="audio/wav")
        assert ref.id
        assert ref.uri == f"seed-media://artifacts/{ref.id}"
        assert ref.media_type == "audio"
        assert ref.mime_type == "audio/wav"
        assert ref.bytes == len(raw)
        assert ref.sha256

        artifact = await store.get(ref.id)
        assert artifact.data == raw
        assert artifact.media_type == "audio"
        assert artifact.mime_type == "audio/wav"

    async def test_put_base64_computes_sha256(self, store: FilesystemArtifactStore) -> None:
        import hashlib

        raw = b"test data for sha256"
        data = base64.b64encode(raw).decode()
        ref = await store.put_base64(data=data, media_type="image", mime_type="image/png")
        expected = hashlib.sha256(raw).hexdigest()
        assert ref.sha256 == expected

    async def test_get_nonexistent_raises(self, store: FilesystemArtifactStore) -> None:
        # Use a valid UUID format that doesn't exist
        fake_id = str(uuid.uuid4())
        with pytest.raises(FileNotFoundError):
            await store.get(fake_id)

    @pytest.mark.parametrize(
        "artifact_id",
        ["../escape", "-" * 36, str(uuid.uuid1()), str(uuid.uuid4()).upper()],
    )
    async def test_get_rejects_noncanonical_uuid4(
        self,
        store: FilesystemArtifactStore,
        artifact_id: str,
    ) -> None:
        with pytest.raises(ValueError, match="Invalid artifact ID"):
            await store.get(artifact_id)

    async def test_storage_revalidates_mime(self, store: FilesystemArtifactStore) -> None:
        data = base64.b64encode(b"not-an-image").decode()
        with pytest.raises(ValueError, match="Image MIME type"):
            await store.put_base64(
                data=data,
                media_type="image",
                mime_type="text/html",
            )

    async def test_get_rejects_cross_principal_access(self, store: FilesystemArtifactStore) -> None:
        owner = AuthContext(principal_id="alice", tenant_id="tenant-a")
        ref = await store.put_base64(
            data=base64.b64encode(b"owned-image").decode(),
            media_type="image",
            mime_type="image/png",
            auth=owner,
        )

        with pytest.raises(PermissionError, match="not owned"):
            await store.get(
                ref.id,
                auth=AuthContext(principal_id="bob", tenant_id="tenant-a"),
            )

    async def test_get_rejects_cross_tenant_access(self, store: FilesystemArtifactStore) -> None:
        owner = AuthContext(principal_id="alice", tenant_id="tenant-a")
        ref = await store.put_base64(
            data=base64.b64encode(b"owned-image").decode(),
            media_type="image",
            mime_type="image/png",
            auth=owner,
        )

        with pytest.raises(PermissionError, match="not owned"):
            await store.get(
                ref.id,
                auth=AuthContext(principal_id="alice", tenant_id="tenant-b"),
            )

    async def test_legacy_metadata_is_local_only(self, store: FilesystemArtifactStore) -> None:
        ref = await store.put_base64(
            data=base64.b64encode(b"legacy-image").decode(),
            media_type="image",
            mime_type="image/png",
        )
        metadata_path = store._metadata_path(ref.id)
        metadata_path.write_text(ref.model_dump_json())

        local_artifact = await store.get(ref.id, auth=AuthContext())
        assert local_artifact.data == b"legacy-image"

        with pytest.raises(PermissionError, match="not owned"):
            await store.get(
                ref.id,
                auth=AuthContext(principal_id="remote", tenant_id="tenant-a"),
            )

    async def test_delete_expired_returns_zero(self, store: FilesystemArtifactStore) -> None:
        from datetime import UTC, datetime

        count = await store.delete_expired(now=datetime.now(UTC))
        assert count == 0

    async def test_delete_expired_removes_artifacts(self, store: FilesystemArtifactStore) -> None:
        from datetime import UTC, datetime, timedelta

        raw = b"expired data"
        data = base64.b64encode(raw).decode()
        ref = await store.put_base64(data=data, media_type="video", mime_type="video/mp4")

        # Verify it exists.
        artifact = await store.get(ref.id)
        assert artifact.data == raw

        # Delete expired with a future timestamp to trigger deletion.
        future = datetime.now(UTC) + timedelta(days=1)
        count = await store.delete_expired(now=future)
        assert count == 1

        # Verify it's gone.
        with pytest.raises(FileNotFoundError):
            await store.get(ref.id)


@pytest.mark.parametrize(
    ("hostname", "expected"),
    [
        ("media.bytepluses.com", True),
        ("MEDIA.BYTEPLUSES.COM", True),
        ("bytepluses.com", False),
        ("bytepluses.com.attacker.example", False),
        ("notbytepluses.com", False),
        ("3002771874-amk-3000006864-default-934334.vod.ap-southeast-1.byteplusvod.com", True),
        ("evil.byteplusvod.com.attacker.example", False),
        ("v16m-default.tiktokcdn.com", True),
        ("tiktokcdn.com", False),
        ("tiktokcdn.com.attacker.example", False),
        ("nottiktokcdn.com", False),
    ],
)
def test_trusted_host_policy_rejects_suffix_confusion(hostname: str, expected: bool) -> None:
    assert _is_trusted_host(hostname) is expected


async def test_copy_from_trusted_url_stores_valid_video(tmp_path: Path) -> None:
    downloader = StubDownloader(
        result=DownloadedMedia(
            body=b"video-bytes",
            content_type="video/mp4",
            final_url="https://media.bytepluses.com/output.mp4",
        )
    )
    store = FilesystemArtifactStore(
        artifact_dir=str(tmp_path),
        ttl_seconds=3600,
        downloader=downloader,  # type: ignore[arg-type]
    )
    try:
        ref = await store.copy_from_trusted_url(
            "https://media.bytepluses.com/output.mp4?signature=secret",
            media_type="video",
            mime_type="video/mp4",
        )
    finally:
        await store.close()

    assert ref.media_type == "video"
    assert ref.bytes == len(b"video-bytes")
    assert downloader.trusted_hosts is not None
    assert downloader.trusted_hosts("media.bytepluses.com") is True


@pytest.mark.parametrize(
    ("download_code", "artifact_code", "retryable"),
    [
        ("untrusted_host", "untrusted_output_host", False),
        ("redirect_rejected", "untrusted_output_host", False),
        ("too_large", "output_too_large", False),
        ("source_expired", "source_expired", False),
        ("network_error", "download_failed", True),
        ("http_error", "download_failed", True),
        ("invalid_url", "download_failed", False),
    ],
)
async def test_copy_translates_safe_download_errors(
    tmp_path: Path,
    download_code: str,
    artifact_code: str,
    retryable: bool,
) -> None:
    downloader = StubDownloader(
        error=SafeDownloadError(  # type: ignore[arg-type]
            download_code,
            "Safe failure without URL details.",
            retryable=retryable,
        )
    )
    store = FilesystemArtifactStore(
        artifact_dir=str(tmp_path),
        downloader=downloader,  # type: ignore[arg-type]
    )
    try:
        with pytest.raises(ArtifactPersistenceError) as exc_info:
            await store.copy_from_trusted_url(
                "https://media.bytepluses.com/output.mp4?signature=must-not-leak",
                media_type="video",
                mime_type="video/mp4",
            )
    finally:
        await store.close()

    assert exc_info.value.code == artifact_code
    assert exc_info.value.retryable is retryable
    assert "signature=" not in str(exc_info.value)


async def test_copy_translates_invalid_mime(tmp_path: Path) -> None:
    downloader = StubDownloader(
        result=DownloadedMedia(
            body=b"html-not-video",
            content_type="text/html",
            final_url="https://media.bytepluses.com/output.mp4",
        )
    )
    store = FilesystemArtifactStore(
        artifact_dir=str(tmp_path),
        downloader=downloader,  # type: ignore[arg-type]
    )
    try:
        with pytest.raises(ArtifactPersistenceError) as exc_info:
            await store.copy_from_trusted_url(
                "https://media.bytepluses.com/output.mp4",
                media_type="video",
                mime_type="video/mp4",
            )
    finally:
        await store.close()

    assert exc_info.value.code == "invalid_output_mime"
    assert exc_info.value.retryable is False


async def test_copy_preserves_artifact_size_ceiling(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloader = StubDownloader(
        result=DownloadedMedia(
            body=b"1234567",
            content_type="video/mp4",
            final_url="https://media.bytepluses.com/output.mp4",
        )
    )
    store = FilesystemArtifactStore(
        artifact_dir=str(tmp_path),
        downloader=downloader,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        "ark_mcp.artifacts.filesystem_store.get_media_limits",
        lambda: SimpleNamespace(
            image_max_bytes=4,
            audio_max_bytes=4,
            video_max_bytes=6,
            three_d_max_bytes=6,
        ),
    )
    try:
        with pytest.raises(ArtifactPersistenceError) as exc_info:
            await store.copy_from_trusted_url(
                "https://media.bytepluses.com/output.mp4",
                media_type="video",
                mime_type="video/mp4",
            )
    finally:
        await store.close()

    assert exc_info.value.code == "output_too_large"
    assert exc_info.value.retryable is False


async def test_copy_translates_storage_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloader = StubDownloader(
        result=DownloadedMedia(
            body=b"video-bytes",
            content_type="video/mp4",
            final_url="https://media.bytepluses.com/output.mp4",
        )
    )
    store = FilesystemArtifactStore(
        artifact_dir=str(tmp_path),
        downloader=downloader,  # type: ignore[arg-type]
    )

    def fail_write(_path: Path, _data: bytes) -> None:
        raise OSError("disk failure at /private/sensitive/path")

    monkeypatch.setattr(store, "_atomic_write", fail_write)
    try:
        with pytest.raises(ArtifactPersistenceError) as exc_info:
            await store.copy_from_trusted_url(
                "https://media.bytepluses.com/output.mp4?signature=must-not-leak",
                media_type="video",
                mime_type="video/mp4",
            )
    finally:
        await store.close()

    assert exc_info.value.code == "storage_failed"
    assert exc_info.value.retryable is True
    assert "sensitive" not in str(exc_info.value)
    assert "signature=" not in str(exc_info.value)
