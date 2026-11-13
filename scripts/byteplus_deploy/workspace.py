"""Private deployment configuration, files, and single-writer workspace."""

from __future__ import annotations

import fcntl
import hashlib
import ipaddress
import json
import os
import re
import shutil
import stat
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path(__file__).resolve().parents[2]
DEFAULTS: dict[str, Any] = {
    "region": "ap-southeast-1",
    "project_name": "default",
    "create_vpc": True,
    "existing_vpc_id": None,
    "existing_subnet_id": None,
    "admin_cidrs": [],
    "https_cidrs": ["0.0.0.0/0"],
    "public_hostname": None,
    "vpc_cidr": "10.42.0.0/16",
    "data_disk_size_gib": 40,
    "volume_type": "ESSD_PL0",
    "eip_bandwidth_mbps": 20,
    "instance_deletion_protection": True,
    "tos_bucket_name": None,
}
REQUIRED = {"name", "zone_id", "image_id", "instance_type", "key_pair_name", "ark_mcp_image"}


def validate_config(supplied: dict[str, Any]) -> dict[str, Any]:
    """Validate nonsecret Terraform inputs without echoing rejected values."""
    if not isinstance(supplied, dict) or set(supplied) - (REQUIRED | DEFAULTS.keys()):
        raise ValueError("Configuration contains unsupported fields; never include credentials.")
    if REQUIRED - supplied.keys():
        raise ValueError("Configuration is missing required deployment fields.")
    values = {**DEFAULTS, **supplied}
    patterns = {
        "name": r"[a-z][a-z0-9-]{1,38}[a-z0-9]",
        "region": r"[a-z]+-[a-z]+-[1-9][0-9]*",
        "image_id": r"image-[A-Za-z0-9-]+",
        "instance_type": r"ecs\.[A-Za-z0-9.]+",
        "key_pair_name": r"[A-Za-z0-9_.-]{1,128}",
        "project_name": r"[A-Za-z0-9_.-]{1,128}",
        "ark_mcp_image": r"ghcr\.io/[a-z0-9][a-z0-9./_-]*@sha256:[a-f0-9]{64}",
    }
    for key, pattern in patterns.items():
        if not isinstance(values[key], str) or not re.fullmatch(pattern, values[key]):
            raise ValueError(f"Invalid {key}.")
    if not isinstance(values["zone_id"], str) or not re.fullmatch(
        re.escape(values["region"]) + r"[a-z]", values["zone_id"]
    ):
        raise ValueError("zone_id must belong to the selected region.")
    if type(values["create_vpc"]) is not bool or values["instance_deletion_protection"] is not True:
        raise ValueError("Use boolean create_vpc and keep deletion protection enabled.")
    for key, prefix in (("existing_vpc_id", "vpc"), ("existing_subnet_id", "subnet")):
        if values["create_vpc"]:
            valid = values[key] is None
        else:
            valid = isinstance(values[key], str) and bool(
                re.fullmatch(prefix + r"-[A-Za-z0-9-]+", values[key])
            )
        if not valid:
            raise ValueError(f"Invalid {key} for the selected network mode.")
    hostname = values["public_hostname"]
    if hostname is not None and (
        not isinstance(hostname, str)
        or len(hostname) > 253
        or not re.fullmatch(r"([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}", hostname)
        or hostname == "example.com"
        or hostname.endswith(".example.com")
    ):
        raise ValueError("public_hostname must be null or a real lowercase DNS hostname.")
    for key, minimum, maximum in (
        ("data_disk_size_gib", 40, 65536),
        ("eip_bandwidth_mbps", 1, 200),
    ):
        if type(values[key]) is not int or not minimum <= values[key] <= maximum:
            raise ValueError(f"Invalid {key}.")
    if values["volume_type"] not in ("ESSD_PL0", "ESSD_FlexPL"):
        raise ValueError("Unsupported volume_type.")
    if values["tos_bucket_name"] is not None:
        raise ValueError(
            "Optional object storage is an operator workflow, not supported by this CLI."
        )
    for key in ("admin_cidrs", "https_cidrs"):
        if not isinstance(values[key], list) or not all(isinstance(v, str) for v in values[key]):
            raise ValueError(f"{key} must be an IPv4 CIDR list.")
        if key == "https_cidrs" and not values[key]:
            raise ValueError("https_cidrs cannot be empty.")
        for cidr in values[key]:
            try:
                network = ipaddress.IPv4Network(cidr)
            except (ValueError, TypeError):
                raise ValueError(f"Invalid {key}.") from None
            if key == "admin_cidrs" and network.prefixlen < 24:
                raise ValueError("SSH sources must be /24 or narrower.")
    try:
        network = ipaddress.IPv4Network(values["vpc_cidr"])
        private_blocks = [
            ipaddress.IPv4Network(v) for v in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
        ]
        valid = 16 <= network.prefixlen <= 24 and any(network.subnet_of(v) for v in private_blocks)
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise ValueError("vpc_cidr must be RFC1918 IPv4 /16 through /24.")
    return values


