"""Standalone cloud adapter for the workflow in docs/byteplus-terraform.md."""

from __future__ import annotations

import base64
import binascii
import importlib.util
import logging
import os
import re
import sys
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from functools import cache
from ipaddress import IPv4Network
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping
    from types import ModuleType

REGION_ENDPOINTS = {
    region: {
        service: f"{service.replace('_', '-')}.{region}.byteplusapi.com"
        for service in ("ecs", "vpc", "storage_ebs")
    }
    for region in ("ap-southeast-1", "ap-southeast-2", "ap-southeast-3", "ap-singapore-1")
}
REGION_ENDPOINTS["ap-southeast-1"]["kms"] = "kms.ap-southeast-1.byteplusapi.com"
_READ_ACTIONS = {
    "ecs": {
        "DescribeImages",
        "DescribeAvailableResource",
        "DescribeKeyPairs",
        "DescribeInstances",
        "DescribeInvocationResults",
    },
    "vpc": {"DescribeVpcs", "DescribeSubnets", "DescribeEipAddresses"},
    "storage_ebs": {"DescribeVolumeType", "DescribeVolumes"},
    "kms": {"DescribeSecret", "GetSecretValue"},
}
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_OUTPUT_BYTES = 64 * 1024
_KNOWN_ERROR_CODES = {
    "CommandExecutionFailed",
    "InstanceNotRunning",
    "AgentNotInstalled",
    "AgentNotRunning",
    "InvocationTimeout",
    "CommandTimeout",
    "InvalidInstanceId",
    "ExecutionError",
}


class CloudError(RuntimeError):
    """A sanitized cloud validation or request failure."""


class _RequestLogFilter(logging.Filter):
    def __init__(self) -> None:
        super().__init__()
        self.active: ContextVar[bool] = ContextVar("byteplus_sensitive_request", default=False)

    def filter(self, record: logging.LogRecord) -> bool:
        return not self.active.get()


@contextmanager
def _quiet_http() -> Iterator[None]:
    """Suppress HTTP library diagnostics only in this sensitive request context."""
    log_filter = _RequestLogFilter()
    names = {
        "httpx",
        "httpcore",
        "httpcore.connection",
        "httpcore.http11",
        "httpcore.http2",
        "httpcore.proxy",
        "httpcore.socks",
    }
    names.update(
        name
        for name in logging.Logger.manager.loggerDict
        if name.startswith(("httpx.", "httpcore."))
    )
    loggers = [logging.getLogger(name) for name in names]
    for logger in loggers:
        logger.addFilter(log_filter)
    context_token = log_filter.active.set(True)
    try:
        yield
    finally:
        log_filter.active.reset(context_token)
        for logger in loggers:
            logger.removeFilter(log_filter)


