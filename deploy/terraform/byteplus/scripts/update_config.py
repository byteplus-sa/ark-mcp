"""Atomically change only provider keys and recreate only the MCP application."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator


def read_private(path: Path, expected_uid: int) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > 65536
        ):
            raise ValueError("Configuration transaction files must be protected regular files.")
        return stream.read(65537)


def write_private(path: Path, content: bytes, expected_uid: int) -> None:
    import receive_config

    if path.exists() or path.is_symlink():
        read_private(path, expected_uid)
    descriptor, temporary = tempfile.mkstemp(prefix=".provider-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        receive_config.sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def cleanup_temporaries(directory: Path, expected_uid: int) -> None:
    paths = list(directory.glob(".provider-????????"))
    if not paths:
        return
    import receive_config

    for path in paths:
        read_private(path, expected_uid)
    for path in paths:
        path.unlink(missing_ok=True)
    receive_config.sync_directory(directory)


@contextmanager
def transaction_lock(directory: Path, expected_uid: int) -> Iterator[None]:
    metadata = directory.lstat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != expected_uid
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise ValueError("Provider configuration directory must be protected.")
    descriptor = os.open(
        directory / ".provider-update.lock",
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
        0o600,
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != expected_uid
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ValueError("Provider update lock is not protected.")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another provider update is active.") from None
        cleanup_temporaries(directory, expected_uid)
        yield
    finally:
        os.close(descriptor)


def cleanup_transaction(
    directory: Path, operation: str, previous_digest: str, next_digest: str, expected_uid: int
) -> None:
    import receive_config

    files = (
        (directory / f".{operation}.backup", previous_digest),
        (directory / f".{operation}.candidate", next_digest),
    )
    for path, expected in files:
        if (path.exists() or path.is_symlink()) and digest(
            read_private(path, expected_uid)
        ) != expected:
            raise ValueError("Terminal transaction cleanup requires matching private files.")
    for path, _ in files:
        path.unlink(missing_ok=True)
    receive_config.sync_directory(directory)


def inspect_transaction(
    previous_digest: str,
    next_digest: str,
    operation: str,
    *,
    env: Path = Path("/srv/ark-mcp/config/.env"),
    expected_uid: int = 0,
) -> dict[str, str]:
    """Return only a reconciled host transaction status, never configuration values."""
    if (
        not re.fullmatch(r"[a-f0-9]{64}", previous_digest)
        or not re.fullmatch(r"[a-f0-9]{64}", next_digest)
        or not re.fullmatch(r"config-[0-9]+-[0-9]+", operation)
        or len(operation) > 80
    ):
        raise ValueError("Provider transaction reference is invalid.")
    with transaction_lock(env.parent, expected_uid):
        return _inspect_transaction(previous_digest, next_digest, operation, env, expected_uid)


def _inspect_transaction(
    previous_digest: str, next_digest: str, operation: str, env: Path, expected_uid: int
) -> dict[str, str]:
    marker = env.parent / f".{operation}.json"
    current = digest(read_private(env, expected_uid))
    installed = read_private(env.parent / "installation.sha256", expected_uid).decode()
    if not marker.exists() and not marker.is_symlink():
        if current != previous_digest or installed != previous_digest:
            raise ValueError("Unmanaged provider configuration change.")
        return {"status": "absent"}
    transaction = json.loads(read_private(marker, expected_uid))
    if (
        not isinstance(transaction, dict)
        or transaction.get("previous_digest") != previous_digest
        or transaction.get("next_digest") != next_digest
    ):
        raise ValueError("Host transaction does not match the approved update.")
    status = transaction.get("status")
    expected = {
        "pending": {previous_digest, next_digest},
        "applied": {next_digest},
        "rolled_back": {previous_digest},
        "recovery_failed": {previous_digest},
    }
    if (
        status not in expected
        or current not in expected[status]
        or installed not in expected[status]
    ):
        raise ValueError("Host transaction state requires manual reconciliation.")
    if (
        status == "pending"
        and digest(read_private(env.parent / f".{operation}.backup", expected_uid))
        != previous_digest
    ):
        raise ValueError("Host recovery backup differs.")
    if status in {"applied", "rolled_back"}:
        cleanup_transaction(env.parent, operation, previous_digest, next_digest, expected_uid)
    return {"status": status}


def update(
    payload: bytes,
    previous_digest: str,
    next_digest: str,
    operation: str,
    *,
    env: Path = Path("/srv/ark-mcp/config/.env"),
    compose: Path = Path("/opt/ark-mcp/compose.yaml"),
    expected_uid: int = 0,
) -> None:
    """Recover an exact transaction; never change shared auth, storage, or job secrets."""
    if (
        not re.fullmatch(r"[a-f0-9]{64}", previous_digest)
        or not re.fullmatch(r"[a-f0-9]{64}", next_digest)
        or not re.fullmatch(r"config-[0-9]+-[0-9]+", operation)
        or len(operation) > 80
        or len(payload) > 65536
        or digest(payload) != next_digest
    ):
        raise ValueError("Provider transaction identity, size or digest is invalid.")
    with transaction_lock(env.parent, expected_uid):
        _update(payload, previous_digest, next_digest, operation, env, compose, expected_uid)


def _update(
    payload: bytes,
    previous_digest: str,
    next_digest: str,
    operation: str,
    env: Path,
    compose: Path,
    expected_uid: int,
) -> None:
    import check_env
    import receive_config

    directory = env.parent
    marker = directory / f".{operation}.json"
    backup = directory / f".{operation}.backup"
    candidate = directory / f".{operation}.candidate"
    expected = {"previous_digest": previous_digest, "next_digest": next_digest}
    transaction: dict[str, Any] = {}
    if marker.exists() or marker.is_symlink():
        transaction = json.loads(read_private(marker, expected_uid))
        if not isinstance(transaction, dict) or any(
            transaction.get(key) != value for key, value in expected.items()
        ):
            raise ValueError("Provider transaction differs; reconcile before retrying.")
    current = read_private(env, expected_uid)
    if transaction.get("status") == "applied":
        if digest(current) != next_digest:
            raise ValueError("Applied configuration was changed outside this transaction.")
        check_env.validate_environment(check_env.load_environment(env, expected_uid=expected_uid))
        receive_config.receipt(directory, next_digest)
        cleanup_transaction(directory, operation, previous_digest, next_digest, expected_uid)
        return
    if transaction and transaction.get("status") != "pending":
        raise ValueError("Provider update failed; reconcile and use a fresh transfer attempt.")
    if digest(current) not in (
        {previous_digest, next_digest} if transaction else {previous_digest}
    ):
        raise ValueError("Host configuration changed; refusing provider overwrite.")
    original = read_private(backup, expected_uid) if transaction else current
    if digest(original) != previous_digest:
        raise ValueError("Provider recovery backup does not match the approved configuration.")
    installed = read_private(directory / "installation.sha256", expected_uid).decode()
    if installed not in ({previous_digest, next_digest} if transaction else {previous_digest}):
        raise ValueError("Host configuration receipt differs; refusing unmanaged update.")
    write_private(candidate, payload, expected_uid)
    try:
        proposed = check_env.load_environment(candidate, expected_uid=expected_uid)
        check_env.validate_environment(proposed)
        if not transaction:
            original_values = check_env.load_environment(env, expected_uid=expected_uid)
            check_env.validate_environment(original_values)
        else:
            original_values = check_env.load_environment(backup, expected_uid=expected_uid)
            check_env.validate_environment(original_values)
        if {key: value for key, value in proposed.items() if key not in check_env.PRODUCT_KEYS} != {
            key: value
            for key, value in original_values.items()
            if key not in check_env.PRODUCT_KEYS
        }:
            raise ValueError("Only provider API keys may change in this transaction.")
        subprocess.run(["mountpoint", "-q", "/srv/ark-mcp"], capture_output=True, check=True)
        if not transaction:
            write_private(backup, original, expected_uid)
            transaction = {**expected, "status": "pending"}
            write_private(marker, json.dumps(transaction).encode(), expected_uid)
        command = ["docker", "compose", "--env-file", str(env), "-f", str(compose)]
        recreate = [
            *command,
            "up",
            "-d",
            "--no-build",
            "--no-deps",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            "240",
            "ark-mcp",
        ]
        try:
            write_private(env, payload, expected_uid)
            receive_config.receipt(directory, next_digest)
            subprocess.run(
                [*command, "config", "--quiet"], capture_output=True, check=True, timeout=30
            )
            subprocess.run(recreate, capture_output=True, check=True, timeout=960)
        except BaseException:
            try:
                write_private(env, original, expected_uid)
                receive_config.receipt(directory, previous_digest)
                subprocess.run(recreate, capture_output=True, check=True, timeout=960)
            except BaseException:
                transaction["status"] = "recovery_failed"
                write_private(marker, json.dumps(transaction).encode(), expected_uid)
                raise ValueError(
                    "Provider update and recovery failed; preserve evidence and inspect MCP health."
                ) from None
            transaction["status"] = "rolled_back"
            write_private(marker, json.dumps(transaction).encode(), expected_uid)
            cleanup_transaction(directory, operation, previous_digest, next_digest, expected_uid)
            raise ValueError(
                "Provider update failed; prior configuration and application restored."
            ) from None
        transaction["status"] = "applied"
        write_private(marker, json.dumps(transaction).encode(), expected_uid)
        cleanup_transaction(directory, operation, previous_digest, next_digest, expected_uid)
    finally:
        candidate.unlink(missing_ok=True)
        receive_config.sync_directory(directory)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Inspect only a provider-update transaction receipt."
    )
    parser.add_argument("--inspect", action="store_true", required=True)
    parser.add_argument("--previous-digest", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--operation", required=True)
    args = parser.parse_args()
    if os.getuid() != 0:
        raise ValueError("Root execution required.")
    subprocess.run(["mountpoint", "-q", "/srv/ark-mcp"], capture_output=True, check=True)
    print(json.dumps(inspect_transaction(args.previous_digest, args.digest, args.operation)))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit(
            "Provider transaction inspection failed; no secret values were logged."
        ) from None
