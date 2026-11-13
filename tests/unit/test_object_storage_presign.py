"""Unit tests for presigning stored artifacts in the object-storage artifact store."""

from __future__ import annotations

import base64
from dataclasses import dataclass

import pytest

from ark_mcp.artifacts import object_storage_store
from ark_mcp.artifacts.object_storage_store import ObjectStorageArtifactStore
from ark_mcp.artifacts.store import PresignableArtifactStore
from ark_mcp.domain.artifacts import MediaType
from ark_mcp.security.auth_context import AuthContext
from ark_mcp.security.safe_downloader import SafeDownloadError

PNG_BASE64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 16).decode()


@dataclass
class _Downloaded:
    body: bytes
    content_type: str | None = None


class FakeGateway:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.presigned: list[tuple[str, int | None]] = []

    @property
    def data_presigns(self) -> list[tuple[str, int | None]]:
        return [call for call in self.presigned if not call[0].endswith(".meta.json")]

    async def upload_bytes(self, *, key: str, data: bytes, mime_type: str) -> None:
        self.objects[key] = data

    async def presign_get(self, *, key: str, expires: int | None = None) -> str:
        self.presigned.append((key, expires))
        return f"https://store.example.com/{key}?sig=abc"

    async def close(self) -> None: ...


class FakeDownloader:
    def __init__(self, gateway: FakeGateway) -> None:
        self._gateway = gateway
        self.trusted_hosts: object = None

    async def download(self, url: str, **kwargs: object) -> _Downloaded:
        if url.startswith("https://out.example.com/"):
            self.trusted_hosts = kwargs["trusted_hosts"]
            return _Downloaded(b"video-bytes", "video/mp4")
        key = url.split("store.example.com/", 1)[1].split("?", 1)[0]
        if key not in self._gateway.objects:
            raise SafeDownloadError("download_failed", "missing", retryable=False)
        return _Downloaded(self._gateway.objects[key])

    async def close(self) -> None: ...


@pytest.fixture
def gateway() -> FakeGateway:
    return FakeGateway()


@pytest.fixture
def downloader(gateway: FakeGateway) -> FakeDownloader:
    return FakeDownloader(gateway)


@pytest.fixture
def store(
    gateway: FakeGateway, downloader: FakeDownloader, monkeypatch: pytest.MonkeyPatch
) -> ObjectStorageArtifactStore:
    monkeypatch.setattr(object_storage_store, "make_object_storage_gateway", lambda _s: gateway)
    return ObjectStorageArtifactStore(downloader=downloader)  # type: ignore[arg-type]


OWNER = AuthContext(principal_id="alice", tenant_id="t1")
OTHER = AuthContext(principal_id="mallory", tenant_id="t1")


class TestPresign:
    def test_object_store_is_presignable(self, store: ObjectStorageArtifactStore) -> None:
        assert isinstance(store, PresignableArtifactStore)

    async def test_returns_presigned_url_for_data_object_and_ref(
        self, store: ObjectStorageArtifactStore, gateway: FakeGateway
    ) -> None:
        ref = await store.put_base64(PNG_BASE64, MediaType.IMAGE, "image/png", auth=OWNER)
        gateway.presigned.clear()

        presigned = await store.presign(ref.id, expires_in_seconds=7200, auth=OWNER)

        expected_key = f"artifacts/{ref.id[:2]}/{ref.id}.png"
        assert gateway.data_presigns == [(expected_key, 7200)]
        assert presigned.url == f"https://store.example.com/{expected_key}?sig=abc"
        assert presigned.ref.id == ref.id
        assert presigned.ref.mime_type == "image/png"

    async def test_other_principal_is_rejected_without_presigning(
        self, store: ObjectStorageArtifactStore, gateway: FakeGateway
    ) -> None:
        ref = await store.put_base64(PNG_BASE64, MediaType.IMAGE, "image/png", auth=OWNER)
        gateway.presigned.clear()

        with pytest.raises(PermissionError):
            await store.presign(ref.id, expires_in_seconds=3600, auth=OTHER)

        assert gateway.data_presigns == []

    async def test_same_principal_id_in_another_tenant_is_rejected(
        self, store: ObjectStorageArtifactStore, gateway: FakeGateway
    ) -> None:
        ref = await store.put_base64(PNG_BASE64, MediaType.IMAGE, "image/png", auth=OWNER)
        gateway.presigned.clear()

        with pytest.raises(PermissionError):
            await store.presign(
                ref.id,
                expires_in_seconds=3600,
                auth=AuthContext(principal_id="alice", tenant_id="t2"),
            )

        assert gateway.data_presigns == []

    async def test_unknown_artifact_raises_not_found(
        self, store: ObjectStorageArtifactStore
    ) -> None:
        with pytest.raises(FileNotFoundError):
            await store.presign(
                "11111111-1111-4111-8111-111111111111", expires_in_seconds=3600, auth=OWNER
            )

    async def test_malformed_artifact_id_raises_value_error(
        self, store: ObjectStorageArtifactStore
    ) -> None:
        with pytest.raises(ValueError, match="Invalid artifact ID"):
            await store.presign("../etc/passwd", expires_in_seconds=3600, auth=OWNER)


class TestScopedTrustedHosts:
    async def test_extra_suffixes_apply_to_one_call_only(
        self, store: ObjectStorageArtifactStore, downloader: FakeDownloader
    ) -> None:
        await store.copy_from_trusted_url(
            "https://out.example.com/v.mp4",
            MediaType.VIDEO,
            "video/mp4",
            extra_trusted_host_suffixes=(".tiktokcdn.com",),
            auth=OWNER,
        )
        predicate = downloader.trusted_hosts
        assert callable(predicate)
        assert predicate("v16m-default.tiktokcdn.com") is True
        assert predicate("tiktokcdn.com.attacker.example") is False

        await store.copy_from_trusted_url(
            "https://out.example.com/v.mp4", MediaType.VIDEO, "video/mp4", auth=OWNER
        )
        assert downloader.trusted_hosts("v16m-default.tiktokcdn.com") is False  # type: ignore[operator]
