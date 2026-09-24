"""BytePlus OpenAPI V4 (HMAC-SHA256) request signing.

Implements the signature scheme documented at
https://docs.byteplus.com/en/docs/byteplus-platform/reference-how-to-calculate-a-signature
and specified in ``specs/SPEC_VOD_OPENAPI_PROVIDER_CONTRACT.md``:

- Canonical request: ``METHOD\\n/\\nCanonicalQueryString\\nCanonicalHeaders\\n``
  ``SignedHeaders\\nHexEncode(Hash(Payload))``
- Credential scope: ``{YYYYMMDD}/{region}/{service}/request``
- Authorization: ``HMAC-SHA256 Credential={AK}/{scope}, SignedHeaders={...},``
  `` Signature={hex}``

STS temporary credentials add a signed ``X-Security-Token`` header. The helpers
here are pure functions so they can be tested against fixed vectors; they never
log keys, tokens, or signatures.
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class OpenApiCredentials:
    """AK/SK (plus optional STS session token) for BytePlus OpenAPI signing."""

    access_key_id: str
    secret_access_key: str
    session_token: str = ""

    def __repr__(self) -> str:
        return f"OpenApiCredentials(access_key_id={self.access_key_id[:4]}***)"


def sha256_hex(payload: bytes) -> str:
    """Return the lowercase hex SHA-256 digest of ``payload``."""
    return hashlib.sha256(payload).hexdigest()


def rfc3986_quote(value: str) -> str:
    """Percent-encode ``value`` per RFC 3986 (unreserved characters kept)."""
    return quote(value, safe="-_.~")


def canonical_query_string(query: dict[str, str]) -> str:
    """Sort and percent-encode query parameters into a canonical string."""
    return "&".join(
        f"{rfc3986_quote(key)}={rfc3986_quote(value)}" for key, value in sorted(query.items())
    )


def signing_key(secret_access_key: str, short_date: str, region: str, service: str) -> bytes:
    """Derive the request signing key from the secret key and credential scope."""
    key = secret_access_key.encode("utf-8")
    for part in (short_date, region, service, "request"):
        key = hmac.new(key, part.encode("utf-8"), hashlib.sha256).digest()
    return key


def sign_request(
    *,
    credentials: OpenApiCredentials,
    method: str,
    host: str,
    query: dict[str, str],
    payload: bytes,
    region: str,
    service: str,
    now: datetime,
    content_type: str = "application/json",
) -> dict[str, str]:
    """Return the headers that authenticate one OpenAPI request.

    The returned mapping includes ``Host``, ``X-Date``, ``X-Content-Sha256``,
    ``Authorization``, ``Content-Type`` (when a body is sent) and
    ``X-Security-Token`` (for STS credentials). ``now`` must be timezone-aware
    UTC.
    """
    request_date = now.strftime("%Y%m%dT%H%M%SZ")
    short_date = now.strftime("%Y%m%d")
    content_sha256 = sha256_hex(payload)

    header_values: dict[str, str] = {
        "host": host,
        "x-content-sha256": content_sha256,
        "x-date": request_date,
    }
    if method.upper() != "GET":
        header_values["content-type"] = content_type
    if credentials.session_token:
        header_values["x-security-token"] = credentials.session_token

    names = sorted(header_values)
    canonical_headers = "".join(f"{name}:{header_values[name].strip()}\n" for name in names)
    signed_headers = ";".join(names)
    canonical_request = "\n".join(
        (
            method.upper(),
            "/",
            canonical_query_string(query),
            canonical_headers,
            signed_headers,
            content_sha256,
        )
    )
    scope = f"{short_date}/{region}/{service}/request"
    string_to_sign = "\n".join(
        ("HMAC-SHA256", request_date, scope, sha256_hex(canonical_request.encode("utf-8")))
    )
    signature = hmac.new(
        signing_key(credentials.secret_access_key, short_date, region, service),
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    headers = {
        "Host": host,
        "X-Date": request_date,
        "X-Content-Sha256": content_sha256,
        "Authorization": (
            f"HMAC-SHA256 Credential={credentials.access_key_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
    }
    if "content-type" in header_values:
        headers["Content-Type"] = content_type
    if credentials.session_token:
        headers["X-Security-Token"] = credentials.session_token
    return headers
