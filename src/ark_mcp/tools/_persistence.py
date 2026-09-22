"""Shared artifact persistence helpers that never drop a billed output.

Generation tools call these instead of the artifact store directly. When
durable storage fails after the store's own retries, the helpers degrade
instead of raising:

- URL outputs return an unpersisted ``ArtifactRef`` whose ``uri`` is the
  temporary provider URL, with ``persistence_error`` set.
- Inline Base64 outputs are retried once, then returned inline in
  ``fallback_data`` when within ``ARTIFACT_INLINE_FALLBACK_MAX_BYTES``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ark_mcp.artifacts.store import ArtifactPersistenceError, ArtifactStore
from ark_mcp.config.env import get_settings
from ark_mcp.domain.artifacts import ArtifactPersistenceIssue, ArtifactRef, MediaType
from ark_mcp.domain.models import VariationError
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.security.auth_context import AuthContext
from ark_mcp.security.media_policy import get_media_limits

PROVIDER_URL_ARTIFACT_ID = "provider-url"
INLINE_FALLBACK_ARTIFACT_ID = "inline-fallback"

_STORAGE_FAILED_MESSAGE = "Provider output could not be written to artifact storage."


def artifact_limit_bytes(media_type: MediaType) -> int:
    """Return the durable artifact size limit for ``media_type`` in bytes."""
    limits = get_media_limits()
    return {
        MediaType.IMAGE: limits.image_max_bytes,
        MediaType.AUDIO: limits.audio_max_bytes,
        MediaType.VIDEO: limits.video_max_bytes,
        MediaType.THREE_D: limits.three_d_max_bytes,
    }[MediaType(media_type)]


def persistence_issue(
    exc: ArtifactPersistenceError | None,
    media_type: MediaType,
    *,
    source_url_expires_at: str | None = None,
) -> ArtifactPersistenceIssue:
    """Build a URL-safe persistence issue; ``exc=None`` means an internal storage error."""
    if exc is None:
        return ArtifactPersistenceIssue(
            code="storage_failed",
            message=_STORAGE_FAILED_MESSAGE,
            retryable=True,
            artifact_limit_bytes=artifact_limit_bytes(media_type),
            source_url_expires_at=source_url_expires_at,
        )
    return ArtifactPersistenceIssue(
        code=exc.code,
        message=exc.safe_message,
        retryable=exc.retryable,
        artifact_limit_bytes=artifact_limit_bytes(media_type),
        source_url_expires_at=source_url_expires_at,
    )


def provider_url_ref(
    *,
    url: str,
    media_type: MediaType,
    mime_type: str,
    source_expires_at: str | None,
    issue: ArtifactPersistenceIssue | None = None,
) -> ArtifactRef:
    """Return an unpersisted reference to a temporary provider URL."""
    return ArtifactRef(
        id=PROVIDER_URL_ARTIFACT_ID,
        uri=url,
        media_type=media_type,
        mime_type=mime_type,
        created_at=datetime.now(UTC).isoformat(),
        source_expires_at=source_expires_at,
        persistence_error=issue,
    )


async def persist_from_url(
    store: ArtifactStore,
    *,
    url: str,
    media_type: MediaType,
    mime_type: str,
    source_expires_at: str | None,
    auth: AuthContext | None,
) -> ArtifactRef:
    """Persist a provider URL, or return the URL itself with ``persistence_error``.

    The store's downloader already retries retryable download failures, so a
    failure here is final for this call. The output was billed, so it is
    returned as a provider-URL reference rather than raised.
    """
    try:
        return await store.copy_from_trusted_url(
            url=url,
            media_type=media_type,
            mime_type=mime_type,
            source_expires_at=source_expires_at,
            auth=auth,
        )
    except ArtifactPersistenceError as exc:
        issue = persistence_issue(exc, media_type, source_url_expires_at=source_expires_at)
    except (OSError, RuntimeError):
        issue = persistence_issue(None, media_type, source_url_expires_at=source_expires_at)
    log_warning("artifact_persist_fallback_url", media_type=str(media_type), code=issue.code)
    return provider_url_ref(
        url=url,
        media_type=media_type,
        mime_type=mime_type,
        source_expires_at=source_expires_at,
        issue=issue,
    )


def _decoded_size(data: str) -> int:
    stripped = data.strip()
    padding = len(stripped) - len(stripped.rstrip("="))
    return max(0, (len(stripped) * 3) // 4 - padding)


async def persist_base64(
    store: ArtifactStore,
    *,
    data: str,
    media_type: MediaType,
    mime_type: str,
    source_expires_at: str | None,
    auth: AuthContext | None,
    provider_url: str | None = None,
) -> ArtifactRef:
    """Persist inline Base64 output without losing it when storage fails.

    Retries a retryable store failure once. On final failure it falls back to
    ``provider_url`` when one exists, otherwise returns the bytes inline in
    ``fallback_data`` when within ``ARTIFACT_INLINE_FALLBACK_MAX_BYTES``. Above
    that cap the ``ArtifactPersistenceError`` is raised.

    Invalid or oversized Base64 (``ValueError`` from the media policy) is not
    a storage failure and propagates unchanged.
    """
    last_exc: ArtifactPersistenceError | None = None
    for attempt in range(2):
        try:
            return await store.put_base64(
                data=data,
                media_type=media_type,
                mime_type=mime_type,
                source_expires_at=source_expires_at,
                auth=auth,
            )
        except ArtifactPersistenceError as exc:
            last_exc = exc
            if not exc.retryable:
                break
        except OSError:
            last_exc = ArtifactPersistenceError(
                "storage_failed", _STORAGE_FAILED_MESSAGE, retryable=True
            )
        log_warning("artifact_persist_base64_retry", attempt=attempt + 1)

    assert last_exc is not None
    if provider_url:
        return provider_url_ref(
            url=provider_url,
            media_type=media_type,
            mime_type=mime_type,
            source_expires_at=source_expires_at,
            issue=persistence_issue(last_exc, media_type, source_url_expires_at=source_expires_at),
        )

    cap = get_settings().artifact_inline_fallback_max_bytes
    if cap <= 0 or _decoded_size(data) > cap:
        raise last_exc
    log_warning("artifact_persist_fallback_inline", media_type=str(media_type))
    return ArtifactRef(
        id=INLINE_FALLBACK_ARTIFACT_ID,
        uri=f"data:{mime_type};base64,",
        media_type=media_type,
        mime_type=mime_type,
        bytes=_decoded_size(data),
        created_at=datetime.now(UTC).isoformat(),
        source_expires_at=source_expires_at,
        persistence_error=persistence_issue(last_exc, media_type),
        fallback_data=data,
    )


def persistence_variation_error(exc: ArtifactPersistenceError) -> VariationError:
    """Describe a final persistence failure of one variation."""
    return VariationError(
        code=exc.code.upper(),
        message=exc.safe_message,
        retryable=exc.retryable,
        ambiguous_completion=False,
        phase="persisting",
    )
