from __future__ import annotations

import base64
import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from scripts.byteplus_deploy.cloud import REGION_ENDPOINTS, Cloud, CloudError

REGION = "ap-southeast-1"
ZONE = f"{REGION}a"
_SIGNING_VALUE = "test-signing-value"
ENVIRONMENT = {
    "BYTEPLUS_ACCESS_KEY": "test-access",
    "BYTEPLUS_SECRET_KEY": _SIGNING_VALUE,
    "BYTEPLUS_SESSION_TOKEN": "test-session-value",
}
CONFIG: dict[str, Any] = {
    "name": "ark-test",
    "region": REGION,
    "zone_id": ZONE,
    "image_id": "image-test",
    "instance_type": "ecs.g3i.large",
    "key_pair_name": "test-keypair",
    "create_vpc": False,
    "existing_vpc_id": "vpc-test",
    "existing_subnet_id": "subnet-test",
    "data_disk_size_gib": 40,
    "volume_type": "ESSD_PL0",
    "project_name": "default",
}


def cloud_with(handler: Callable[[httpx.Request], httpx.Response]) -> Cloud:
    return Cloud(REGION, ENVIRONMENT, httpx.Client(transport=httpx.MockTransport(handler)))


def result_cloud(result: dict[str, Any]) -> Cloud:
    return cloud_with(lambda _: httpx.Response(200, json={"Result": result}))


def invocation(**changes: Any) -> dict[str, Any]:
    return {
        "InstanceId": "i-test",
        "InvocationId": "inv-test",
        "InvocationResultStatus": "Success",
        "ExitCode": 0,
        "Output": base64.b64encode(b"ready\n").decode(),
        **changes,
    }


def test_request_signs_allowed_regional_get_without_logging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"ResponseMetadata": {}, "Result": {"ok": True}})

    with caplog.at_level(logging.DEBUG):
        assert cloud_with(handler).request(
            "ecs", "DescribeInstances", {"InstanceIds.1": "i-test"}
        ) == {"ok": True}
    request = seen[0]
    assert request.method == "GET"
    assert request.url.host == "ecs.ap-southeast-1.byteplusapi.com"
    assert request.url.params["Version"] == "2020-04-01"
    assert request.headers["X-Security-Token"] == ENVIRONMENT["BYTEPLUS_SESSION_TOKEN"]
    assert f"/{REGION}/ecs/request" in request.headers["Authorization"]
    assert not caplog.text


@pytest.mark.parametrize("region", ["", "../evil", "https://example.com", "unlisted-region"])
def test_region_allowlist_rejects_unknown_regions(region: str) -> None:
    with pytest.raises(CloudError):
        Cloud(region, ENVIRONMENT)


def test_region_endpoint_allowlist_has_only_expected_service_hosts() -> None:
    for region, services in REGION_ENDPOINTS.items():
        expected = {"ecs", "vpc", "storage_ebs"}
        if region == "ap-southeast-1":
            expected.add("kms")
        assert set(services) == expected
        for service, host in services.items():
            assert host == f"{service.replace('_', '-')}.{region}.byteplusapi.com"


@pytest.mark.parametrize(
    ("service", "action", "parameters"),
    [
        ("unknown", "DescribeInstances", {}),
        ("ecs", "DeleteInstances", {}),
        ("ecs", "RunCommand", {}),
        ("ecs", "DescribeInstances", {"Action": "DeleteInstances"}),
        ("ecs", "DescribeInstances", {"Version": "other"}),
        ("ecs", "DescribeInstances", {"InstanceIds.1": 12}),
    ],
)
def test_request_rejects_unapproved_operations_and_parameters(
    service: str, action: str, parameters: dict[str, Any]
) -> None:
    with pytest.raises(CloudError):
        result_cloud({}).request(service, action, parameters)


def test_explicit_empty_environment_does_not_fall_back_to_shell(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key, value in ENVIRONMENT.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(CloudError):
        Cloud(REGION, {})


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, json={"Message": "test-session-value"}),
        httpx.Response(
            200, json={"ResponseMetadata": {"Error": {"Message": "test-session-value"}}}
        ),
        httpx.Response(200, text="test-session-value"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"Result": []}),
        httpx.Response(302, headers={"Location": "https://example.com"}),
    ],
)
def test_request_errors_are_credential_free(response: httpx.Response) -> None:
    with pytest.raises(CloudError) as caught:
        cloud_with(lambda _: response).request("ecs", "DescribeInstances", {})
    assert "test-session-value" not in str(caught.value)
    assert "https:" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_run_command_submits_once_with_root_shell_and_bounded_timeout() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"Result": {"InvocationId": "inv-test"}})

    assert cloud_with(handler).run_command("i-test", "true\n", "installer", 1800) == "inv-test"
    assert len(seen) == 1
    query = seen[0].url.params
    assert query["Action"] == "RunCommand"
    assert query["Type"] == "Shell"
    assert query["Username"] == "root"
    assert query["InvocationName"] == "installer"
    assert query["InstanceIds.1"] == "i-test"
    assert query["Timeout"] == "1800"
    assert base64.b64decode(query["CommandContent"]) == b"true\n"
    assert query["ContentEncoding"] == "Base64"


