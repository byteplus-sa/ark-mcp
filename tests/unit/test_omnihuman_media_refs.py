"""Unit tests for OmniHuman image/audio reference resolution."""

from __future__ import annotations

import pytest

from ark_mcp.artifacts.store import PresignedArtifact
from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.security.auth_context import AuthContext
from ark_mcp.tools._omnihuman_shared import (
    AUDIO_MAX_BYTES,
    IMAGE_MAX_BYTES,
    MediaRefError,
    resolve_media_ref,
)

OWNER = AuthContext(principal_id="alice", tenant_id="t1")
ARTIFACT_ID = "11111111-1111-4111-8111-111111111111"
PUBLIC_DNS = lambda _host, _port: ("93.184.216.34",)  # noqa: E731


def artifact(
    *,
    media_type: MediaType = MediaType.IMAGE,
    mime_type: str = "image/png",
    size: int | None = 1024,
) -> ArtifactRef:
    return ArtifactRef(
        id=ARTIFACT_ID,
        uri=f"seed-media://artifacts/{ARTIFACT_ID}",
        media_type=media_type,
        mime_type=mime_type,
        bytes=size,
        created_at="2026-10-15T00:00:00+00:00",
    )


class FakeStore:
    def __init__(self, ref: ArtifactRef | None = None, error: Exception | None = None) -> None:
        self._ref = ref or artifact()
        self._error = error
        self.calls: list[tuple[str, int, AuthContext]] = []

    async def presign(
        self, artifact_id: str, *, expires_in_seconds: int, auth: AuthContext
    ) -> PresignedArtifact:
        self.calls.append((artifact_id, expires_in_seconds, auth))
        if self._error is not None:
            raise self._error
        return PresignedArtifact(url="https://store.example.com/k?sig=1", ref=self._ref)


class NonPresignableStore:
    """Mimics the filesystem store, which has no URL a provider could fetch."""


async def resolve(ref: str, kind: str, store: object | None = None) -> str:
    return await resolve_media_ref(
        ref,
        kind,  # type: ignore[arg-type]
        store=store or FakeStore(),  # type: ignore[arg-type]
        owner=OWNER,
        address_resolver=PUBLIC_DNS,
    )


class TestHttpsUrls:
    async def test_public_https_url_passes_through(self) -> None:
        url = "https://media.example.com/face.png?X-Amz-Signature=abc"
        assert await resolve(url, "image") == url

    async def test_surrounding_whitespace_is_trimmed(self) -> None:
        assert await resolve("  https://media.example.com/a.mp3 \n", "audio") == (
            "https://media.example.com/a.mp3"
        )

    @pytest.mark.parametrize(
        "ref",
        [
            "http://media.example.com/face.png",
            "file:///etc/passwd",
            "ftp://media.example.com/a.mp3",
            "https://10.0.0.1/face.png",
            "https://127.0.0.1/face.png",
            "https://user:pw@media.example.com/face.png",  # pragma: allowlist secret
            "/tmp/face.png",
            "",
            "   ",
        ],
    )
    async def test_unsafe_or_unsupported_references_are_rejected(self, ref: str) -> None:
        with pytest.raises(MediaRefError):
            await resolve(ref, "image")

    async def test_rejection_message_does_not_echo_the_url(self) -> None:
        with pytest.raises(MediaRefError) as exc_info:
            await resolve("https://10.0.0.1/secret-path.png", "image")
        assert "secret-path" not in str(exc_info.value)


class TestArtifacts:
    async def test_image_artifact_is_presigned_for_the_owner(self) -> None:
        store = FakeStore()

        url = await resolve(f"seed-media://artifacts/{ARTIFACT_ID}", "image", store)

        assert url == "https://store.example.com/k?sig=1"
        artifact_id, expires, auth = store.calls[0]
        assert artifact_id == ARTIFACT_ID
        assert expires >= 3600
        assert auth == OWNER

    async def test_audio_artifact_is_accepted(self) -> None:
        store = FakeStore(artifact(media_type=MediaType.AUDIO, mime_type="audio/mpeg"))
        assert await resolve(f"seed-media://artifacts/{ARTIFACT_ID}", "audio", store)

    @pytest.mark.parametrize("mime_type", ["audio/wav", "audio/x-wav", "audio/mp3"])
    async def test_supported_audio_mimes(self, mime_type: str) -> None:
        store = FakeStore(artifact(media_type=MediaType.AUDIO, mime_type=mime_type))
        assert await resolve(f"seed-media://artifacts/{ARTIFACT_ID}", "audio", store)

    async def test_wrong_media_type_is_rejected(self) -> None:
        store = FakeStore(artifact(media_type=MediaType.AUDIO, mime_type="audio/mpeg"))
        with pytest.raises(MediaRefError, match="image"):
            await resolve(f"seed-media://artifacts/{ARTIFACT_ID}", "image", store)

    async def test_unsupported_audio_format_is_rejected(self) -> None:
        store = FakeStore(artifact(media_type=MediaType.AUDIO, mime_type="audio/flac"))
        with pytest.raises(MediaRefError, match="wav or mp3"):
            await resolve(f"seed-media://artifacts/{ARTIFACT_ID}", "audio", store)

    async def test_unsupported_image_format_is_rejected(self) -> None:
        store = FakeStore(artifact(mime_type="image/svg+xml"))
        with pytest.raises(MediaRefError, match="image format"):
            await resolve(f"seed-media://artifacts/{ARTIFACT_ID}", "image", store)

    async def test_oversized_image_is_rejected(self) -> None:
        store = FakeStore(artifact(size=IMAGE_MAX_BYTES))
        with pytest.raises(MediaRefError, match="larger than"):
            await resolve(f"seed-media://artifacts/{ARTIFACT_ID}", "image", store)

    async def test_image_just_under_limit_is_accepted(self) -> None:
        store = FakeStore(artifact(size=IMAGE_MAX_BYTES - 1))
        assert await resolve(f"seed-media://artifacts/{ARTIFACT_ID}", "image", store)

    async def test_oversized_audio_is_rejected(self) -> None:
        store = FakeStore(
            artifact(media_type=MediaType.AUDIO, mime_type="audio/wav", size=AUDIO_MAX_BYTES)
        )
        with pytest.raises(MediaRefError, match="larger than"):
            await resolve(f"seed-media://artifacts/{ARTIFACT_ID}", "audio", store)

    async def test_unknown_size_is_not_rejected(self) -> None:
        store = FakeStore(artifact(size=None))
        assert await resolve(f"seed-media://artifacts/{ARTIFACT_ID}", "image", store)

    @pytest.mark.parametrize("error", [FileNotFoundError("x"), PermissionError("x")])
    async def test_missing_or_foreign_artifact_gives_one_indistinguishable_error(
        self, error: Exception
    ) -> None:
        with pytest.raises(MediaRefError, match="not found"):
            await resolve(f"seed-media://artifacts/{ARTIFACT_ID}", "image", FakeStore(error=error))

    @pytest.mark.parametrize(
        "ref",
        ["seed-media://artifacts/", "seed-media://artifacts/../x", "seed-media://other/abc"],
    )
    async def test_malformed_artifact_uri_is_rejected(self, ref: str) -> None:
        with pytest.raises(MediaRefError):
            await resolve(ref, "image", FakeStore(error=ValueError("Invalid artifact ID format")))

    async def test_store_without_presign_gives_actionable_error(self) -> None:
        with pytest.raises(MediaRefError, match="media_upload"):
            await resolve(f"seed-media://artifacts/{ARTIFACT_ID}", "image", NonPresignableStore())
