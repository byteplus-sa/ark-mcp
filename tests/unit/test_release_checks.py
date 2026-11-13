import io
import json
import tarfile
import zipfile
from pathlib import Path

import pytest
from scripts.release_checks import read_version, validate_tag, verify_artifacts


@pytest.fixture
def release_tree(tmp_path: Path) -> Path:
    (tmp_path / "src/ark_mcp").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "ark-mcp"\nversion = "0.2.0"\n')
    (tmp_path / "src/ark_mcp/__init__.py").write_text('__version__ = "0.2.0"\n')
    (tmp_path / "uv.lock").write_text('[[package]]\nname = "ark-mcp"\nversion = "0.2.0"\n')
    (tmp_path / ".release-please-manifest.json").write_text('{".": "0.2.0"}')
    return tmp_path


def test_consistent_versions(release_tree: Path) -> None:
    assert read_version(release_tree) == "0.2.0"


@pytest.mark.parametrize(
    "path",
    ["pyproject.toml", "uv.lock", "src/ark_mcp/__init__.py", ".release-please-manifest.json"],
)
def test_version_drift_is_rejected(release_tree: Path, path: str) -> None:
    target = release_tree / path
    target.write_text(target.read_text().replace("0.2.0", "0.3.0"))
    with pytest.raises(ValueError, match="versions disagree"):
        read_version(release_tree)


def test_empty_manifest_only_allowed_before_first_release(release_tree: Path) -> None:
    (release_tree / ".release-please-manifest.json").write_text(json.dumps({}))
    assert read_version(release_tree, allow_bootstrap=True) == "0.2.0"
    with pytest.raises(ValueError, match="manifest"):
        read_version(release_tree)


def test_bootstrap_override_cannot_freeze_future_releases(release_tree: Path) -> None:
    (release_tree / "release-please-config.json").write_text(
        json.dumps({"packages": {".": {"release-as": "0.2.0"}}})
    )
    with pytest.raises(ValueError, match="one-time release-as"):
        read_version(release_tree)
    (release_tree / ".release-please-manifest.json").write_text("{}")
    assert read_version(release_tree, allow_bootstrap=True) == "0.2.0"


@pytest.mark.parametrize("tag", ["", "v01.2.0", "0.2.0", "v0.2.0rc1", "v0.2.0;echo bad", "v1.0.0"])
def test_invalid_or_mismatched_tag_is_rejected(tag: str) -> None:
    with pytest.raises(ValueError):
        validate_tag(tag, "0.2.0")


def test_valid_tag() -> None:
    validate_tag("v0.2.0", "0.2.0")


def make_artifacts(directory: Path, version: str = "0.2.0") -> None:
    metadata = f"Metadata-Version: 2.1\nName: ark-mcp\nVersion: {version}\n".encode()
    with zipfile.ZipFile(directory / "ark_mcp-0.2.0-py3-none-any.whl", "w") as wheel:
        wheel.writestr("ark_mcp-0.2.0.dist-info/METADATA", metadata)
    with tarfile.open(directory / "ark_mcp-0.2.0.tar.gz", "w:gz") as archive:
        member = tarfile.TarInfo("ark_mcp-0.2.0/PKG-INFO")
        member.size = len(metadata)
        archive.addfile(member, io.BytesIO(metadata))


def test_artifact_metadata_and_checksums(tmp_path: Path) -> None:
    make_artifacts(tmp_path)
    (tmp_path / ".gitignore").write_text("*")
    checksums = verify_artifacts(tmp_path, "0.2.0")
    assert len(checksums.splitlines()) == 2
    assert "  ark_mcp-0.2.0-py3-none-any.whl" in checksums
    assert "  ark_mcp-0.2.0.tar.gz" in checksums
    assert checksums == verify_artifacts(tmp_path, "0.2.0")


def test_empty_artifacts_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="one wheel and one"):
        verify_artifacts(tmp_path, "0.2.0")


def test_wrong_artifact_version_rejected(tmp_path: Path) -> None:
    make_artifacts(tmp_path, "9.9.9")
    with pytest.raises(ValueError, match="metadata"):
        verify_artifacts(tmp_path, "0.2.0")


def test_unexpected_artifact_rejected(tmp_path: Path) -> None:
    make_artifacts(tmp_path)
    (tmp_path / "credentials.json").write_text("{}")
    with pytest.raises(ValueError, match="Unexpected"):
        verify_artifacts(tmp_path, "0.2.0")
