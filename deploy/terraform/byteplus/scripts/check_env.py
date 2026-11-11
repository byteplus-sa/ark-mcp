"""Validate private host configuration without executing or printing its values."""

from __future__ import annotations

import math
import os
import re
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit


def load_environment(path: Path, *, expected_uid: int = 0) -> dict[str, str]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, encoding="utf-8") as stream:
        metadata = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != expected_uid
        ):
            raise ValueError("The .env must be a root-owned regular file with mode 0600.")
        if metadata.st_size > 65536:
            raise ValueError("The .env exceeds 64 KiB.")
        values: dict[str, str] = {}
        for line in stream:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            key, separator, value = line.partition("=")
            if not separator or not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
                raise ValueError("Use one KEY=value assignment per line.")
            if key in values:
                raise ValueError(f"Duplicate setting: {key}.")
            value = value.strip()
            if len(value) >= 2 and value[0] in "\"'" and value[-1] == value[0]:
                value = value[1:-1]
            if any(character in value for character in "$`"):
                raise ValueError(f"{key} must be a literal value without interpolation.")
            values[key] = value
    return values


def validate_environment(values: dict[str, str]) -> None:
    host = values.get("MCP_PUBLIC_HOST", "")
    if not re.fullmatch(r"([a-z0-9]([a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,63}", host):
        raise ValueError("MCP_PUBLIC_HOST must be a DNS hostname.")
    if host == "example.com" or host.endswith(".example.com"):
        raise ValueError("Replace the example MCP_PUBLIC_HOST.")
    for key in ("MCP_JWT_JWKS_URI", "MCP_JWT_ISSUER"):
        try:
            parsed = urlsplit(values.get(key, ""))
        except ValueError:
            raise ValueError(f"{key} must be a valid HTTPS URL.") from None
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.hostname == "example.com"
            or parsed.hostname.endswith(".example.com")
        ):
            raise ValueError(f"Configure a real HTTPS {key} without URL credentials.")
    for key in ("MCP_JWT_AUDIENCE", "MCP_TENANT_CLAIM"):
        if not values.get(key):
            raise ValueError(f"{key} is required.")
    if not re.fullmatch(r"[a-fA-F0-9]{64}", values.get("REDIS_PASSWORD", "")):
        raise ValueError("REDIS_PASSWORD must be 64 hex characters generated on the host.")
    if len(values.get("FASTMCP_TASKS_ENCRYPTION_KEY", "")) < 32:
        raise ValueError("FASTMCP_TASKS_ENCRYPTION_KEY must contain at least 32 random characters.")
    for key in (
        "DAILY_BUDGET_USD",
        "RATE_LIMIT_RPM",
        "RATE_LIMIT_BURST",
        "PROVIDER_MAX_CONCURRENCY",
        "PRINCIPAL_MAX_CONCURRENCY",
    ):
        try:
            number = float(values.get(key, ""))
        except ValueError:
            raise ValueError(f"{key} must be positive.") from None
        if (
            not math.isfinite(number)
            or number <= 0
            or (key != "DAILY_BUDGET_USD" and not number.is_integer())
        ):
            raise ValueError(
                f"{key} must be positive; request and concurrency limits must be integers."
            )
    if not any(
        values.get(key)
        for key in (
            "BYTEPLUS_MODELARK_API_KEY",
            "BYTEPLUS_SEED_SPEECH_API_KEY",
            "BYTEPLUS_VOD_MEDIAKIT_API_KEY",
            "BYTEPLUS_CV_API_KEY",
        )
    ):
        raise ValueError("Configure at least one BytePlus product API key.")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: check_env.py /path/to/private/.env", file=sys.stderr)
        return 2
    try:
        validate_environment(load_environment(Path(sys.argv[1])))
    except (OSError, UnicodeError, ValueError) as error:
        message = str(error) if type(error) is ValueError else "Cannot read the private .env file."
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
