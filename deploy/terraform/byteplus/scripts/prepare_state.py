"""Explicitly initialize only the identified new state disk, never at boot."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

MOUNT = Path("/srv/ark-mcp")
MARKER = Path("/var/lib/ark-mcp-state.json")


def validate_disk(
    record: dict[str, Any], volume_id: str, size_gib: int, *, allow_existing: bool
) -> None:
    if (
        record.get("serial") not in (volume_id, volume_id.removeprefix("vol-"))
        or record.get("size") != size_gib * 1024**3
        or record.get("type") != "disk"
    ):
        raise ValueError("State disk identity, type or size differs.")
    if record.get("children"):
        raise ValueError("Partitioned state disks require manual recovery.")
    if not allow_existing and (record.get("fstype") or any(record.get("mountpoints") or [])):
        raise ValueError("Refusing to initialize a used disk.")


def validate_signatures(result: dict[str, Any]) -> None:
    if result.get("signatures") != []:
        raise ValueError("Refusing to format a disk with filesystem or partition signatures.")


def output(command: list[str]) -> str:
    return subprocess.check_output(command, text=True).strip()


def prepare(volume_id: str, size_gib: int, name: str) -> str:
    if not re.fullmatch(r"vol-[A-Za-z0-9]+", volume_id):
        raise ValueError("Invalid state volume ID.")
    device = "/dev/disk/by-id/virtio-" + volume_id.removeprefix("vol-")
    disks = json.loads(
        output(
            ["lsblk", "--json", "--bytes", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINTS,SERIAL", device]
        )
    )["blockdevices"]
    if len(disks) != 1:
        raise ValueError("Expected exactly one identified state disk.")
    marker = json.loads(MARKER.read_text()) if MARKER.exists() else None
    if marker and (marker.get("volume_id") != volume_id or marker.get("name") != name):
        raise ValueError("State receipt belongs to another deployment.")
    validate_disk(disks[0], volume_id, size_gib, allow_existing=marker is not None)
    mounted = subprocess.run(["mountpoint", "-q", str(MOUNT)], check=False).returncode == 0
    if marker is None:
        if mounted:
            raise ValueError("Unmanaged state mount requires manual recovery.")
        validate_signatures(json.loads(output(["wipefs", "--json", "--no-act", device])))
        subprocess.run(["mkfs.ext4", "-q", device], check=True)
        uuid = output(["blkid", "-s", "UUID", "-o", "value", device])
        if not re.fullmatch(r"[a-fA-F0-9-]{36}", uuid):
            raise ValueError("State filesystem UUID is missing.")
        marker = {"volume_id": volume_id, "name": name, "uuid": uuid}
        descriptor = os.open(MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(marker, stream)
            stream.flush()
            os.fsync(stream.fileno())
    uuid = output(["blkid", "-s", "UUID", "-o", "value", device])
    if uuid != marker["uuid"] or disks[0].get("fstype") not in (None, "ext4"):
        raise ValueError("Filesystem UUID/type differs from the retained receipt.")
    if mounted:
        if output(["findmnt", "-no", "UUID", "--mountpoint", str(MOUNT)]) != uuid:
            raise ValueError("Mounted filesystem UUID differs.")
        (MOUNT / "config").mkdir(mode=0o700, exist_ok=True)
        return uuid
    fstab = Path("/etc/fstab")
    lines = fstab.read_text().splitlines()
    registration = f"UUID={uuid} {MOUNT} ext4 defaults,nofail 0 2"
    for line in lines:
        fields = line.split()
        if line.startswith("#") or len(fields) < 2:
            continue
        if fields[1] == str(MOUNT) or fields[0] == f"UUID={uuid}":
            if line != registration:
                raise ValueError("Conflicting fstab registration.")
            break
    else:
        with fstab.open("a") as stream:
            stream.write("\n" + registration + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    MOUNT.mkdir(mode=0o755, exist_ok=True)
    subprocess.run(["mount", str(MOUNT)], check=True)
    if output(["findmnt", "-no", "UUID", "--mountpoint", str(MOUNT)]) != uuid:
        raise ValueError("State mount UUID verification failed.")
    (MOUNT / "config").mkdir(mode=0o700, exist_ok=True)
    return uuid


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--volume-id", required=True)
    parser.add_argument("--size-gib", required=True, type=int)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    if os.getuid() != 0 or not Path("/var/lib/ark-mcp-installed").exists():
        raise ValueError("Root execution and completed installer required.")
    if (
        not re.fullmatch(r"[a-z][a-z0-9-]{1,38}[a-z0-9]", args.name)
        or not 40 <= args.size_gib <= 65536
    ):
        raise ValueError("Invalid deployment name or disk size.")
    print(json.dumps({"uuid": prepare(args.volume_id, args.size_gib, args.name)}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError):
        raise SystemExit(
            "State preparation refused; inspect disk identity and receipt before retrying."
        ) from None