def test_run_command_ambiguous_timeout_is_not_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("private-command-url", request=request)

    with pytest.raises(CloudError, match="acceptance"):
        cloud_with(handler).run_command("i-test", "true", "installer")
    assert calls == 1


@pytest.mark.parametrize(
    ("command", "name", "timeout"),
    [("", "test", 600), ("true", "", 600), ("true", "test", 0), ("true", "test", True)],
)
def test_run_command_rejects_invalid_input(command: str, name: str, timeout: int) -> None:
    with pytest.raises(CloudError):
        result_cloud({}).run_command("i-test", command, name, timeout)


@pytest.mark.parametrize("payload", [{}, {"InvocationResults": []}])
def test_missing_invocation_results_are_working(payload: dict[str, Any]) -> None:
    assert result_cloud(payload).get_result("i-test", "inv-test") == {
        "status": "working",
        "exit_code": None,
        "output": "",
        "error_code": None,
    }


def test_success_requires_exact_identity_and_zero_exit_code() -> None:
    assert result_cloud({"InvocationResults": [invocation()]}).get_result("i-test", "inv-test") == {
        "status": "succeeded",
        "exit_code": 0,
        "output": "ready\n",
        "error_code": None,
    }


@pytest.mark.parametrize(
    ("status", "exit_code", "expected"),
    [
        ("Pending", None, "working"),
        ("Running", None, "working"),
        ("Success", 1, "failed"),
        ("Success", None, "failed"),
        ("Success", False, "failed"),
        ("Failed", 0, "failed"),
        ("Timeout", None, "failed"),
        ("Cancelled", None, "failed"),
        ("Stopped", None, "failed"),
        ("Unexpected", 0, "failed"),
    ],
)
def test_provider_status_normalization(status: str, exit_code: Any, expected: str) -> None:
    record = invocation(InvocationResultStatus=status, ExitCode=exit_code)
    assert (
        result_cloud({"InvocationResults": [record]}).get_result("i-test", "inv-test")["status"]
        == expected
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"InstanceId": "i-other", "Output": "invalid"},
        {"InstanceId": None},
        {"InvocationId": "inv-other"},
        {"Output": "%%%"},
        {"Output": "YQ"},
        {"Output": "YQ==\n"},
        {"Output": "YR=="},
        {"Output": base64.b64encode(b"\xff").decode()},
        {"Output": None},
        {"OutputTruncated": True},
        {"Truncated": True},
        {"Dropped": 1},
        {"InvocationResultStatus": []},
    ],
)
def test_mismatched_or_malformed_results_are_rejected(changes: dict[str, Any]) -> None:
    with pytest.raises(CloudError):
        result_cloud({"InvocationResults": [invocation(**changes)]}).get_result(
            "i-test", "inv-test"
        )


def test_duplicate_invocation_records_are_rejected() -> None:
    with pytest.raises(CloudError):
        result_cloud({"InvocationResults": [invocation(), invocation()]}).get_result(
            "i-test", "inv-test"
        )


def inventory() -> dict[str, Any]:
    return {
        "DescribeImages": {
            "Images": [
                {
                    "ImageId": "image-test",
                    "Architecture": "x86_64",
                    "Platform": "Ubuntu",
                    "PlatformVersion": "24.04",
                    "OsType": "Linux",
                    "Status": "available",
                    "IsSupportCloudInit": True,
                }
            ]
        },
        "DescribeAvailableResource": {
            "AvailableZones": [
                {
                    "ZoneId": ZONE,
                    "Status": "Available",
                    "AvailableResources": [
                        {
                            "Type": "InstanceType",
                            "SupportedResources": [
                                {"Value": CONFIG["instance_type"], "Status": "Available"}
                            ],
                        }
                    ],
                }
            ]
        },
        "DescribeVolumeType": {"VolumeTypes": [{"Id": "ESSD_PL0", "Zones": [ZONE]}]},
        "DescribeKeyPairs": {"KeyPairs": [{"KeyPairName": "test-keypair"}]},
        "DescribeVpcs": {"Vpcs": [{"VpcId": "vpc-test"}]},
        "DescribeEipAddresses": {"EipAddresses": []},
        "DescribeSubnets": {
            "Subnets": [{"SubnetId": "subnet-test", "VpcId": "vpc-test", "ZoneId": ZONE}]
        },
        "DescribeInstances": {
            "Instances": [
                {
                    "InstanceId": "i-test",
                    "InstanceName": "ark-test",
                    "ZoneId": ZONE,
                    "VpcId": "vpc-test",
                    "NetworkInterfaces": [
                        {"Type": "primary", "SubnetId": "subnet-test", "VpcId": "vpc-test"}
                    ],
                    "Volumes": [{"VolumeId": "vol-test"}],
                }
            ]
        },
        "DescribeVolumes": {
            "Volumes": [
                {
                    "VolumeId": "vol-test",
                    "InstanceId": "i-test",
                    "VolumeName": "ark-test-state",
                    "ZoneId": ZONE,
                    "Kind": "data",
                    "Size": 40,
                    "VolumeType": "ESSD_PL0",
                    "Status": "attached",
                    "DeleteWithInstance": False,
                }
            ]
        },
    }


