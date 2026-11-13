from __future__ import annotations

import base64
import hashlib
import json
import logging
from typing import Any

import httpx
import pytest
import respx
from scripts.byteplus_deploy.registry import RegistryError, check_image

BASE = "https://ghcr.io/v2/example/ark-mcp"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
OCI_CONFIG = "application/vnd.oci.image.config.v1+json"


def encoded(value: dict[str, Any]) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def config_blob(**changes: Any) -> bytes:
    return encoded(
        {
            "os": "linux",
            "architecture": "amd64",
            "config": {"Env": ["MCP_AUTH_MODE=api_key"]},
            **changes,
        }
    )


def manifest_blob(config: bytes) -> bytes:
    return encoded(
        {
            "schemaVersion": 2,
            "mediaType": OCI_MANIFEST,
            "config": {"mediaType": OCI_CONFIG, "digest": digest(config), "size": len(config)},
            "layers": [],
        }
    )


def image_name(manifest: bytes) -> str:
    return "ghcr.io/example/ark-mcp@" + digest(manifest)


def register_image(config: bytes) -> bytes:
    manifest = manifest_blob(config)
    respx.get(f"{BASE}/manifests/{digest(manifest)}").respond(
        200, content=manifest, headers={"Content-Type": OCI_MANIFEST}
    )
    respx.get(f"{BASE}/blobs/{digest(config)}").respond(200, content=config)
    return manifest


