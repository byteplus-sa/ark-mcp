"""Media policy — MIME, size, and duration preflight for Base64 media.

Preflight Base64 decoded size, MIME type, and audio duration before calling
the provider. This prevents oversized or malicious media from reaching the
upstream API.
"""

from __future__ import annotations

import base64
import struct
from typing import ClassVar

from pydantic import BaseModel


class MediaValidationError(ValueError):
    """Raised when media fails size or MIME validation."""


# Allowed MIME types per media category.
_ALLOWED_AUDIO_MIMES: frozenset[str] = frozenset(
    {
        "audio/wav",
        "audio/x-wav",
        "audio/wave",
        "audio/mpeg",
        "audio/mp3",
        "audio/aac",
        "audio/x-aac",
        "audio/mp4",
        "audio/pcm",
        "audio/x-pcm",
        "audio/ogg",
        "audio/ogg;codecs=opus",
        "audio/webm",
        "audio/flac",
        "audio/x-flac",
    }
)

_ALLOWED_IMAGE_MIMES: frozenset[str] = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
    }
)

_ALLOWED_VIDEO_MIMES: frozenset[str] = frozenset(
    {
        "video/mp4",
        "video/quicktime",
        "video/x-matroska",
    }
)


class MediaLimits(BaseModel):
    """Size limits (in bytes) for each media type."""

    audio_max_bytes: int = 10 * 1024 * 1024  # 10 MB per the API docs
    audio_max_seconds: int = 30
    image_max_bytes: int = 10 * 1024 * 1024  # 10 MB
    video_max_bytes: int = 200 * 1024 * 1024  # 200 MB

    ALLOWED_AUDIO_MIMES: ClassVar[frozenset[str]] = _ALLOWED_AUDIO_MIMES
    ALLOWED_IMAGE_MIMES: ClassVar[frozenset[str]] = _ALLOWED_IMAGE_MIMES
    ALLOWED_VIDEO_MIMES: ClassVar[frozenset[str]] = _ALLOWED_VIDEO_MIMES


def get_media_limits() -> MediaLimits:
    """Return the default media limits."""
    return MediaLimits()


def validate_audio_mime(mime_type: str | None) -> None:
    """Validate that the MIME type is an allowed audio format."""
    if mime_type is None:
        return  # Let the provider reject it.
    # Normalize and check.
    normalized = mime_type.lower().split(";")[0].strip()
    if normalized not in _ALLOWED_AUDIO_MIMES:
        raise MediaValidationError(
            f"Audio MIME type '{mime_type}' is not allowed. Allowed: {sorted(_ALLOWED_AUDIO_MIMES)}"
        )


def validate_image_mime(mime_type: str | None) -> None:
    """Validate that the MIME type is an allowed image format."""
    if mime_type is None:
        return
    normalized = mime_type.lower().split(";")[0].strip()
    if normalized not in _ALLOWED_IMAGE_MIMES:
        raise MediaValidationError(
            f"Image MIME type '{mime_type}' is not allowed. Allowed: {sorted(_ALLOWED_IMAGE_MIMES)}"
        )


def validate_video_mime(mime_type: str | None) -> None:
    """Validate that the MIME type is an allowed video format."""
    if mime_type is None:
        return
    normalized = mime_type.lower().split(";")[0].strip()
    if normalized not in _ALLOWED_VIDEO_MIMES:
        raise MediaValidationError(
            f"Video MIME type '{mime_type}' is not allowed. Allowed: {sorted(_ALLOWED_VIDEO_MIMES)}"
        )


def check_base64_size(data: str, max_bytes: int, *, label: str = "media") -> int:
    """Check the decoded size of Base64 data without fully decoding.

    Returns the estimated decoded size in bytes.
    Raises MediaValidationError if the decoded size exceeds max_bytes.
    """
    # Estimate: Base64 encodes 3 bytes as 4 chars.
    # Remove padding for accurate estimation.
    stripped = data.rstrip("=")
    estimated = (len(stripped) * 3) // 4
    if estimated > max_bytes:
        raise MediaValidationError(
            f"{label} decoded size ({estimated} bytes) exceeds limit ({max_bytes} bytes)."
        )
    return estimated


def decode_base64_safely(data: str, max_bytes: int, *, label: str = "media") -> bytes:
    """Decode Base64 data with size limit enforcement.

    Raises MediaValidationError if the decoded size exceeds max_bytes.
    """
    check_base64_size(data, max_bytes, label=label)
    try:
        return base64.b64decode(data, validate=True)
    except Exception as exc:
        raise MediaValidationError(f"Invalid Base64 data for {label}: {exc}") from exc


def _parse_wav_duration(raw: bytes) -> float | None:
    """Parse a WAV file's RIFF header and return duration in seconds.

    Walks the WAVE chunk list to find ``fmt `` (byte rate) and ``data``
    (payload size). Returns ``None`` for non-WAV data or malformed headers.
    """
    if len(raw) < 12:
        return None
    if raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return None

    offset = 12
    byte_rate: int | None = None
    data_size: int | None = None

    while offset + 8 <= len(raw):
        chunk_id = raw[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", raw, offset + 4)[0]
        offset += 8

        if chunk_id == b"fmt " and offset + 16 <= len(raw):
            byte_rate = struct.unpack_from("<I", raw, offset + 8)[0]
        elif chunk_id == b"data":
            data_size = chunk_size
            break

        offset += chunk_size
        if chunk_size % 2 == 1:
            offset += 1

    if byte_rate and data_size is not None and byte_rate > 0:
        return data_size / byte_rate
    return None


def check_audio_duration_from_base64(
    data: str, max_seconds: int, *, label: str = "audio"
) -> float | None:
    """Check audio duration from Base64-encoded WAV data.

    Decodes the Base64 data and parses the WAV RIFF header to determine
    duration. For non-WAV formats (MP3, PCM, OGG), duration cannot be
    determined without a full decoder and the check is skipped — the
    provider enforces the limit server-side.

    This preflight is best-effort: it trusts the declared ``data`` chunk
    size in the WAV header. A crafted header with an under-reported size
    would bypass the check, but the provider enforces the limit
    server-side regardless.

    Returns the duration in seconds if it could be determined, ``None``
    otherwise.
    Raises :class:`MediaValidationError` if the duration exceeds
    ``max_seconds``.
    """
    try:
        raw = base64.b64decode(data, validate=True)
    except Exception as exc:
        raise MediaValidationError(f"Invalid Base64 data for {label}: {exc}") from exc

    duration = _parse_wav_duration(raw)
    if duration is not None and duration > max_seconds:
        raise MediaValidationError(
            f"{label} duration ({duration:.1f}s) exceeds limit ({max_seconds}s)."
        )
    return duration