def inventory_cloud(payload: dict[str, Any], seen: list[str]) -> Cloud:
    def handler(request: httpx.Request) -> httpx.Response:
        action = request.url.params["Action"]
        seen.append(action)
        return httpx.Response(200, json={"Result": payload[action]})

    return cloud_with(handler)


def test_preflight_checks_explicit_inventory_without_mutation() -> None:
    seen: list[str] = []
    warnings = inventory_cloud(inventory(), seen).preflight(CONFIG)
    assert all(action.startswith("Describe") for action in seen)
    assert set(seen) == {
        "DescribeImages",
        "DescribeAvailableResource",
        "DescribeVolumeType",
        "DescribeKeyPairs",
        "DescribeVpcs",
        "DescribeEipAddresses",
        "DescribeSubnets",
    }
    assert any("quota" in warning.lower() for warning in warnings)


@pytest.mark.parametrize(
    ("action", "collection", "field", "value"),
    [
        ("DescribeImages", "Images", "Architecture", "arm64"),
        ("DescribeImages", "Images", "PlatformVersion", "22.04"),
        ("DescribeImages", "Images", "ImageId", "image-other"),
        ("DescribeImages", "Images", "IsSupportCloudInit", False),
        ("DescribeImages", "Images", "Status", "creating"),
        ("DescribeKeyPairs", "KeyPairs", "KeyPairName", "other-key"),
        ("DescribeSubnets", "Subnets", "ZoneId", "other-zone"),
        ("DescribeSubnets", "Subnets", "SubnetId", "subnet-other"),
        ("DescribeSubnets", "Subnets", "VpcId", "vpc-other"),
        ("DescribeVolumeType", "VolumeTypes", "Zones", []),
    ],
)
def test_preflight_rejects_unavailable_or_mismatched_resources(
    action: str, collection: str, field: str, value: Any
) -> None:
    payload = inventory()
    payload[action][collection][0][field] = value
    with pytest.raises(CloudError):
        inventory_cloud(payload, []).preflight(CONFIG)


def test_preflight_rejects_sold_out_instance_type() -> None:
    payload = inventory()
    payload["DescribeAvailableResource"]["AvailableZones"][0]["AvailableResources"][0][
        "SupportedResources"
    ][0]["Status"] = "SoldOut"
    with pytest.raises(CloudError):
        inventory_cloud(payload, []).preflight(CONFIG)


def test_verify_instance_queries_both_instance_and_storage_identity() -> None:
    seen: list[str] = []
    inventory_cloud(inventory(), seen).verify_instance("i-test", "vol-test", CONFIG)
    assert {"DescribeInstances", "DescribeVolumes"}.issubset(seen)
    assert all(action.startswith("Describe") for action in seen)


@pytest.mark.parametrize("size", [40, "40"])
def test_verify_instance_accepts_provider_integer_and_numeric_string_disk_sizes(
    size: int | str,
) -> None:
    payload = inventory()
    payload["DescribeVolumes"]["Volumes"][0]["Size"] = size
    inventory_cloud(payload, []).verify_instance("i-test", "vol-test", CONFIG)


@pytest.mark.parametrize("size", [None, True, 40.0, "040", "40.0", " 40", "100", [], {}])
def test_verify_instance_rejects_malformed_or_mismatched_disk_sizes(size: Any) -> None:
    payload = inventory()
    payload["DescribeVolumes"]["Volumes"][0]["Size"] = size
    with pytest.raises(CloudError):
        inventory_cloud(payload, []).verify_instance("i-test", "vol-test", CONFIG)