@respx.mock
def test_public_image_digest_platform_and_auth_mode_are_verified(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manifest = register_image(config_blob())
    with caplog.at_level(logging.DEBUG):
        check_image(image_name(manifest))
    assert len(respx.calls) == 2
    assert not caplog.text
    assert all("authorization" not in call.request.headers for call in respx.calls)


@pytest.mark.parametrize(
    "image",
    [
        "",
        "ghcr.io/example/ark-mcp:latest",
        "ghcr.io/example/ark-mcp:v1.0.0",
        "docker.io/example/ark-mcp@sha256:" + "0" * 64,
        "ghcr.io@example.com/ark-mcp@sha256:" + "0" * 64,
        "ghcr.io/example/../ark-mcp@sha256:" + "0" * 64,
        "ghcr.io/example/ark-mcp@sha256:" + "A" * 64,
        "https://ghcr.io/example/ark-mcp@sha256:" + "0" * 64,
        "ghcr.io/example/ark-mcp@sha256:" + "0" * 64 + "?token=private",
    ],
)
@respx.mock
def test_only_plain_ghcr_sha256_references_are_allowed(image: str) -> None:
    with pytest.raises(RegistryError):
        check_image(image)
    assert not respx.calls


@pytest.mark.parametrize(
    "body",
    [
        {"schemaVersion": 2, "mediaType": []},
        {"schemaVersion": 2, "mediaType": {}},
        {
            "schemaVersion": 2,
            "mediaType": OCI_INDEX,
            "manifests": [
                {
                    "digest": "sha256:" + "0" * 64,
                    "size": 1,
                    "mediaType": OCI_MANIFEST,
                    "platform": {"os": "linux", "architecture": "amd64", "variant": []},
                }
            ],
        },
    ],
)
@respx.mock
def test_untrusted_manifest_field_types_are_sanitized(body: dict[str, Any]) -> None:
    content = encoded(body)
    respx.get(f"{BASE}/manifests/{digest(content)}").respond(200, content=content)
    with pytest.raises(RegistryError):
        check_image(image_name(content))


@respx.mock
def test_wrong_docker_content_digest_header_is_rejected() -> None:
    config = config_blob()
    manifest = register_image(config)
    respx.get(f"{BASE}/manifests/{digest(manifest)}").respond(
        200, content=manifest, headers={"Docker-Content-Digest": "sha256:" + "0" * 64}
    )
    with pytest.raises(RegistryError, match="integrity"):
        check_image(image_name(manifest))


@respx.mock
def test_wrong_config_descriptor_size_is_rejected() -> None:
    config = config_blob()
    body = json.loads(manifest_blob(config))
    body["config"]["size"] += 1
    manifest = encoded(body)
    respx.get(f"{BASE}/manifests/{digest(manifest)}").respond(200, content=manifest)
    respx.get(f"{BASE}/blobs/{digest(config)}").respond(200, content=config)
    with pytest.raises(RegistryError, match="integrity"):
        check_image(image_name(manifest))


@respx.mock
def test_expired_private_token_does_not_leak_authentication_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    manifest = manifest_blob(config_blob())
    respx.get(f"{BASE}/manifests/{digest(manifest)}").respond(
        401, headers={"WWW-Authenticate": CHALLENGE}
    )
    respx.get("https://ghcr.io/token").respond(403, text="test-registry-value")
    with caplog.at_level(logging.DEBUG), pytest.raises(RegistryError) as caught:
        check_image(image_name(manifest), "test-user", "test-registry-value")
    assert "test-registry-value" not in str(caught.value)
    assert not caplog.text
    assert len(respx.calls) == 2


@respx.mock
def test_wrong_manifest_digest_is_rejected_before_blob_requests() -> None:
    url = f"{BASE}/manifests/sha256:" + "0" * 64
    respx.get(url).respond(200, content=manifest_blob(config_blob()))
    with pytest.raises(RegistryError, match="integrity"):
        check_image("ghcr.io/example/ark-mcp@sha256:" + "0" * 64)
    assert len(respx.calls) == 1


@respx.mock
def test_wrong_config_digest_is_rejected() -> None:
    config = config_blob()
    manifest = register_image(config)
    respx.get(f"{BASE}/blobs/{digest(config)}").respond(200, content=config + b" ")
    with pytest.raises(RegistryError, match="integrity"):
        check_image(image_name(manifest))


@pytest.mark.parametrize(
    "changes",
    [
        {"architecture": "arm64"},
        {"os": "windows"},
        {"config": {}},
        {"config": {"Env": ["MCP_AUTH_MODE=local"]}},
        {"config": {"Env": ["MCP_AUTH_MODE=api_key", "MCP_AUTH_MODE=local"]}},
        {"config": {"Env": ["MCP_AUTH_MODE=api_key", "MCP_AUTH_MODE=api_key"]}},
        {"config": {"Env": "MCP_AUTH_MODE=api_key"}},
        {"config": {"Env": [None]}},
    ],
)
@respx.mock
def test_config_must_be_linux_amd64_with_one_explicit_shared_key_mode(
    changes: dict[str, Any],
) -> None:
    manifest = register_image(config_blob(**changes))
    with pytest.raises(RegistryError):
        check_image(image_name(manifest))


@respx.mock
def test_multi_platform_index_verifies_selected_child_manifest_and_config() -> None:
    config = config_blob()
    manifest = register_image(config)
    index = encoded(
        {
            "schemaVersion": 2,
            "mediaType": OCI_INDEX,
            "manifests": [
                {
                    "digest": "sha256:" + "1" * 64,
                    "size": 123,
                    "mediaType": OCI_MANIFEST,
                    "platform": {"os": "linux", "architecture": "arm64"},
                },
                {
                    "digest": digest(manifest),
                    "size": len(manifest),
                    "mediaType": OCI_MANIFEST,
                    "platform": {"os": "linux", "architecture": "amd64"},
                },
            ],
        }
    )
    respx.get(f"{BASE}/manifests/{digest(index)}").respond(200, content=index)
    check_image(image_name(index))
    assert len(respx.calls) == 3


@respx.mock
def test_index_descriptor_cannot_disguise_wrong_config_architecture() -> None:
    manifest = register_image(config_blob(architecture="arm64"))
    index = encoded(
        {
            "schemaVersion": 2,
            "mediaType": OCI_INDEX,
            "manifests": [
                {
                    "digest": digest(manifest),
                    "size": len(manifest),
                    "mediaType": OCI_MANIFEST,
                    "platform": {"os": "linux", "architecture": "amd64"},
                }
            ],
        }
    )
    respx.get(f"{BASE}/manifests/{digest(index)}").respond(200, content=index)
    with pytest.raises(RegistryError):
        check_image(image_name(index))


@pytest.mark.parametrize(
    "manifest",
    [
        {"schemaVersion": 1},
        {"schemaVersion": 2, "mediaType": OCI_MANIFEST, "config": None},
        {
            "schemaVersion": 2,
            "mediaType": OCI_INDEX,
            "manifests": [{"platform": {"os": "linux", "architecture": "arm64"}, "digest": "bad"}],
        },
        {
            "schemaVersion": 2,
            "mediaType": OCI_MANIFEST,
            "config": {"digest": "../../private", "size": 1},
        },
    ],
)
@respx.mock
def test_malformed_or_unsupported_manifests_are_rejected(manifest: dict[str, Any]) -> None:
    content = encoded(manifest)
    respx.get(f"{BASE}/manifests/{digest(content)}").respond(200, content=content)
    with pytest.raises(RegistryError):
        check_image(image_name(content))


CHALLENGE = (
    'Bearer realm="https://ghcr.io/token",service="ghcr.io",scope="repository:example/ark-mcp:pull"'
)


@respx.mock
def test_anonymous_token_exchange_stays_on_ghcr_and_requests_pull_only() -> None:
    config = config_blob()
    manifest = register_image(config)
    route = respx.get(f"{BASE}/manifests/{digest(manifest)}")
    route.side_effect = [
        httpx.Response(401, headers={"WWW-Authenticate": CHALLENGE}),
        httpx.Response(200, content=manifest),
    ]
    token_route = respx.get("https://ghcr.io/token").respond(200, json={"token": "issued-test"})
    check_image(image_name(manifest))
    token_request = token_route.calls[0].request
    assert token_request.url.params["service"] == "ghcr.io"
    assert token_request.url.params["scope"] == "repository:example/ark-mcp:pull"
    assert "Authorization" not in token_request.headers
    assert route.calls[1].request.headers["Authorization"] == "Bearer issued-test"


@respx.mock
def test_private_credentials_are_explicit_and_never_sent_to_first_request() -> None:
    config = config_blob()
    manifest = register_image(config)
    route = respx.get(f"{BASE}/manifests/{digest(manifest)}")
    route.side_effect = [
        httpx.Response(401, headers={"WWW-Authenticate": CHALLENGE}),
        httpx.Response(200, content=manifest),
    ]
    token_route = respx.get("https://ghcr.io/token").respond(200, json={"access_token": "issued"})
    check_image(image_name(manifest), "test-user", "test-registry-value")
    assert "Authorization" not in route.calls[0].request.headers
    credentials = token_route.calls[0].request.headers["Authorization"].removeprefix("Basic ")
    assert base64.b64decode(credentials) == b"test-user:test-registry-value"


@pytest.mark.parametrize(
    "challenge",
    [
        CHALLENGE.replace("https://ghcr.io/token", "https://attacker.example/token"),
        CHALLENGE.replace("https://ghcr.io/token", "http://ghcr.io/token"),
        CHALLENGE.replace("https://ghcr.io/token", "https://user@ghcr.io/token"),
        CHALLENGE.replace("https://ghcr.io/token", "https://ghcr.io/token?private=true"),
        CHALLENGE.replace('service="ghcr.io"', 'service="attacker"'),
        CHALLENGE.replace(":pull", ":pull,push"),
        CHALLENGE.replace("example/ark-mcp", "unrelated/repo"),
        CHALLENGE + ',realm="https://attacker.example"',
        "Basic realm=private",
        "",
    ],
)
@respx.mock
def test_untrusted_auth_challenge_cannot_receive_credentials(challenge: str) -> None:
    manifest = manifest_blob(config_blob())
    respx.get(f"{BASE}/manifests/{digest(manifest)}").respond(
        401, headers={"WWW-Authenticate": challenge}
    )
    with pytest.raises(RegistryError):
        check_image(image_name(manifest), "test-user", "test-registry-value")
    assert len(respx.calls) == 1


@pytest.mark.parametrize("status", [302, 403, 404, 500])
@respx.mock
def test_registry_http_errors_are_sanitized_and_never_redirect(status: int) -> None:
    manifest = manifest_blob(config_blob())
    respx.get(f"{BASE}/manifests/{digest(manifest)}").respond(
        status, text="test-registry-value", headers={"Location": "https://attacker.example"}
    )
    with pytest.raises(RegistryError) as caught:
        check_image(image_name(manifest), "test-user", "test-registry-value")
    assert "test-registry-value" not in str(caught.value)
    assert "https:" not in str(caught.value)
    assert len(respx.calls) == 1


@respx.mock
def test_registry_transport_failure_is_credential_free() -> None:
    manifest = manifest_blob(config_blob())
    respx.get(f"{BASE}/manifests/{digest(manifest)}").side_effect = httpx.ReadTimeout(
        "test-registry-value"
    )
    with pytest.raises(RegistryError) as caught:
        check_image(image_name(manifest))
    assert "test-registry-value" not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(("username", "token"), [("user", None), (None, "value"), ("", "value")])
@respx.mock
def test_partial_private_credentials_are_rejected_before_requests(
    username: str | None, token: str | None
) -> None:
    with pytest.raises(RegistryError):
        check_image(image_name(manifest_blob(config_blob())), username, token)
    assert not respx.calls


@respx.mock
def test_blob_redirect_to_github_cdn_strips_credentials_and_remains_digest_verified(
    caplog: pytest.LogCaptureFixture,
) -> None:
    config = config_blob()
    manifest = register_image(config)
    respx.get(f"{BASE}/manifests/{digest(manifest)}").side_effect = [
        httpx.Response(401, headers={"WWW-Authenticate": CHALLENGE}),
        httpx.Response(200, content=manifest),
    ]
    respx.get("https://ghcr.io/token").respond(200, json={"token": "test-bearer-value"})
    target = "https://pkg-containers.githubusercontent.com/ghcr1/blobs/example?sig=private"
    respx.get(f"{BASE}/blobs/{digest(config)}").respond(
        307,
        headers={
            "Location": target,
            "Set-Cookie": "private=registry; Domain=githubusercontent.com",
        },
    )
    download = respx.get(target).respond(200, content=config)
    with caplog.at_level(logging.DEBUG):
        check_image(image_name(manifest), "test-user", "test-registry-value")
    assert "Authorization" not in download.calls[0].request.headers
    assert "Cookie" not in download.calls[0].request.headers
    assert not caplog.text


@pytest.mark.parametrize(
    "target",
    [
        "https://attacker.example/config",
        "http://pkg-containers.githubusercontent.com/config",
        "https://user@pkg-containers.githubusercontent.com/config",
        "https://pkg-containers.githubusercontent.com:8443/config",
        "https://pkg-containers.githubusercontent.com/config#fragment",
    ],
)
@respx.mock
def test_untrusted_blob_redirects_are_rejected_without_following(target: str) -> None:
    config = config_blob()
    manifest = register_image(config)
    respx.get(f"{BASE}/blobs/{digest(config)}").respond(307, headers={"Location": target})
    with pytest.raises(RegistryError):
        check_image(image_name(manifest))
    assert len(respx.calls) == 2


@respx.mock
def test_blob_redirect_content_still_requires_original_digest() -> None:
    config = config_blob()
    manifest = register_image(config)
    target = "https://pkg-containers.githubusercontent.com/ghcr1/blobs/config"
    respx.get(f"{BASE}/blobs/{digest(config)}").respond(307, headers={"Location": target})
    respx.get(target).respond(200, content=config + b" ")
    with pytest.raises(RegistryError, match="integrity"):
        check_image(image_name(manifest))
