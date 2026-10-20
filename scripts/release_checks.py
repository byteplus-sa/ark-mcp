"""Validate release versions and distributable metadata without importing the server."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import tarfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path

VERSION_PATTERN = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"


def read_version(root: Path, *, allow_bootstrap: bool = False) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    version = project["version"]
    if not isinstance(version, str) or not re.fullmatch(VERSION_PATTERN, version):
        raise ValueError("Package version must be a stable semantic version")
    module = ast.parse((root / "src/ark_mcp/__init__.py").read_text())
    runtime_versions = [
        ast.literal_eval(node.value)
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "__version__" for target in node.targets
        )
    ]
    lock = tomllib.loads((root / "uv.lock").read_text())
    lock_versions = [
        package["version"] for package in lock["package"] if package["name"] == project["name"]
    ]
    manifest = json.loads((root / ".release-please-manifest.json").read_text())
    if "." not in manifest and not (allow_bootstrap and manifest == {}):
        raise ValueError("Release manifest must contain the root version")
    if (
        runtime_versions != [version]
        or lock_versions != [version]
        or manifest.get(".", version) != version
    ):
        raise ValueError("Package, runtime, lockfile, and manifest versions disagree")
    return version


def validate_tag(tag: str, version: str) -> None:
    if not re.fullmatch(f"v{VERSION_PATTERN}", tag) or tag != f"v{version}":
        raise ValueError("Release tag must exactly match the package version")


def verify_artifacts(directory: Path, version: str) -> str:
    wheels = sorted(directory.glob("*.whl"))
    archives = sorted(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(archives) != 1:
        raise ValueError("Expected one wheel and one source distribution")
    allowed = {*wheels, *archives, directory / "SHA256SUMS", directory / ".gitignore"}
    if any(path not in allowed for path in directory.iterdir()):
        raise ValueError("Unexpected file in release artifact directory")
    expected = {
        f"ark_mcp-{version}-py3-none-any.whl",
        f"ark_mcp-{version}.tar.gz",
    }
    if {wheels[0].name, archives[0].name} != expected:
        raise ValueError("Distribution filenames do not match the release version")
    with zipfile.ZipFile(wheels[0]) as wheel:
        metadata_files = [name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata_files) != 1:
            raise ValueError("Wheel must contain exactly one metadata file")
        wheel_metadata = wheel.read(metadata_files[0])
    with tarfile.open(archives[0]) as archive:
        source_metadata_file = archive.extractfile(f"ark_mcp-{version}/PKG-INFO")
        if source_metadata_file is None:
            raise ValueError("Source distribution is missing metadata")
        with source_metadata_file:
            source_metadata = source_metadata_file.read()
    for raw in (wheel_metadata, source_metadata):
        metadata = BytesParser().parsebytes(raw)
        if metadata["Name"] != "ark-mcp" or metadata["Version"] != version:
            raise ValueError("Distribution metadata does not match the release")
    return "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in sorted([*wheels, *archives])
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--tag")
    parser.add_argument("--artifacts", type=Path)
    args = parser.parse_args()
    version = read_version(args.root, allow_bootstrap=args.tag is None)
    if args.tag is not None:
        validate_tag(args.tag, version)
    if args.artifacts is not None:
        checksums = verify_artifacts(args.artifacts, version)
        (args.artifacts / "SHA256SUMS").write_text(checksums)


if __name__ == "__main__":
    main()