def read_private(path: Path, *, limit: int = 65536) -> bytes:
    """Read a same-owner regular 0600 file, never following symlinks or FIFOs."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ValueError("Private inputs must be same-owner regular mode-0600 files.")
        if metadata.st_size > limit:
            raise ValueError("Private input exceeds the supported size.")
        content = stream.read(limit + 1)
        if len(content) > limit:
            raise ValueError("Private input exceeds the supported size.")
        return content


def atomic_private(path: Path, content: bytes) -> None:
    """Replace a private file atomically and fsync its contents and directory."""
    if path.is_symlink():
        raise ValueError("Refusing to replace a symlink.")
    descriptor, temporary = tempfile.mkstemp(prefix=".deploy-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Workspace:
    """An explicitly initialized deployment, never a borrowed state directory."""

    def __init__(self, path: Path) -> None:
        self.path = path.absolute()
        metadata = self.path.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or metadata.st_uid != os.getuid()
        ):
            raise ValueError("Workspace must be a same-owner real mode-0700 directory.")
        self.config = validate_config(json.loads(read_private(self.path / "config.json")))
        self.module = self.path / "source/deploy/terraform/byteplus"
        self.journal()

    @classmethod
    def create(cls, path: Path, config: dict[str, Any]) -> Workspace:
        config = validate_config(config)
        if path.parent.is_symlink():
            raise ValueError("Workspace parent must not be a symlink.")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir(mode=0o700)
        try:
            sources = [
                ROOT / "deploy/compose/compose.yaml",
                ROOT / "deploy/compose/Caddyfile",
                ROOT / "deploy/compose/.env.example",
                ROOT / "scripts/generate_mcp_key.py",
            ]
            module = ROOT / "deploy/terraform/byteplus"
            sources.extend(module.glob("*.tf"))
            sources.append(module / ".terraform.lock.hcl")
            sources.extend((module / "scripts").glob("*.py"))
            sources.extend((module / "scripts").glob("*.sh"))
            sources.extend((module / "scripts").glob("*.service"))
            sources.extend((module / "scripts").glob("*.conf"))
            sources.extend((ROOT / "scripts/byteplus_deploy").glob("*.py"))
            sources.append(ROOT / "src/ark_mcp/providers/byteplus_openapi/signing.py")
            manifest = []
            for source in sources:
                if source.is_symlink() or not source.is_file():
                    raise ValueError("Deployment source must contain only regular files.")
                destination = path / "source" / source.relative_to(ROOT)
                destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                destination.chmod(0o600)
                manifest.append(str(destination.relative_to(path)))
            atomic_private(path / "source-manifest.json", json.dumps(sorted(manifest)).encode())
            atomic_private(path / "config.json", json.dumps(config, sort_keys=True).encode())
            atomic_private(
                path / "journal.json",
                json.dumps({"version": 1, "operations": {}, "phase": "initialized"}).encode(),
            )
            return cls(path)
        except Exception:
            shutil.rmtree(path)
            raise

    def journal(self) -> dict[str, Any]:
        result: dict[str, Any] = json.loads(read_private(self.path / "journal.json", limit=4194304))
        if result.get("version") != 1 or not isinstance(result.get("operations"), dict):
            raise ValueError("Workspace journal is invalid or unsupported.")
        return result

    def save_journal(self, journal: dict[str, Any]) -> None:
        atomic_private(self.path / "journal.json", json.dumps(journal, sort_keys=True).encode())

    def cleanup_temporaries(self) -> None:
        """Remove reserved atomic-write leftovers while the caller holds the workspace lock."""
        paths = list(self.path.glob(".deploy-????????"))
        for path in paths:
            read_private(path, limit=4194304)
        for path in paths:
            path.unlink(missing_ok=True)
        directory = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    @contextmanager
    def lock(self) -> Iterator[None]:
        descriptor = os.open(self.path / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("Deployment workspace is already in use.") from None
            yield
        finally:
            os.close(descriptor)

    def fingerprint(self) -> str:
        digest = hashlib.sha256(read_private(self.path / "config.json"))
        manifest_bytes = read_private(self.path / "source-manifest.json")
        manifest = json.loads(manifest_bytes)
        if (
            not isinstance(manifest, list)
            or not manifest
            or any(
                not isinstance(entry, str)
                or not entry.startswith("source/")
                or ".." in Path(entry).parts
                or Path(entry).is_absolute()
                for entry in manifest
            )
        ):
            raise ValueError("Frozen source manifest is invalid.")
        expected = {entry for entry in manifest if entry.endswith(".tf")}
        actual = {str(path.relative_to(self.path)) for path in self.module.glob("*.tf")}
        if actual != expected:
            raise ValueError("Unexpected or missing Terraform source files.")
        digest.update(manifest_bytes)
        for entry in manifest:
            path = self.path / entry
            relative = Path(entry)
            if any((self.path / parent).is_symlink() for parent in relative.parents):
                raise ValueError("Frozen source path must not contain symlinks.")
            digest.update(entry.encode())
            content = read_private(path, limit=1048576)
            digest.update(content)
            if (
                entry.startswith(("source/scripts/byteplus_deploy/", "source/src/"))
                and (ROOT / relative.relative_to("source")).read_bytes() != content
            ):
                raise ValueError("CLI or signer changed since workspace initialization.")
        return digest.hexdigest()
