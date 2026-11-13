"""Redaction for dry-run previews of provider request bodies."""

from __future__ import annotations

from urllib.parse import urlsplit


def redact_media_url(url: str) -> str:
    """Reduce a media reference to what is safe to echo back.

    HTTPS URLs keep only scheme and host (paths and signatures can carry
    credentials), inline ``data:`` URIs become their size, and ``asset://``
    references are returned unchanged.
    """
    if url.startswith("data:"):
        return f"<base64: {len(url)} bytes>"
    parts = urlsplit(url)
    if parts.scheme == "https":
        return f"{parts.scheme}://{parts.netloc}"
    return url
