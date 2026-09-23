"""Object-storage artifact store.

Persists generated media and its ownership metadata into the configured TOS or
S3 bucket instead of the pod-local filesystem, so durable ``seed-media://``
resources survive across replicas.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ark_mcp.artifacts.filesystem_store import (
    _GENERIC_OCTET_STREAM,
    _is_trusted_host,
    _mime_to_ext,
    _translate_download_error,
)
from ark_mcp.artifacts.store import (
    ArtifactLocation,
    ArtifactMetadata,
    ArtifactPersistenceError,
    StoredArtifact,
)
from ark_mcp.config.env import get_settings
from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import debug as log_debug
from ark_mcp.observability.logger import info as log_info
from ark_mcp.observability.metrics import ARTIFACT_OPERATIONS
from ark_mcp.providers.object_storage import make_object_storage_gateway
from ark_mcp.providers.retry import call_with_retry
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
)

if TYPE_CHECKING:
    from ark_mcp.config.env import Settings
    from ark_mcp.security.media_policy import MediaLimits


def _max_bytes_for(limits: MediaLimits, media_type: MediaType) -> int:
    return {
        "image": limits.image_max_bytes,
        "audio": limits.audio_max_bytes,
        "video": limits.video_max_bytes,
        "three_d": limits.three_d_max_bytes,
    }[media_type]


def _validate_mime(media_type: MediaType, mime_type: str) -> None:
    {
        "image": validate_image_mime,
        "audio": validate_audio_mime,
        "video": validate_video_mime,
        "three_d": validate_3d_mime,
    }[media_type](mime_type)


def _trust_self_minted_host(_hostname: str) -> bool:
    return True


class ObjectStorageArtifactStore:
    """Artifact store backed by the configured TOS/S3 object-storage gateway."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        ttl_seconds: int | None = None,
        downloader: SafeDownloader | None = None,
    ) -> None:
        resolved = settings or get_settings()
        self._ttl_seconds = ttl_seconds or resolved.artifact_ttl_seconds
        self._downloader = downloader or SafeDownloader(
            timeout=resolved.artifact_download_timeout_seconds,
            connect_timeout=resolved.connect_timeout_ms / 1000,
            max_attempts=resolved.artifact_download_max_attempts,
        )
        self._gateway = make_object_storage_gateway(resolved)
        self._sweep_logged = False

    @staticmethod
    def _validate_artifact_id(artifact_id: str) -> None:
        try:
            parsed = uuid.UUID(artifact_id, version=4)
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"Invalid artifact ID format: '{artifact_id}'") from exc
        if str(parsed) != artifact_id:
            raise ValueError(f"Invalid artifact ID format: '{artifact_id}'")

    @staticmethod
    def _data_key(artifact_id: str, ext: str) -> str:
        return f"artifacts/{artifact_id[:2]}/{artifact_id}{ext}"

    @staticmethod
    def _meta_key(artifact_id: str) -> str:
        return f"artifacts/{artifact_id[:2]}/{artifact_id}.meta.json"

    async def put_base64(
        self,
        data: str,
        media_type: MediaType,
        mime_type: str,
        source_expires_at: str | None = None,
        auth: AuthContext | None = None,
    ) -> ArtifactRef:
        limits = get_media_limits()
        raw = decode_base64_safely(data, _max_bytes_for(limits, media_type), label=media_type)
        return await self._store_bytes(raw, media_type, mime_type, source_expires_at, auth)

    async def copy_from_trusted_url(
        self,
        url: str,
        media_type: MediaType,
        mime_type: str,
        source_expires_at: str | None = None,
        auth: AuthContext | None = None,
    ) -> ArtifactRef:
        limits = get_media_limits()
        max_bytes = _max_bytes_for(limits, media_type)
        try:
            downloaded = await self._downloader.download(
                url,
                trusted_hosts=_is_trusted_host,
                max_bytes=max_bytes,
            )
        except SafeDownloadError as exc:
            raise _translate_download_error(exc) from exc

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

    async def _store_bytes(
        self,
        raw: bytes,
        media_type: MediaType,
        mime_type: str,
        source_expires_at: str | None,
        auth: AuthContext | None,
    ) -> ArtifactRef:
        limits = get_media_limits()
        max_bytes = _max_bytes_for(limits, media_type)
        if len(raw) > max_bytes:
            raise ValueError(
                f"{media_type} size ({len(raw)} bytes) exceeds limit ({max_bytes} bytes)."
            )
        _validate_mime(media_type, mime_type)

        owner = auth or AuthContext()
        artifact_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        ext = _mime_to_ext(mime_type)
        await self._upload_with_retry(
            key=self._data_key(artifact_id, ext),
            data=raw,
            mime_type=mime_type,
        )

        ref = ArtifactRef(
            id=artifact_id,
            uri=f"seed-media://artifacts/{artifact_id}",
            media_type=media_type,
            mime_type=mime_type,
            bytes=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            created_at=now.isoformat(),
            expires_at=(now + timedelta(seconds=self._ttl_seconds)).isoformat(),
            source_expires_at=source_expires_at,
        )
        metadata = ArtifactMetadata(
            ref=ref,
            principal_id=owner.principal_id,
            tenant_id=owner.tenant_id,
        )
        await self._upload_with_retry(
            key=self._meta_key(artifact_id),
            data=metadata.model_dump_json().encode("utf-8"),
            mime_type="application/json",
        )

        log_info(
            "artifact_stored",
            artifact_id=artifact_id,
            media_type=media_type,
            mime_type=mime_type,
            bytes=len(raw),
            backend="object_storage",
        )
        ARTIFACT_OPERATIONS.labels(
            operation="put",
            status="success",
            media_type=media_type,
        ).inc()
        return ref

    async def _upload_with_retry(self, *, key: str, data: bytes, mime_type: str) -> None:
        """Upload one object, retrying transient provider failures.

        Object keys are fixed per artifact, so a repeated PUT is idempotent.
        Final failures surface as ``ArtifactPersistenceError('storage_failed')``.
        """
        try:
            await call_with_retry(
                lambda: self._gateway.upload_bytes(key=key, data=data, mime_type=mime_type)
            )
        except ProviderError as exc:
            raise ArtifactPersistenceError(
                "storage_failed",
                "Provider output could not be written to artifact storage.",
                retryable=exc.retryable,
            ) from exc

    async def _download_object(self, key: str, max_bytes: int) -> bytes:
        presigned = await self._gateway.presign_get(key=key)
        downloaded = await self._downloader.download(
            presigned,
            trusted_hosts=_trust_self_minted_host,
            max_bytes=max_bytes,
        )
        return downloaded.body

    async def get(self, artifact_id: str, auth: AuthContext | None = None) -> StoredArtifact:
        self._validate_artifact_id(artifact_id)
        try:
            meta_bytes = await self._download_object(self._meta_key(artifact_id), 1_048_576)
            metadata = ArtifactMetadata.model_validate_json(meta_bytes.decode("utf-8"))
        except (SafeDownloadError, ValueError) as exc:
            raise FileNotFoundError(f"Artifact '{artifact_id}' not found.") from exc

        owner = auth or AuthContext()
        if metadata.principal_id != owner.principal_id or metadata.tenant_id != owner.tenant_id:
            raise PermissionError("Artifact is not owned by the current principal.")

        limits = get_media_limits()
        ext = _mime_to_ext(metadata.ref.mime_type)
        try:
            data = await self._download_object(
                self._data_key(artifact_id, ext),
                _max_bytes_for(limits, metadata.ref.media_type),
            )
        except SafeDownloadError as exc:
            raise FileNotFoundError(f"Artifact '{artifact_id}' not found.") from exc

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
        """Return artifact metadata with no local path (object storage backend)."""
        self._validate_artifact_id(artifact_id)
        try:
            meta_bytes = await self._download_object(self._meta_key(artifact_id), 1_048_576)
            metadata = ArtifactMetadata.model_validate_json(meta_bytes.decode("utf-8"))
        except (SafeDownloadError, ValueError) as exc:
            raise FileNotFoundError(f"Artifact '{artifact_id}' not found.") from exc

        owner = auth or AuthContext()
        if metadata.principal_id != owner.principal_id or metadata.tenant_id != owner.tenant_id:
            raise PermissionError("Artifact is not owned by the current principal.")

        return ArtifactLocation(path=None, ref=metadata.ref)

    async def delete_expired(self, now: datetime) -> int:
        if not self._sweep_logged:
            log_debug("object_artifact_sweep_unsupported", backend="object_storage")
            self._sweep_logged = True
        return 0

    async def close(self) -> None:
        await self._gateway.close()
        await self._downloader.close()
