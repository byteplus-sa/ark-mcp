"""Generate a shared MCP key without loading application settings or logging the key."""

from __future__ import annotations

import argparse
import hashlib
import os
import secrets
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("key_file", type=Path, help="New private file for the raw key (mode 0600).")
    args = parser.parse_args()
    if os.name != "posix":
        parser.error("Generate keys on a POSIX host that enforces private file permissions.")
    token = secrets.token_hex(32)
    try:
        descriptor = os.open(args.key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="ascii") as stream:
            stream.write(token + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except OSError:
        print(
            "Cannot create the private key file; choose a new path in a private directory.",
            file=sys.stderr,
        )
        return 1
    print(f"MCP_API_KEY_SHA256={hashlib.sha256(token.encode('ascii')).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
