"""Persist an image-only change and recreate just the application container."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import yaml


def replace_file(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise ValueError("Compose file must not be a symlink.")
    descriptor, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def set_image(path: Path, image: str) -> None:
    if not re.fullmatch(r"ghcr\.io/[a-z0-9][a-z0-9./_-]*@sha256:[a-f0-9]{64}", image):
        raise ValueError("Require a GHCR image pinned to a sha256 digest.")
    compose: dict[str, Any] = yaml.safe_load(path.read_text())
    application = compose["services"]["ark-mcp"]
    if "build" in application:
        raise ValueError("Use the installed production Compose configuration.")
    application["image"] = image
    replace_file(path, yaml.safe_dump(compose, sort_keys=False).encode())


def update(
    image: str,
    *,
    env: Path = Path("/srv/ark-mcp/config/.env"),
    compose: Path = Path("/opt/ark-mcp/compose.yaml"),
) -> None:
    import check_env

    subprocess.run(["mountpoint", "-q", "/srv/ark-mcp"], check=True)
    check_env.validate_environment(check_env.load_environment(env))
    subprocess.run(["docker", "image", "inspect", image], capture_output=True, check=True)
    original = compose.read_bytes()
    command = ["docker", "compose", "--env-file", str(env), "-f", str(compose)]
    try:
        set_image(compose, image)
        subprocess.run([*command, "config", "--quiet"], capture_output=True, check=True)
        subprocess.run(
            [
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
            ],
            capture_output=True,
            check=True,
            timeout=960,
        )
    except BaseException:
        replace_file(compose, original)
        subprocess.run(
            [
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
            ],
            capture_output=True,
            check=False,
            timeout=960,
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    if os.getuid() != 0:
        raise ValueError("Root execution required.")
    update(args.image)
    print("Application image updated; retained configuration and data unchanged.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        raise SystemExit(
            "Application-only update failed; inspect health before retrying."
        ) from None
