"""Filesystem-backed artifact store.

Implements atomic temp-file rename, SHA-256, MIME sniffing, ownership
metadata, and TTL cleanup. Used for local ``stdio`` deployment.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ark_mcp.artifacts.store import (
    ArtifactLocation,
    ArtifactMetadata,
    ArtifactPersistenceError,
    ArtifactPersistenceErrorCode,
    ArtifactStore,
    StoredArtifact,
)
from ark_mcp.config.env import get_settings
from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.observability.logger import info as log_info
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.observability.metrics import ARTIFACT_OPERATIONS
from ark_mcp.security.auth_context import AuthContext
from ark_mcp.security.media_policy import (
    decode_base64_safely,
    get_media_limits,
    validate_3d_mime,
    validate_audio_mime,
    validate_image_mime,
    validate_video_mime,
)
from ark_mcp.security.safe_downloader import (
    SafeDownloader,
    SafeDownloadError,
    SafeDownloadErrorCode,
)

# Host allowlist for downloading provider output URLs.
_TRUSTED_EXACT_HOSTS: frozenset[str] = frozenset()
_TRUSTED_HOST_SUFFIXES: tuple[str, ...] = (
    ".bytepluses.com",
    ".byteplus.com",
    ".byteplusvod.com",
    ".bytedance.com",
    ".bytednsdoc.com",
    ".volces.com",
    ".tos-ap-southeast.bytepluses.com",
)


def _is_trusted_host(hostname: str) -> bool:
    """Check if a hostname is in the trusted provider/TOS allowlist."""
    hostname_lower = hostname.lower()
    return hostname_lower in _TRUSTED_EXACT_HOSTS or any(
        hostname_lower.endswith(suffix) for suffix in _TRUSTED_HOST_SUFFIXES
    )


# Generic binary content-types that carry no useful file-type signal. When a
# provider returns one of these for a known file extension (e.g. .glb), keep
# the caller-supplied MIME type instead of adopting the generic header.
_GENERIC_OCTET_STREAM: frozenset[str] = frozenset(
    {
        "application/octet-stream",
        "binary/octet-stream",
    }
)


def _translate_download_error(exc: SafeDownloadError) -> ArtifactPersistenceError:
    code_map: dict[SafeDownloadErrorCode, ArtifactPersistenceErrorCode] = {
        "untrusted_host": "untrusted_output_host",
        "redirect_rejected": "untrusted_output_host",
        "too_large": "output_too_large",
        "source_expired": "source_expired",
        "invalid_url": "download_failed",
        "http_error": "download_failed",
        "network_error": "download_failed",
    }
    return ArtifactPersistenceError(
        code_map[exc.code],
        exc.safe_message,
        retryable=exc.retryable,
    )


def _mime_to_media_type(mime_type: str) -> MediaType:
    """Infer the logical media type from a MIME type."""
    if mime_type.startswith("image/"):
        return MediaType.IMAGE
    if mime_type.startswith("audio/"):
        return MediaType.AUDIO
    if mime_type.startswith("video/"):
        return MediaType.VIDEO
    if mime_type.startswith("model/") or mime_type in {
        "application/zip",
        "application/x-zip-compressed",
    }:
        return MediaType.THREE_D
    return MediaType.IMAGE  # Conservative default


_MIME_TO_EXT: dict[str, str] = {
    "audio/wav": ".wav",
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/aac": ".aac",
    "audio/mp4": ".m4a",
    "audio/flac": ".flac",
    "audio/ogg": ".ogg",
    "audio/pcm": ".pcm",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "model/gltf-binary": ".glb",
    "model/gltf+json": ".gltf",
    "model/vnd.usdz+zip": ".usdz",
}


def _mime_to_ext(mime_type: str) -> str:
    return _MIME_TO_EXT.get(mime_type, "")


class FilesystemArtifactStore(ArtifactStore):
    """Filesystem-backed artifact store for local ``stdio`` deployment."""

    def __init__(
        self,
        *,
        artifact_dir: str | None = None,
        ttl_seconds: int | None = None,
        downloader: SafeDownloader | None = None,
    ) -> None:
        settings = get_settings()
        self._base_dir = Path(artifact_dir or settings.artifact_dir).expanduser().resolve()
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._ttl_seconds = ttl_seconds or settings.artifact_ttl_seconds
        self._downloader = downloader or SafeDownloader(
            timeout=settings.artifact_download_timeout_seconds,
            connect_timeout=settings.connect_timeout_ms / 1000,
            max_attempts=settings.artifact_download_max_attempts,
        )

    def _validate_artifact_id(self, artifact_id: str) -> None:
        try:
            parsed = uuid.UUID(artifact_id, version=4)
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"Invalid artifact ID format: '{artifact_id}'") from exc
        if str(parsed) != artifact_id:
            raise ValueError(f"Invalid artifact ID format: '{artifact_id}'")

    def _artifact_path(self, artifact_id: str, ext: str = "") -> Path:
        self._validate_artifact_id(artifact_id)
        return self._safe_path(artifact_id[:2], f"{artifact_id}{ext}")

    def _metadata_path(self, artifact_id: str) -> Path:
        self._validate_artifact_id(artifact_id)
        return self._safe_path(artifact_id[:2], f"{artifact_id}.meta.json")

    def _safe_path(self, *parts: str) -> Path:
        path = self._base_dir.joinpath(*parts).resolve()
        try:
            path.relative_to(self._base_dir)
        except ValueError as exc:
            raise ValueError("Artifact path escapes the configured artifact directory.") from exc
        return path

    async def put_base64(
        self,
        data: str,
        media_type: MediaType,
        mime_type: str,
        source_expires_at: str | None = None,
        auth: AuthContext | None = None,
    ) -> ArtifactRef:
        """Store Base64-encoded media and return a durable artifact reference."""
        limits = get_media_limits()
        max_bytes = {
            "image": limits.image_max_bytes,
            "audio": limits.audio_max_bytes,
            "video": limits.video_max_bytes,
            "three_d": limits.three_d_max_bytes,
        }[media_type]
        raw = decode_base64_safely(data, max_bytes, label=media_type)
        return await self._store_bytes(raw, media_type, mime_type, source_expires_at, auth)

    async def copy_from_trusted_url(
        self,
        url: str,
        media_type: MediaType,
        mime_type: str,
        source_expires_at: str | None = None,
        auth: AuthContext | None = None,
    ) -> ArtifactRef:
        """Download media from a trusted provider URL and store it.

        Only downloads from configured BytePlus/TOS host allowlists.
        Validates URL, MIME, and size before storage.
        """
        limits = get_media_limits()
        max_bytes = {
            "image": limits.image_max_bytes,
            "audio": limits.audio_max_bytes,
            "video": limits.video_max_bytes,
            "three_d": limits.three_d_max_bytes,
        }[media_type]
        try:
            downloaded = await self._downloader.download(
                url,
                trusted_hosts=_is_trusted_host,
                max_bytes=max_bytes,
            )
        except SafeDownloadError as exc:
            raise _translate_download_error(exc) from exc

        # Validate MIME from content-type if the header is present.
        content_type = downloaded.content_type or ""
        if content_type and content_type != mime_type and content_type not in _GENERIC_OCTET_STREAM:
            log_info(
                "artifact_mime_mismatch",
                expected=mime_type,
                actual=content_type,
                url_host="trusted-provider",
            )
            mime_type = content_type or mime_type

        if len(downloaded.body) > max_bytes:
            raise ArtifactPersistenceError(
                "output_too_large",
                f"Provider output exceeds the {max_bytes}-byte artifact limit.",
                retryable=False,
            )

        try:
            return await self._store_bytes(
                downloaded.body, media_type, mime_type, source_expires_at, auth
            )
        except ArtifactPersistenceError:
            raise
        except ValueError as exc:
            raise ArtifactPersistenceError(
                "invalid_output_mime",
                "Provider output has an unsupported media type.",
                retryable=False,
            ) from exc
        except OSError as exc:
            raise ArtifactPersistenceError(
                "storage_failed",
                "Provider output could not be written to artifact storage.",
                retryable=True,
            ) from exc

    async def _store_bytes(
        self,
        raw: bytes,
        media_type: MediaType,
        mime_type: str,
        source_expires_at: str | None,
        auth: AuthContext | None,
    ) -> ArtifactRef:
        """Store raw bytes atomically and return an ``ArtifactRef``."""
        limits = get_media_limits()
        max_bytes = {
            "image": limits.image_max_bytes,
            "audio": limits.audio_max_bytes,
            "video": limits.video_max_bytes,
            "three_d": limits.three_d_max_bytes,
        }[media_type]
        if len(raw) > max_bytes:
            raise ValueError(
                f"{media_type} size ({len(raw)} bytes) exceeds limit ({max_bytes} bytes)."
            )
        {
            "image": validate_image_mime,
            "audio": validate_audio_mime,
            "video": validate_video_mime,
            "three_d": validate_3d_mime,
        }[media_type](mime_type)

        owner = auth or AuthContext()
        artifact_id = str(uuid.uuid4())
        sha256 = hashlib.sha256(raw).hexdigest()
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=self._ttl_seconds)

        # Atomic write: temp file in the same directory, then rename.
        shard_dir = self._artifact_path(artifact_id).parent
        shard_dir.mkdir(parents=True, exist_ok=True)

        ext = _mime_to_ext(mime_type)
        self._atomic_write(self._artifact_path(artifact_id, ext=ext), raw)

        ref = ArtifactRef(
            id=artifact_id,
            uri=f"seed-media://artifacts/{artifact_id}",
            media_type=media_type,
            mime_type=mime_type,
            bytes=len(raw),
            sha256=sha256,
            created_at=now.isoformat(),
            expires_at=expires_at.isoformat(),
            source_expires_at=source_expires_at,
        )

        # Write versioned ownership metadata alongside the artifact.
        metadata = ArtifactMetadata(
            ref=ref,
            principal_id=owner.principal_id,
            tenant_id=owner.tenant_id,
        )
        self._atomic_write(
            self._metadata_path(artifact_id),
            metadata.model_dump_json().encode("utf-8"),
        )

        log_info(
            "artifact_stored",
            artifact_id=artifact_id,
            media_type=media_type,
            mime_type=mime_type,
            bytes=len(raw),
        )
        ARTIFACT_OPERATIONS.labels(
            operation="put",
            status="success",
            media_type=media_type,
        ).inc()
        return ref

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        """Atomically write bytes using a temporary file in the target directory."""
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

    def _find_artifact(self, artifact_id: str) -> Path:
        """Find an artifact file, trying known extensions and bare UUID."""
        self._validate_artifact_id(artifact_id)
        shard = artifact_id[:2]
        for ext in ("", *sorted(_MIME_TO_EXT.values())):
            candidate = self._safe_path(shard, f"{artifact_id}{ext}")
            if candidate.exists():
                return candidate
        return self._artifact_path(artifact_id)

    async def get(self, artifact_id: str, auth: AuthContext | None = None) -> StoredArtifact:
        """Retrieve a stored artifact by ID."""
        path = self._find_artifact(artifact_id)
        meta_path = self._metadata_path(artifact_id)

        if not path.exists():
            raise FileNotFoundError(f"Artifact '{artifact_id}' not found.")

        metadata = self._load_metadata(meta_path)
        owner = auth or AuthContext()
        if metadata.principal_id != owner.principal_id or metadata.tenant_id != owner.tenant_id:
            raise PermissionError("Artifact is not owned by the current principal.")

        data = path.read_bytes()

        artifact = StoredArtifact(
            data=data,
            media_type=metadata.ref.media_type,
            mime_type=metadata.ref.mime_type,
            artifact_id=artifact_id,
        )
        ARTIFACT_OPERATIONS.labels(
            operation="get",
            status="success",
            media_type=metadata.ref.media_type,
        ).inc()
        return artifact

    async def locate(self, artifact_id: str, auth: AuthContext | None = None) -> ArtifactLocation:
        """Resolve the on-disk path and metadata of a stored artifact."""
        self._validate_artifact_id(artifact_id)
        path = self._find_artifact(artifact_id)
        if not path.exists():
            raise FileNotFoundError(f"Artifact '{artifact_id}' not found.")

        metadata = self._load_metadata(self._metadata_path(artifact_id))
        owner = auth or AuthContext()
        if metadata.principal_id != owner.principal_id or metadata.tenant_id != owner.tenant_id:
            raise PermissionError("Artifact is not owned by the current principal.")

        return ArtifactLocation(path=path.resolve(), ref=metadata.ref)

    @staticmethod
    def _load_metadata(meta_path: Path) -> ArtifactMetadata:
        """Load v2 metadata, mapping legacy v1 records to the local owner."""
        if not meta_path.exists():
            raise FileNotFoundError("Artifact metadata not found.")

        import json

        raw_metadata = json.loads(meta_path.read_text())
        if raw_metadata.get("schema_version") == 2:
            return ArtifactMetadata.model_validate(raw_metadata)

        return ArtifactMetadata(
            ref=ArtifactRef.model_validate(raw_metadata),
            principal_id="local",
            tenant_id="local",
        )

    async def delete_expired(self, now: datetime | None = None) -> int:
        """Delete expired artifacts. Returns the number deleted."""
        if now is None:
            now = datetime.now(UTC)
        deleted = 0

        for meta_file in self._base_dir.rglob("*.meta.json"):
            try:
                metadata = self._load_metadata(meta_file)
                expires_str = metadata.ref.expires_at
                if expires_str:
                    expires_dt = datetime.fromisoformat(expires_str)
                    if expires_dt <= now:
                        artifact_id = metadata.ref.id
                        artifact_path = self._find_artifact(artifact_id)
                        if artifact_path.exists():
                            artifact_path.unlink()
                        meta_file.unlink()
                        deleted += 1
            except Exception as exc:
                log_warning(
                    "artifact_cleanup_error",
                    meta_file=str(meta_file),
                    error=str(exc),
                )

        if deleted:
            log_info("artifacts_expired_deleted", count=deleted)
        return deleted

    async def close(self) -> None:
        await self._downloader.close()