@cache
def _signer() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "src/ark_mcp/providers/byteplus_openapi/signing.py"
    name = "_byteplus_deploy_signing"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CloudError("Standalone cloud signer is unavailable.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _records(result: dict[str, Any], key: str) -> list[dict[str, Any]]:
    records = result.get(key, [])
    if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
        raise CloudError("Cloud inventory response is malformed.")
    return records


def _one(result: dict[str, Any], key: str, identity_key: str, identity: str) -> dict[str, Any]:
    records = _records(result, key)
    if len(records) != 1 or records[0].get(identity_key) != identity or result.get("NextToken"):
        raise CloudError("Cloud resource identity could not be verified.")
    return records[0]


def _text(config: Mapping[str, Any], name: str) -> str:
    value = config.get(name)
    if not isinstance(value, str) or not value.strip():
        raise CloudError("Deployment configuration is incomplete.")
    return value


def _identifier(value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise CloudError("Cloud resource identifier is invalid.")


class Cloud:
    """Signed, single-attempt control-plane calls without application imports."""

    def __init__(
        self,
        region: str,
        environment: Mapping[str, str] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if region not in REGION_ENDPOINTS:
            raise CloudError("Infrastructure region is not allowlisted.")
        environment = os.environ if environment is None else environment
        access = environment.get("BYTEPLUS_ACCESS_KEY", "")
        secret = environment.get("BYTEPLUS_SECRET_KEY", "")
        session = environment.get("BYTEPLUS_SESSION_TOKEN", "")
        if not all(isinstance(value, str) for value in (access, secret, session)):
            raise CloudError("Cloud credentials must be supplied through environment variables.")
        if not access.strip() or not secret.strip():
            raise CloudError("BYTEPLUS_ACCESS_KEY and BYTEPLUS_SECRET_KEY are required.")
        self.region = region
        self._credentials = _signer().OpenApiCredentials(access, secret, session)
        self._owns_client = client is None
        self._client = (
            client
            if client is not None
            else httpx.Client(timeout=30, follow_redirects=False, trust_env=False)
        )

    def close(self) -> None:
        """Close an internally allocated HTTP client."""
        if self._owns_client:
            self._client.close()

    def request(self, service: str, action: str, parameters: dict[str, str]) -> dict[str, Any]:
        """Issue one allowlisted read; mutations require an explicit run_command call."""
        if action not in _READ_ACTIONS.get(service, set()):
            raise CloudError("Cloud operation is not allowlisted.")
        return self._request(service, action, parameters)

    def _request(self, service: str, action: str, parameters: dict[str, str]) -> dict[str, Any]:
        if (
            service not in REGION_ENDPOINTS[self.region]
            or not isinstance(parameters, dict)
            or any(
                not isinstance(key, str)
                or not isinstance(value, str)
                or key in {"Action", "Version"}
                or not re.fullmatch(r"[A-Za-z][A-Za-z0-9.]*", key)
                for key, value in parameters.items()
            )
        ):
            raise CloudError("Cloud request parameters are invalid.")
        host = REGION_ENDPOINTS[self.region][service]
        version = "2021-02-18" if service == "kms" else "2020-04-01"
        query = {"Action": action, "Version": version, **parameters}
        headers = _signer().sign_request(
            credentials=self._credentials,
            method="GET",
            host=host,
            query=query,
            payload=b"",
            region=self.region,
            service=service,
            now=datetime.now(UTC),
        )
        try:
            with _quiet_http():
                response = self._client.get(
                    f"https://{host}/",
                    params=query,
                    headers=headers,
                    follow_redirects=False,
                    auth=None,
                )
            if response.status_code != 200 or len(response.content) > _MAX_RESPONSE_BYTES:
                raise CloudError("Cloud request failed.")
            body = response.json()
        except (httpx.HTTPError, ValueError):
            message = (
                "Guest command acceptance is unknown; reconcile before resubmission."
                if action == "RunCommand"
                else "Cloud request failed."
            )
            raise CloudError(message) from None
        if not isinstance(body, dict):
            raise CloudError("Cloud response is malformed.")
        metadata = body.get("ResponseMetadata", {})
        if not isinstance(metadata, dict) or metadata.get("Error"):
            raise CloudError("Cloud request was rejected; check access and resource availability.")
        result = body.get("Result")
        if not isinstance(result, dict):
            raise CloudError("Cloud response is malformed.")
        return result

    def _validate_config(self, config: dict[str, Any]) -> None:
        if not isinstance(config, dict) or config.get("region") != self.region:
            raise CloudError("Deployment region does not match the cloud adapter.")
        if type(config.get("create_vpc")) is not bool:
            raise CloudError("Deployment network selection must be explicit.")
        if not _text(config, "zone_id").startswith(self.region):
            raise CloudError("Deployment zone does not belong to the selected region.")

    def preflight(self, config: dict[str, Any]) -> list[str]:
        """Read selected image, compute, storage, keypair and network access evidence."""
        self._validate_config(config)
        image_id = _text(config, "image_id")
        image = _one(
            self.request("ecs", "DescribeImages", {"ImageIds.1": image_id}),
            "Images",
            "ImageId",
            image_id,
        )
        if (
            image.get("Architecture") not in ("x86_64", "amd64")
            or image.get("Platform") != "Ubuntu"
            or not re.fullmatch(r"24\.04(?:\.\d+)?", str(image.get("PlatformVersion", "")))
            or image.get("OsType") != "Linux"
            or image.get("Status") != "available"
            or image.get("IsSupportCloudInit") is not True
        ):
            raise CloudError("Select an available Ubuntu 24.04 AMD64 image with cloud-init.")
        zone = _text(config, "zone_id")
        instance_type = _text(config, "instance_type")
        available = self.request(
            "ecs",
            "DescribeAvailableResource",
            {
                "ZoneId": zone,
                "DestinationResource": "InstanceType",
                "InstanceType": instance_type,
                "InstanceChargeType": "PostPaid",
                "SpotStrategy": "NoSpot",
            },
        )
        matched_zone = _one(available, "AvailableZones", "ZoneId", zone)
        resources = _records(matched_zone, "AvailableResources")
        if matched_zone.get("Status") != "Available" or not any(
            resource.get("Type") == "InstanceType"
            and any(
                item.get("Value") == instance_type and item.get("Status") == "Available"
                for item in _records(resource, "SupportedResources")
            )
            for resource in resources
        ):
            raise CloudError("Selected instance type is unavailable in this zone.")
        volume_type = _text(config, "volume_type")
        types = self.request(
            "storage_ebs",
            "DescribeVolumeType",
            {
                "ZoneId": zone,
                "VolumeTypeIds.1": volume_type,
                "PageSize": "100",
            },
        )
        volume = _one(types, "VolumeTypes", "Id", volume_type)
        if not isinstance(volume.get("Zones"), list) or zone not in volume["Zones"]:
            raise CloudError("Selected storage type is unavailable in this zone.")
        keypair = _text(config, "key_pair_name")
        _one(
            self.request("ecs", "DescribeKeyPairs", {"KeyPairNames.1": keypair}),
            "KeyPairs",
            "KeyPairName",
            keypair,
        )
        self.request("vpc", "DescribeVpcs", {"PageSize": "1"})
        self.request("vpc", "DescribeEipAddresses", {"PageSize": "1"})
        if not config["create_vpc"]:
            self._subnet(_text(config, "existing_subnet_id"), config)
        return [
            "Inventory access and current availability do not guarantee quota or future capacity.",
            "Require a successful ECS-agent probe and installer checks before transferring secrets.",
        ]

    def run_command(self, instance_id: str, command: str, name: str, timeout: int = 600) -> str:
        """Submit exactly one root Shell invocation; never retry ambiguous acceptance."""
        _identifier(instance_id)
        _identifier(name)
        if (
            not isinstance(command, str)
            or not command.strip()
            or "\x00" in command
            or len(command.encode()) > 12 * 1024
            or type(timeout) is not int
            or not 1 <= timeout <= 86400
        ):
            raise CloudError("Guest command or timeout is invalid.")
        result = self._request(
            "ecs",
            "RunCommand",
            {
                "InstanceIds.1": instance_id,
                "CommandContent": base64.b64encode(command.encode()).decode(),
                "ContentEncoding": "Base64",
                "InvocationName": name,
                "Type": "Shell",
                "Username": "root",
                "Timeout": str(timeout),
            },
        )
        invocation_id = result.get("InvocationId")
        if not isinstance(invocation_id, str) or not invocation_id:
            raise CloudError("Guest command acceptance is unknown; reconcile before resubmission.")
        _identifier(invocation_id)
        return invocation_id

    def get_result(self, instance_id: str, invocation_id: str) -> dict[str, Any]:
        """Normalize one exact instance invocation without polling or replay."""
        _identifier(instance_id)
        _identifier(invocation_id)
        result = self.request(
            "ecs",
            "DescribeInvocationResults",
            {
                "InstanceId": instance_id,
                "InvocationId": invocation_id,
                "PageSize": "100",
            },
        )
        records = _records(result, "InvocationResults")
        working = {"status": "working", "exit_code": None, "output": "", "error_code": None}
        if not records:
            return working
        record = _one(result, "InvocationResults", "InstanceId", instance_id)
        if record.get("InvocationId") != invocation_id:
            raise CloudError("Guest invocation identity does not match.")
        if any(record.get(flag) for flag in ("OutputTruncated", "Truncated", "Dropped")):
            raise CloudError("Guest output is truncated.")
        output = record.get("Output", "")
        if not isinstance(output, str) or len(output) > ((_MAX_OUTPUT_BYTES + 2) // 3) * 4:
            raise CloudError("Guest output is malformed or truncated.")
        try:
            decoded = base64.b64decode(output, validate=True)
            if base64.b64encode(decoded).decode() != output or len(decoded) >= _MAX_OUTPUT_BYTES:
                raise CloudError("Guest output is malformed or truncated.")
            text = decoded.decode("utf-8")
        except (binascii.Error, ValueError):
            raise CloudError("Guest output is malformed or truncated.") from None
        provider_status = record.get("InvocationResultStatus")
        if not isinstance(provider_status, str):
            raise CloudError("Guest invocation status is malformed.")
        exit_code = record.get("ExitCode")
        exit_code = exit_code if type(exit_code) is int else None
        error = record.get("ErrorCode")
        error_code = (
            (error if isinstance(error, str) and error in _KNOWN_ERROR_CODES else "ProviderError")
            if error
            else None
        )
        status = "failed"
        if provider_status in {"Pending", "Scheduled", "Running", "Stopping"}:
            status = "working"
        elif provider_status == "Success" and exit_code == 0 and error_code is None:
            status = "succeeded"
        return {"status": status, "exit_code": exit_code, "output": text, "error_code": error_code}

    def _subnet(self, subnet_id: str, config: dict[str, Any]) -> dict[str, Any]:
        subnet = _one(
            self.request("vpc", "DescribeSubnets", {"SubnetIds.1": subnet_id}),
            "Subnets",
            "SubnetId",
            subnet_id,
        )
        if subnet.get("ZoneId") != _text(config, "zone_id"):
            raise CloudError("Subnet does not belong to the selected zone.")
        if not config["create_vpc"]:
            if subnet.get("VpcId") != _text(config, "existing_vpc_id"):
                raise CloudError("Subnet does not belong to the selected VPC.")
            return subnet
        vpc_id = subnet.get("VpcId")
        if not isinstance(vpc_id, str) or not vpc_id:
            raise CloudError("Subnet VPC identity is missing.")
        vpc = _one(
            self.request("vpc", "DescribeVpcs", {"VpcIds.1": vpc_id}), "Vpcs", "VpcId", vpc_id
        )
        name = _text(config, "name")
        try:
            network = IPv4Network(_text(config, "vpc_cidr"))
        except ValueError:
            raise CloudError("Deployment VPC CIDR is invalid.") from None
        expected_subnet = str(IPv4Network((network.network_address, 24)))
        if (
            vpc.get("VpcName") != f"{name}-vpc"
            or vpc.get("CidrBlock") != str(network)
            or subnet.get("SubnetName") != f"{name}-subnet"
            or subnet.get("CidrBlock") != expected_subnet
        ):
            raise CloudError("Created network does not match deployment configuration.")
        return subnet

    def verify_instance(self, instance_id: str, volume_id: str, config: dict[str, Any]) -> None:
        """Verify exact instance, primary network and retained disk attachment identities."""
        self._validate_config(config)
        _identifier(instance_id)
        _identifier(volume_id)
        instance = _one(
            self.request("ecs", "DescribeInstances", {"InstanceIds.1": instance_id}),
            "Instances",
            "InstanceId",
            instance_id,
        )
        name = _text(config, "name")
        zone = _text(config, "zone_id")
        if instance.get("InstanceName") != name or instance.get("ZoneId") != zone:
            raise CloudError("Instance name or zone does not match deployment configuration.")
        primary = [
            interface
            for interface in _records(instance, "NetworkInterfaces")
            if interface.get("Type") == "primary"
        ]
        if len(primary) != 1 or not isinstance(primary[0].get("SubnetId"), str):
            raise CloudError("Instance primary subnet could not be verified.")
        subnet_id = primary[0]["SubnetId"]
        if not config["create_vpc"] and subnet_id != _text(config, "existing_subnet_id"):
            raise CloudError("Instance subnet does not match deployment configuration.")
        subnet = self._subnet(subnet_id, config)
        if instance.get("VpcId") != subnet.get("VpcId") or primary[0].get("VpcId") != subnet.get(
            "VpcId"
        ):
            raise CloudError("Instance VPC does not match deployment configuration.")
        volumes = _records(instance, "Volumes")
        if sum(volume.get("VolumeId") == volume_id for volume in volumes) != 1:
            raise CloudError("State disk is not attached to the selected instance.")
        volume = _one(
            self.request("storage_ebs", "DescribeVolumes", {"VolumeIds.1": volume_id}),
            "Volumes",
            "VolumeId",
            volume_id,
        )
        volume_size = volume.get("Size")
        if isinstance(volume_size, str) and re.fullmatch(r"[1-9][0-9]{0,4}", volume_size):
            volume_size = int(volume_size)
        if (
            volume.get("InstanceId") != instance_id
            or volume.get("ZoneId") != zone
            or volume.get("VolumeName") != f"{name}-state"
            or volume.get("Kind") != "data"
            or type(volume_size) is not int
            or volume_size != config.get("data_disk_size_gib")
            or volume.get("VolumeType") != _text(config, "volume_type")
            or volume.get("Status") != "attached"
            or volume.get("DeleteWithInstance") is not False
        ):
            raise CloudError("Retained state disk identity or attachment does not match.")