@pytest.mark.parametrize(
    ("action", "collection", "field", "value"),
    [
        ("DescribeInstances", "Instances", "InstanceId", "i-other"),
        ("DescribeInstances", "Instances", "InstanceName", "other"),
        ("DescribeInstances", "Instances", "ZoneId", "other"),
        ("DescribeInstances", "Instances", "VpcId", "vpc-other"),
        ("DescribeInstances", "Instances", "NetworkInterfaces", []),
        ("DescribeInstances", "Instances", "Volumes", [{"VolumeId": "vol-other"}]),
        ("DescribeVolumes", "Volumes", "VolumeId", "vol-other"),
        ("DescribeVolumes", "Volumes", "InstanceId", "i-other"),
        ("DescribeVolumes", "Volumes", "VolumeName", "other-state"),
        ("DescribeVolumes", "Volumes", "ZoneId", "other"),
        ("DescribeVolumes", "Volumes", "Kind", "system"),
        ("DescribeVolumes", "Volumes", "Size", 100),
        ("DescribeVolumes", "Volumes", "VolumeType", "ESSD_FlexPL"),
        ("DescribeVolumes", "Volumes", "Status", "available"),
        ("DescribeVolumes", "Volumes", "Status", "in-use"),
        ("DescribeVolumes", "Volumes", "DeleteWithInstance", True),
    ],
)
def test_verify_instance_rejects_wrong_attachment_or_identity(
    action: str, collection: str, field: str, value: Any
) -> None:
    payload = inventory()
    payload[action][collection][0][field] = value
    with pytest.raises(CloudError):
        inventory_cloud(payload, []).verify_instance("i-test", "vol-test", CONFIG)


def test_verify_created_network_matches_name_cidr_and_primary_subnet() -> None:
    payload = inventory()
    payload["DescribeVpcs"]["Vpcs"][0].update(VpcName="ark-test-vpc", CidrBlock="10.42.0.0/16")
    payload["DescribeSubnets"]["Subnets"][0].update(
        SubnetName="ark-test-subnet", CidrBlock="10.42.0.0/24"
    )
    config = {
        **CONFIG,
        "create_vpc": True,
        "existing_vpc_id": None,
        "existing_subnet_id": None,
        "vpc_cidr": "10.42.0.0/16",
    }
    inventory_cloud(payload, []).verify_instance("i-test", "vol-test", config)
    payload["DescribeSubnets"]["Subnets"][0]["SubnetName"] = "unrelated-subnet"
    with pytest.raises(CloudError):
        inventory_cloud(payload, []).verify_instance("i-test", "vol-test", config)


def test_import_cloud_does_not_initialize_ark_application() -> None:
    root = Path(__file__).resolve().parents[2]
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from scripts.byteplus_deploy.cloud import Cloud; "
            "Cloud('ap-southeast-1', dict.fromkeys("
            "('BYTEPLUS_ACCESS_KEY', 'BYTEPLUS_SECRET_KEY'), 'test')); "
            "assert not any(n == 'ark_mcp' or n.startswith('ark_mcp.') for n in sys.modules)",
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr


def test_request_error_does_not_expose_url_at_debug_level(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        logging.getLogger("httpcore.http11").debug("private-query test-session-value")
        raise httpx.ReadTimeout("test-session-value", request=request)

    with caplog.at_level(logging.DEBUG), pytest.raises(CloudError):
        cloud_with(handler).run_command("i-test", "private-command", "test")
    assert not caplog.text


def test_unknown_provider_error_code_is_not_returned_as_raw_data() -> None:
    payload = {"InvocationResults": [invocation(ErrorCode="test-session-value")]}
    result = result_cloud(payload).get_result("i-test", "inv-test")
    assert result["status"] == "failed"
    assert result["error_code"] == "ProviderError"


def test_terminal_output_at_provider_size_limit_is_rejected() -> None:
    record = invocation(Output=base64.b64encode(b"x" * 65536).decode())
    with pytest.raises(CloudError, match="truncated"):
        result_cloud({"InvocationResults": [record]}).get_result("i-test", "inv-test")


@pytest.mark.parametrize("payload", [{"InvocationResults": None}, {"InvocationResults": [None]}])
def test_malformed_result_records_are_sanitized(payload: dict[str, Any]) -> None:
    with pytest.raises(CloudError):
        result_cloud(payload).get_result("i-test", "inv-test")


def test_truncated_inventory_page_cannot_verify_exact_identity() -> None:
    payload = inventory()
    payload["DescribeInstances"]["NextToken"] = "private-pagination-value"
    with pytest.raises(CloudError):
        inventory_cloud(payload, []).verify_instance("i-test", "vol-test", CONFIG)


@pytest.mark.parametrize(
    "changes", [{"Architecture": []}, {"Platform": {}}, {"PlatformVersion": ["24.04"]}]
)
def test_invalid_image_metadata_is_sanitized(changes: dict[str, Any]) -> None:
    payload = inventory()
    payload["DescribeImages"]["Images"][0].update(changes)
    with pytest.raises(CloudError):
        inventory_cloud(payload, []).preflight(CONFIG)
