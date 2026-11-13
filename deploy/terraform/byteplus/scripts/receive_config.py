"""Decrypt host-bound configuration or temporary registry credentials locally."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

CONFIG = Path("/srv/ark-mcp/config")


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def cleanup_transfer(directory: Path, *, expected_uid: int = 0) -> None:
    if directory.parent != CONFIG or not re.fullmatch(r"\.transfer-[a-f0-9]{24}", directory.name):
        raise ValueError("Transfer cleanup requires an exact managed directory.")
    parent = CONFIG.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or parent.st_uid != expected_uid
        or stat.S_IMODE(parent.st_mode) != 0o700
    ):
        raise ValueError("Transfer configuration directory is not protected.")
    if not directory.exists() and not directory.is_symlink():
        sync_directory(CONFIG)
        return
    metadata = directory.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError("Transfer directory is not protected.")
    files = list(directory.iterdir())
    for path in files:
        if path.name not in {"key.pem", "envelope.json", "envelope.json.b64"}:
            raise ValueError("Unexpected transfer evidence; refusing cleanup.")
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != expected_uid
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size > 262144
            ):
                raise ValueError("Transfer evidence is not a protected bounded regular file.")
        finally:
            os.close(descriptor)
    for path in files:
        path.unlink(missing_ok=True)
    sync_directory(directory)
    directory.rmdir()
    sync_directory(CONFIG)


def receipt(directory: Path, digest: str) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".receipt-", dir=directory)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(digest)
            stream.flush()
            os.fsync(stream.fileno())
        target = directory / "installation.sha256"
        if target.is_symlink():
            raise ValueError("Configuration receipt must not be a symlink.")
        os.replace(temporary, target)
        sync_directory(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def existing_matches(path: Path, digest: str, *, expected_uid: int = 0) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > 65536
        ):
            raise ValueError("Existing configuration is not a protected regular file.")
        if hashlib.sha256(stream.read()).hexdigest() != digest:
            raise ValueError("Existing configuration differs; refusing overwrite or key rotation.")
    return True


def decrypt_payload(
    envelope: dict[str, str], private_key: rsa.RSAPrivateKey, instance_id: str
) -> bytes:
    key = private_key.decrypt(
        base64.b64decode(envelope["wrapped_key"], validate=True),
        padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    return AESGCM(key).decrypt(
        base64.b64decode(envelope["nonce"], validate=True),
        base64.b64decode(envelope["ciphertext"], validate=True),
        instance_id.encode(),
    )


def install(payload: bytes, digest: str, *, expected_uid: int = 0) -> None:
    import check_env

    if len(payload) > 65536 or hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("Configuration digest or size differs.")
    lines = payload.decode().splitlines()
    values: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if (
            not separator
            or key in values
            or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
            or any(c in value for c in "$`")
        ):
            raise ValueError("Invalid literal configuration.")
        values[key] = value
    check_env.validate_environment(values)
    directory = CONFIG
    directory.mkdir(mode=0o700, exist_ok=True)
    metadata = directory.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError("Configuration directory must be root-owned mode-0700.")
    target = directory / ".env"
    if existing_matches(target, digest, expected_uid=expected_uid):
        check_env.validate_environment(
            check_env.load_environment(target, expected_uid=expected_uid)
        )
        receipt(directory, digest)
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".configuration-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        check_env.validate_environment(
            check_env.load_environment(Path(temporary), expected_uid=expected_uid)
        )
        os.link(temporary, target, follow_symlinks=False)
        sync_directory(directory)
        receipt(directory, digest)
    finally:
        Path(temporary).unlink(missing_ok=True)


def pull_image(payload: bytes, image: str) -> None:
    credentials: dict[str, Any] = json.loads(payload)
    if not re.fullmatch(r"ghcr\.io/[a-z0-9][a-z0-9./_-]*@sha256:[a-f0-9]{64}", image):
        raise ValueError("Image must be a GHCR sha256 digest.")
    with tempfile.TemporaryDirectory(prefix="ark-mcp-registry-", dir="/run") as directory:
        token = credentials.get("token", "")
        username = credentials.get("username", "")
        if token:
            if not isinstance(token, str) or not isinstance(username, str) or not username:
                raise ValueError("Registry credentials are incomplete.")
            login = subprocess.run(
                [
                    "docker",
                    "--config",
                    directory,
                    "login",
                    "ghcr.io",
                    "-u",
                    username,
                    "--password-stdin",
                ],
                input=token + "\n",
                text=True,
                capture_output=True,
                timeout=60,
                check=False,
            )
            if login.returncode:
                raise ValueError("Temporary registry login failed.")
        pulled = subprocess.run(
            ["docker", "--config", directory, "pull", image],
            capture_output=True,
            timeout=480,
            check=False,
        )
        if pulled.returncode:
            raise ValueError(
                "Pinned image pull failed; supply fresh registry access before retrying."
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup-transfer", type=Path)
    parser.add_argument("--instance-id")
    parser.add_argument("--envelope", type=Path)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--kind", choices=("config", "image", "provider-update"))
    parser.add_argument("--digest")
    parser.add_argument("--previous-digest")
    parser.add_argument("--operation")
    parser.add_argument("--image")
    args = parser.parse_args()
    if os.getuid() != 0:
        raise ValueError("Root execution required.")
    subprocess.run(["mountpoint", "-q", "/srv/ark-mcp"], check=True)
    if args.cleanup_transfer is not None:
        cleanup_transfer(args.cleanup_transfer)
        print(json.dumps({"transfer": "cleaned"}))
        return 0
    if (
        not args.instance_id
        or args.kind is None
        or args.key is None
        or args.envelope is None
        or args.envelope.name != "envelope.json"
        or args.key != args.envelope.parent / "key.pem"
        or args.envelope.parent.parent != CONFIG
        or not re.fullmatch(r"\.transfer-[a-f0-9]{24}", args.envelope.parent.name)
    ):
        raise ValueError("An exact managed transfer and installation kind are required.")
    try:
        if args.kind == "config":
            import check_env

            if not args.digest or not re.fullmatch(r"[a-f0-9]{64}", args.digest):
                raise ValueError("Configuration digest required.")
            if existing_matches(CONFIG / ".env", args.digest):
                check_env.validate_environment(check_env.load_environment(CONFIG / ".env"))
                receipt(CONFIG, args.digest)
                print(json.dumps({"configuration": "unchanged"}))
                return 0
        key = serialization.load_pem_private_key(args.key.read_bytes(), password=None)
        if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 3072:
            raise ValueError("Host transfer key is invalid.")
        payload = decrypt_payload(json.loads(args.envelope.read_bytes()), key, args.instance_id)
        if args.kind == "config":
            install(payload, args.digest)
        elif args.kind == "provider-update":
            import update_config

            update_config.update(
                payload, args.previous_digest or "", args.digest or "", args.operation or ""
            )
        else:
            pull_image(payload, args.image or "")
    finally:
        cleanup_transfer(args.envelope.parent)
    print(json.dumps({args.kind: "installed"}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit("Encrypted installation failed; no secret values were logged.") from None
