"""Unit tests for the BytePlus OpenAPI V4 signer (service ``ark``)."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

from ark_mcp.providers.byteplus_openapi.signing import (
    OpenApiCredentials,
    canonical_query_string,
    sign_request,
)

_NOW = datetime(2026, 9, 24, 1, 2, 3, tzinfo=UTC)
_HOST = "ark.ap-southeast-1.byteplusapi.com"
_BODY = b'{"Name":"mcp-api-test","GroupType":"AIGC"}'


def _expected_signature(*, secret: str, signed_headers: list[tuple[str, str]]) -> str:
    """Independent re-derivation of the documented V4 algorithm."""
    payload_hash = hashlib.sha256(_BODY).hexdigest()
    canonical_headers = "".join(f"{name}:{value}\n" for name, value in signed_headers)
    canonical_request = "\n".join(
        [
            "POST",
            "/",
            "Action=CreateAssetGroup&Version=2024-01-01",
            canonical_headers,
            ";".join(name for name, _ in signed_headers),
            payload_hash,
        ]
    )
    scope = "20260924/ap-southeast-1/ark/request"
    string_to_sign = "\n".join(
        [
            "HMAC-SHA256",
            "20260924T010203Z",
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    key = secret.encode()
    for part in ("20260924", "ap-southeast-1", "ark", "request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    return hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()


def _sign(credentials: OpenApiCredentials) -> dict[str, str]:
    return sign_request(
        credentials=credentials,
        method="POST",
        host=_HOST,
        query={"Version": "2024-01-01", "Action": "CreateAssetGroup"},
        payload=_BODY,
        region="ap-southeast-1",
        service="ark",
        now=_NOW,
    )


def test_canonical_query_string_sorts_and_encodes() -> None:
    assert canonical_query_string({"b": "x y", "a": "1/2"}) == "a=1%2F2&b=x%20y"


def test_long_term_credentials_signature_matches_reference() -> None:
    headers = _sign(OpenApiCredentials("AKLTexample", "secret-example"))  # pragma: allowlist secret
    payload_hash = hashlib.sha256(_BODY).hexdigest()
    signature = _expected_signature(
        secret="secret-example",  # pragma: allowlist secret
        signed_headers=[
            ("content-type", "application/json"),
            ("host", _HOST),
            ("x-content-sha256", payload_hash),
            ("x-date", "20260924T010203Z"),
        ],
    )
    assert headers["X-Date"] == "20260924T010203Z"
    assert headers["X-Content-Sha256"] == payload_hash
    assert headers["Authorization"] == (
        "HMAC-SHA256 Credential=AKLTexample/20260924/ap-southeast-1/ark/request, "
        "SignedHeaders=content-type;host;x-content-sha256;x-date, "
        f"Signature={signature}"
    )
    assert "X-Security-Token" not in headers


def test_sts_session_token_is_sent_and_signed() -> None:
    headers = _sign(
        OpenApiCredentials("AKTPexample", "secret-example", "token-123")
    )  # pragma: allowlist secret
    payload_hash = hashlib.sha256(_BODY).hexdigest()
    signature = _expected_signature(
        secret="secret-example",  # pragma: allowlist secret
        signed_headers=[
            ("content-type", "application/json"),
            ("host", _HOST),
            ("x-content-sha256", payload_hash),
            ("x-date", "20260924T010203Z"),
            ("x-security-token", "token-123"),  # pragma: allowlist secret
        ],
    )
    assert headers["X-Security-Token"] == "token-123"  # pragma: allowlist secret
    assert (
        "SignedHeaders=content-type;host;x-content-sha256;x-date;x-security-token"
        in (headers["Authorization"])
    )
    assert headers["Authorization"].endswith(f"Signature={signature}")


def test_credentials_repr_hides_secrets() -> None:
    text = repr(
        OpenApiCredentials("AKLTexample", "super-secret", "token-abc")
    )  # pragma: allowlist secret
    assert "super-secret" not in text  # pragma: allowlist secret
    assert "token-abc" not in text
