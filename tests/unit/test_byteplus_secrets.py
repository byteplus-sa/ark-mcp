from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
from scripts.byteplus_deploy.cloud import Cloud, CloudError
from scripts.byteplus_deploy.secrets import SecretReference, fetch_provider_keys, parse_bundle
from scripts.byteplus_deploy.workspace import atomic_private

REFERENCE = {
    "region": "ap-southeast-1",
    "account_id": "1234567890",
    "project_name": "default",
    "secret_name": "ark-team-providers",
    "version_id": "b58a7e97-3000-4e6e-90c4-c01958333717",
}
BUNDLE = {"BYTEPLUS_MODELARK_API_KEY": "synthetic-provider"}


def reference(tmp_path: Path, **changes: Any) -> SecretReference:
    path = tmp_path / "reference.json"
    atomic_private(path, json.dumps({**REFERENCE, **changes}).encode())
    return SecretReference.load(path)


def response(action: str) -> dict[str, Any]:
    if action == "DescribeSecret":
        return {
            "Secret": {
                "SecretName": REFERENCE["secret_name"],
                "UID": REFERENCE["account_id"],
                "ProjectName": "default",
                "SecretType": "Generic",
                "Managed": False,
            }
        }
    return {"VersionID": REFERENCE["version_id"], "SecretValue": json.dumps(BUNDLE)}


def test_pinned_read_signs_kms_and_suppresses_secret_diagnostics(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        logging.getLogger("httpcore.http11").debug("synthetic-provider")
        return httpx.Response(200, json={"Result": response(request.url.params["Action"])})

    cloud = Cloud(
        "ap-southeast-1",
        {"BYTEPLUS_ACCESS_KEY": "synthetic-access", "BYTEPLUS_SECRET_KEY": "synthetic-signing"},
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with caplog.at_level(logging.DEBUG):
        keys = fetch_provider_keys(reference(tmp_path), cloud=cloud)
    assert keys["BYTEPLUS_MODELARK_API_KEY"] == "synthetic-provider"
    assert keys["BYTEPLUS_CV_API_KEY"] == ""
    assert [item.url.params["Action"] for item in seen] == ["DescribeSecret", "GetSecretValue"]
    assert seen[1].url.params["VersionID"] == REFERENCE["version_id"]
    assert all(item.url.host == "kms.ap-southeast-1.byteplusapi.com" for item in seen)
    assert all(item.url.params["Version"] == "2021-02-18" for item in seen)
    assert "/ap-southeast-1/kms/request" in seen[1].headers["Authorization"]
    assert not caplog.text


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("region", "ap-southeast-2"),
        ("account_id", 123),
        ("project_name", "../escape"),
        ("secret_name", "a"),
        ("secret_name", "a" * 32),
        ("version_id", ""),
        ("version_id", "CURRENT"),
        ("BYTEPLUS_MODELARK_API_KEY", "synthetic-provider"),
    ],
)
def test_reference_rejects_unapproved_or_unpinned_inputs(
    tmp_path: Path, field: str, value: Any
) -> None:
    with pytest.raises(ValueError) as caught:
        reference(tmp_path, **{field: value})
    assert "synthetic-provider" not in str(caught.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("SecretName", "other-secret"),
        ("UID", "other-account"),
        ("ProjectName", "other-project"),
        ("SecretType", "IAM"),
        ("Managed", True),
    ],
)
def test_identity_mismatch_prevents_value_retrieval(tmp_path: Path, field: str, value: Any) -> None:
    seen = []

    class Client:
        region = "ap-southeast-1"

        def request(self, service: str, action: str, parameters: dict[str, str]) -> Any:
            seen.append(action)
            payload = response(action)
            payload["Secret"][field] = value
            return payload

    with pytest.raises(ValueError, match="identity"):
        fetch_provider_keys(reference(tmp_path), cloud=Client())  # type: ignore[arg-type]
    assert seen == ["DescribeSecret"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"VersionID": "other", "SecretValue": json.dumps(BUNDLE)},
        {"VersionID": REFERENCE["version_id"], "SecretValue": "synthetic-provider"},
        {"VersionID": REFERENCE["version_id"], "SecretValue": None},
        {"VersionID": REFERENCE["version_id"], "SecretValue": "x" * 30721},
    ],
)
def test_version_and_value_failures_are_sanitized(tmp_path: Path, payload: Any) -> None:
    class Client:
        region = "ap-southeast-1"

        def request(self, service: str, action: str, parameters: dict[str, str]) -> Any:
            return response(action) if action == "DescribeSecret" else payload

    with pytest.raises(ValueError) as caught:
        fetch_provider_keys(reference(tmp_path), cloud=Client())  # type: ignore[arg-type]
    assert "synthetic-provider" not in str(caught.value)


@pytest.mark.parametrize(
    "value",
    [
        "{}",
        "[]",
        '{"BYTEPLUS_MODELARK_API_KEY": null}',
        '{"BYTEPLUS_MODELARK_API_KEY": true}',
        '{"BYTEPLUS_MODELARK_API_KEY": "a", "BYTEPLUS_MODELARK_API_KEY": "b"}',
        '{"MCP_API_KEY_SHA256": "synthetic-provider"}',
        '{"BYTEPLUS_MODELARK_API_KEY": "line\\nbreak"}',
        '{"BYTEPLUS_MODELARK_API_KEY": "$INTERPOLATION"}',
        '{"BYTEPLUS_MODELARK_API_KEY": " leading-space"}',
        '{"BYTEPLUS_MODELARK_API_KEY": "unicode-\\u00e9"}',
    ],
)
def test_bundle_rejects_empty_wrong_types_duplicates_and_unsafe_values(value: str) -> None:
    with pytest.raises(ValueError) as caught:
        parse_bundle(value)
    assert "synthetic-provider" not in str(caught.value)


def test_reference_json_duplicates_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "reference.json"
    text = json.dumps(REFERENCE).replace('"default"', '"default", "project_name": "other"')
    atomic_private(path, text.encode())
    with pytest.raises(ValueError):
        SecretReference.load(path)


def test_secret_requests_do_not_permit_kms_mutations() -> None:
    cloud = Cloud(
        "ap-southeast-1",
        {"BYTEPLUS_ACCESS_KEY": "synthetic-access", "BYTEPLUS_SECRET_KEY": "synthetic-signing"},
    )
    try:
        with pytest.raises(CloudError):
            cloud.request("kms", "SetSecretValue", {})
    finally:
        cloud.close()
