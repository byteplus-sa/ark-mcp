"""Shared models and input resolution for the OmniHuman 2.0 tools."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from ark_mcp.artifacts.store import PresignableArtifactStore
from ark_mcp.domain.artifacts import MediaType
from ark_mcp.security.url_policy import AddressResolver, UrlValidationError, validate_url

if TYPE_CHECKING:
    from ark_mcp.artifacts.store import ArtifactStore
    from ark_mcp.security.auth_context import AuthContext

MediaKind = Literal["image", "audio"]

ARTIFACT_URI_PREFIX = "seed-media://artifacts/"
IMAGE_MAX_BYTES = 30 * 1024 * 1024
AUDIO_MAX_BYTES = 15 * 1024 * 1024
PRESIGN_TTL_SECONDS = 3600
OUTPUT_HOST_SUFFIXES: tuple[str, ...] = (".tiktokcdn.com",)

_IMAGE_MIME_TYPES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/bmp",
        "image/tiff",
        "image/gif",
        "image/heic",
        "image/heif",
    }
)
_AUDIO_MIME_TYPES: frozenset[str] = frozenset(
    {"audio/wav", "audio/x-wav", "audio/wave", "audio/mpeg", "audio/mp3"}
)

_KIND_RULES: dict[MediaKind, tuple[MediaType, frozenset[str], int, str]] = {
    "image": (MediaType.IMAGE, _IMAGE_MIME_TYPES, IMAGE_MAX_BYTES, "image format"),
    "audio": (MediaType.AUDIO, _AUDIO_MIME_TYPES, AUDIO_MAX_BYTES, "audio format (wav or mp3)"),
}


class MediaRefError(ValueError):
    """A media reference that OmniHuman cannot use; the message is safe to show clients."""


async def resolve_media_ref(
    ref: str,
    kind: MediaKind,
    *,
    store: ArtifactStore,
    owner: AuthContext,
    address_resolver: AddressResolver | None = None,
) -> str:
    """Turn an HTTPS URL or ``seed-media://`` artifact into a URL BytePlus can fetch."""
    cleaned = ref.strip()
    if cleaned.startswith(ARTIFACT_URI_PREFIX):
        return await _presign_artifact(
            cleaned.removeprefix(ARTIFACT_URI_PREFIX), kind, store=store, owner=owner
        )
    try:
        return validate_url(cleaned, resolver=address_resolver).url
    except UrlValidationError:
        raise MediaRefError(
            f"The {kind} reference must be a public HTTPS URL or a seed-media://artifacts/<id> "
            f"artifact. {UrlValidationError.safe_message}"
        ) from None


async def _presign_artifact(
    artifact_id: str, kind: MediaKind, *, store: ArtifactStore, owner: AuthContext
) -> str:
    if not isinstance(store, PresignableArtifactStore):
        raise MediaRefError(
            "Artifact references need object storage (OBJECT_STORAGE_BACKEND with "
            "ARTIFACT_BACKEND=object_storage) so BytePlus can fetch the file. Upload it with "
            f"media_upload and pass the returned presigned URL as the {kind} instead."
        )
    try:
        presigned = await store.presign(
            artifact_id, expires_in_seconds=PRESIGN_TTL_SECONDS, auth=owner
        )
    except (FileNotFoundError, PermissionError):
        raise MediaRefError(f"The {kind} artifact was not found.") from None
    except ValueError:
        raise MediaRefError(f"The {kind} artifact reference is malformed.") from None

    media_type, mime_types, max_bytes, label = _KIND_RULES[kind]
    ref = presigned.ref
    if ref.media_type != media_type:
        raise MediaRefError(f"The artifact is {ref.media_type.value}, but an {kind} is required.")
    if ref.mime_type not in mime_types:
        raise MediaRefError(f"The artifact has an unsupported {label}: {ref.mime_type}.")
    if ref.bytes is not None and ref.bytes >= max_bytes:
        raise MediaRefError(
            f"The {kind} artifact is larger than the {max_bytes // (1024 * 1024)} MB limit."
        )
    return presigned.url
