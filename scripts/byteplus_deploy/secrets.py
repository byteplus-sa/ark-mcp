"""Pinned operator-side BytePlus generic-secret reads without application imports."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from .cloud import Cloud
from .private import PRODUCT_KEYS
from .workspace import read_private

if TYPE_CHECKING:
    from pathlib import Path


def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON contains duplicate fields.")
        result[key] = value
    return result


@dataclass(frozen=True)
class SecretReference:
    """An explicit nonsecret account/project/name/version binding."""

    region: str
    account_id: str
    project_name: str
    secret_name: str
    version_id: str

    @classmethod
    def load(cls, path: Path) -> SecretReference:
        try:
            values = json.loads(read_private(path), object_pairs_hook=unique_object)
            reference = cls(**values)
            reference.validate()
            return reference
        except (ValueError, TypeError, UnicodeError):
            raise ValueError(
                "Secret reference is invalid; use only the required nonsecret fields."
            ) from None

    def validate(self) -> None:
        patterns = {
            "region": r"ap-southeast-1",
            "account_id": r"[1-9][0-9]{0,19}",
            "project_name": r"[A-Za-z0-9_.-]{1,128}",
            "secret_name": r"[A-Za-z0-9_-]{2,31}",
            "version_id": r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}",
        }
        for name, pattern in patterns.items():
            value = getattr(self, name)
            if not isinstance(value, str) or not re.fullmatch(pattern, value):
                raise ValueError(
                    "Secret reference requires an allowlisted region and exact version."
                )
        if self.project_name in {".", ".."}:
            raise ValueError("Secret project is invalid.")

    def metadata(self) -> dict[str, str]:
        return asdict(self)


def parse_bundle(value: str) -> dict[str, str]:
    """Parse only product keys; absent or empty entries intentionally disable products."""
    try:
        if not isinstance(value, str) or len(value.encode()) > 30720:
            raise ValueError
        supplied = json.loads(value, object_pairs_hook=unique_object)
        if not isinstance(supplied, dict) or set(supplied) - PRODUCT_KEYS:
            raise ValueError
        for key in supplied:
            token = supplied[key]
            if (
                not isinstance(token, str)
                or len(token) > 8192
                or any(ord(character) < 33 or ord(character) > 126 for character in token)
                or any(character in token for character in "$`#\"'\\")
            ):
                raise ValueError
        if not any(supplied.values()):
            raise ValueError
        return {key: supplied.get(key, "") for key in sorted(PRODUCT_KEYS)}
    except (TypeError, ValueError, UnicodeError):
        raise ValueError(
            "Secret value must be a bounded JSON bundle containing only literal product API keys."
        ) from None


def fetch_provider_keys(
    reference: SecretReference, *, cloud: Cloud | None = None
) -> dict[str, str]:
    """Verify exact generic-secret identity before reading its explicitly pinned version."""
    reference.validate()
    owns_client = cloud is None
    client = Cloud(reference.region) if cloud is None else cloud
    try:
        if client.region != reference.region:
            raise ValueError("Secret region does not match its API client.")
        result = client.request("kms", "DescribeSecret", {"SecretName": reference.secret_name})
        secret = result.get("Secret")
        if (
            not isinstance(secret, dict)
            or any(
                secret.get(field) != expected
                for field, expected in (
                    ("SecretName", reference.secret_name),
                    ("UID", reference.account_id),
                    ("ProjectName", reference.project_name),
                    ("SecretType", "Generic"),
                )
            )
            or secret.get("Managed") is not False
        ):
            raise ValueError(
                "Secret identity does not match the approved generic-secret reference."
            )
        result = client.request(
            "kms",
            "GetSecretValue",
            {"SecretName": reference.secret_name, "VersionID": reference.version_id},
        )
        if result.get("VersionID") != reference.version_id:
            raise ValueError("Secret response does not match the pinned version.")
        return parse_bundle(result.get("SecretValue"))  # type: ignore[arg-type]
    finally:
        if owns_client:
            client.close()
