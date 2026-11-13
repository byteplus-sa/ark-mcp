"""Read-only GHCR preflight for the workflow in docs/byteplus-terraform.md."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.request import parse_http_list

import httpx

from .cloud import _quiet_http

_DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
_REFERENCE = re.compile(
    r"ghcr\.io/(?P<repository>[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/"
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*)+)@(?P<digest>sha256:[a-f0-9]{64})"
)
_MANIFEST_TYPES = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}
_INDEX_TYPES = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
_CONFIG_TYPES = {
    "application/vnd.oci.image.config.v1+json",
    "application/vnd.docker.container.image.v1+json",
}
_ACCEPT = ", ".join(sorted(_MANIFEST_TYPES | _INDEX_TYPES))
_MAX_DOCUMENT_BYTES = 4 * 1024 * 1024


class RegistryError(RuntimeError):
    """A credential-free image preflight failure."""


def _json_object(content: bytes) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except (ValueError, UnicodeError):
        raise RegistryError("Registry document is malformed.") from None
    if not isinstance(value, dict):
        raise RegistryError("Registry document is malformed.")
    return value


def _descriptor(value: Any, media_types: set[str]) -> tuple[str, int]:
    if not isinstance(value, dict):
        raise RegistryError("Image descriptor is malformed.")
    digest = value.get("digest")
    size = value.get("size")
    if (
        not isinstance(digest, str)
        or not _DIGEST.fullmatch(digest)
        or type(size) is not int
        or not 0 < size <= _MAX_DOCUMENT_BYTES
        or not isinstance(value.get("mediaType"), str)
        or value.get("mediaType") not in media_types
    ):
        raise RegistryError("Image descriptor is unsupported or malformed.")
    return digest, size


def _challenge(header: str, repository: str) -> str:
    scheme, separator, values = header.partition(" ")
    if not separator or scheme.lower() != "bearer":
        raise RegistryError("GHCR authentication challenge is unsupported.")
    fields: dict[str, str] = {}
    for field in parse_http_list(values):
        match = re.fullmatch(r'([a-zA-Z]+)="([^"\\]*)"', field.strip())
        if match is None or match[1].lower() in fields:
            raise RegistryError("GHCR authentication challenge is malformed.")
        fields[match[1].lower()] = match[2]
    if (
        fields.get("realm") != "https://ghcr.io/token"
        or fields.get("service") != "ghcr.io"
        or fields.get("scope") not in {None, f"repository:{repository}:pull"}
        or set(fields) - {"realm", "service", "scope"}
    ):
        raise RegistryError("GHCR authentication endpoint or scope is not trusted.")
    return fields["realm"]


class _Registry:
    def __init__(
        self, client: httpx.Client, repository: str, username: str | None, token: str | None
    ) -> None:
        self.client = client
        self.repository = repository
        self.username = username
        self.token = token
        self.bearer: str | None = None

    def _get(
        self,
        url: str,
        *,
        headers: dict[str, str],
        parameters: dict[str, str] | None = None,
        auth: httpx.BasicAuth | None = None,
        credential_free: bool = False,
    ) -> httpx.Response:
        try:
            with _quiet_http():
                request = self.client.build_request("GET", url, headers=headers, params=parameters)
                if credential_free:
                    request.headers.pop("Authorization", None)
                    request.headers.pop("Cookie", None)
                response = self.client.send(request, auth=auth, follow_redirects=False)
        except httpx.HTTPError:
            raise RegistryError("GHCR request failed; check access and connectivity.") from None
        if len(response.content) > _MAX_DOCUMENT_BYTES:
            raise RegistryError("Registry document exceeds the safe size limit.")
        return response

    def document(self, kind: str, digest: str, size: int | None = None) -> dict[str, Any]:
        url = f"https://ghcr.io/v2/{self.repository}/{kind}/{digest}"
        headers = {"Accept": _ACCEPT} if kind == "manifests" else {}
        if self.bearer is not None:
            headers["Authorization"] = f"Bearer {self.bearer}"
        response = self._get(url, headers=headers)
        if response.status_code == 401 and self.bearer is None:
            realm = _challenge(response.headers.get("WWW-Authenticate", ""), self.repository)
            auth = (
                httpx.BasicAuth(self.username, self.token)
                if self.username is not None and self.token is not None
                else None
            )
            issued = self._get(
                realm,
                headers={},
                auth=auth,
                parameters={"service": "ghcr.io", "scope": f"repository:{self.repository}:pull"},
            )
            if issued.status_code != 200:
                raise RegistryError(
                    "GHCR pull access was denied; provide valid explicit credentials."
                )
            tokens = _json_object(issued.content)
            bearer = tokens.get("token") or tokens.get("access_token")
            if (
                not isinstance(bearer, str)
                or not bearer
                or len(bearer) > 16384
                or any(not 33 <= ord(character) <= 126 for character in bearer)
            ):
                raise RegistryError("GHCR authentication response is malformed.")
            self.bearer = bearer
            headers["Authorization"] = f"Bearer {bearer}"
            response = self._get(url, headers=headers)
        if kind == "blobs":
            for _ in range(2):
                if response.status_code not in {301, 302, 303, 307, 308}:
                    break
                try:
                    target = httpx.URL(response.headers.get("Location", ""))
                except httpx.InvalidURL:
                    raise RegistryError("GHCR blob redirect is malformed.") from None
                if (
                    target.scheme != "https"
                    or target.host != "pkg-containers.githubusercontent.com"
                    or target.port not in {None, 443}
                    or target.userinfo
                    or target.fragment
                ):
                    raise RegistryError("GHCR blob redirect target is not trusted.")
                response = self._get(str(target), headers={}, credential_free=True)
        if response.status_code != 200:
            raise RegistryError("GHCR image retrieval failed; check digest and pull access.")
        content = response.content
        actual = "sha256:" + hashlib.sha256(content).hexdigest()
        declared = response.headers.get("Docker-Content-Digest")
        if actual != digest or (declared is not None and declared != digest):
            raise RegistryError("Registry content failed digest integrity verification.")
        if size is not None and len(content) != size:
            raise RegistryError("Registry content failed size integrity verification.")
        return _json_object(content)


def check_image(image: str, username: str | None = None, token: str | None = None) -> None:
    """Verify a pinned GHCR image's integrity, Linux AMD64 platform and shared-key mode."""
    if not isinstance(image, str) or (reference := _REFERENCE.fullmatch(image)) is None:
        raise RegistryError("Use a GHCR image pinned to a lowercase sha256 digest.")
    if (username is None) != (token is None) or (
        username is not None
        and (
            not isinstance(username, str)
            or not username
            or ":" in username
            or any(not 33 <= ord(character) <= 126 for character in username)
            or not isinstance(token, str)
            or not token
            or any(not 33 <= ord(character) <= 126 for character in token)
        )
    ):
        raise RegistryError("Supply both an explicit registry username and token.")
    with httpx.Client(timeout=30, trust_env=False, follow_redirects=False) as client:
        registry = _Registry(client, reference["repository"], username, token)
        manifest = registry.document("manifests", reference["digest"])
        if type(manifest.get("schemaVersion")) is not int or manifest["schemaVersion"] != 2:
            raise RegistryError("Image manifest schema is unsupported.")
        if not isinstance(manifest.get("mediaType"), str):
            raise RegistryError("Image manifest type is malformed.")
        if manifest.get("mediaType") in _INDEX_TYPES:
            manifests = manifest.get("manifests")
            if not isinstance(manifests, list) or any(
                not isinstance(item, dict) for item in manifests
            ):
                raise RegistryError("Image index is malformed.")
            candidates = [
                item
                for item in manifests
                if isinstance(item.get("platform"), dict)
                and item["platform"].get("os") == "linux"
                and item["platform"].get("architecture") == "amd64"
                and item["platform"].get("variant") in (None, "")
            ]
            if len(candidates) != 1:
                raise RegistryError("Image index must select exactly one Linux AMD64 manifest.")
            child_digest, child_size = _descriptor(candidates[0], _MANIFEST_TYPES)
            manifest = registry.document("manifests", child_digest, child_size)
        if (
            type(manifest.get("schemaVersion")) is not int
            or manifest["schemaVersion"] != 2
            or not isinstance(manifest.get("mediaType"), str)
            or manifest.get("mediaType") not in _MANIFEST_TYPES
        ):
            raise RegistryError("Image manifest type is unsupported.")
        config_digest, config_size = _descriptor(manifest.get("config"), _CONFIG_TYPES)
        config = registry.document("blobs", config_digest, config_size)
        if config.get("os") != "linux" or config.get("architecture") != "amd64":
            raise RegistryError("Image config must declare Linux AMD64.")
        settings = config.get("config")
        environment = settings.get("Env") if isinstance(settings, dict) else None
        if not isinstance(environment, list) or any(
            not isinstance(value, str) for value in environment
        ):
            raise RegistryError("Image config environment is malformed.")
        modes = [value for value in environment if value.startswith("MCP_AUTH_MODE=")]
        if modes != ["MCP_AUTH_MODE=api_key"]:
            raise RegistryError("Image must explicitly default to MCP_AUTH_MODE=api_key.")
