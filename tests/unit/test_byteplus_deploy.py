"""Behavioral tests for the protected deployment workspace."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from scripts.byteplus_deploy.workspace import Workspace, read_private, validate_config


@pytest.fixture
def deployment_config() -> dict[str, object]:
    return {
        "name": "team-demo",
        "region": "ap-southeast-1",
        "zone_id": "ap-southeast-1a",
        "image_id": "image-demo",
        "instance_type": "ecs.c3i.large",
        "key_pair_name": "demo-public-key",
        "ark_mcp_image": "ghcr.io/example/ark-mcp@sha256:" + "a" * 64,
    }


def test_minimum_defaults_keep_ssh_closed(deployment_config: dict[str, object]) -> None:
    config = validate_config(deployment_config)
    assert config["data_disk_size_gib"] == 40
    assert config["admin_cidrs"] == []
    assert config["public_hostname"] is None
    assert config["instance_deletion_protection"] is True


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("name", "../escape"),
        ("region", "localhost/evil"),
        ("zone_id", "another-region-a"),
        ("admin_cidrs", ["0.0.0.0/0"]),
        ("data_disk_size_gib", True),
        ("data_disk_size_gib", 39),
        ("public_hostname", ""),
        ("ark_mcp_image", "ghcr.io/example/ark-mcp:latest"),
        ("BYTEPLUS_MODELARK_API_KEY", "synthetic-secret"),
        ("instance_deletion_protection", False),
        ("existing_vpc_id", "vpc-unapproved"),
    ],
)
def test_invalid_configuration_is_rejected_without_echoing_values(
    deployment_config: dict[str, object], key: str, value: object
) -> None:
    deployment_config[key] = value
    with pytest.raises(ValueError) as error:
        validate_config(deployment_config)
    assert "synthetic-secret" not in str(error.value)


def test_workspace_creation_preserves_source_and_protects_files(
    deployment_config: dict[str, object], tmp_path: Path
) -> None:
    workspace = Workspace.create(tmp_path / "deployment", validate_config(deployment_config))
    assert workspace.path.stat().st_mode & 0o777 == 0o700
    assert (workspace.path / "config.json").stat().st_mode & 0o777 == 0o600
    assert (workspace.module / ".terraform.lock.hcl").exists()
    assert not list((workspace.path / "source").rglob("terraform.tfstate"))
    assert not list((workspace.path / "source").rglob(".env"))
    with pytest.raises(FileExistsError):
        Workspace.create(workspace.path, validate_config(deployment_config))


def test_private_input_rejects_symlink_fifo_and_wrong_mode(tmp_path: Path) -> None:
    private = tmp_path / "secret"
    private.write_text("synthetic")
    with pytest.raises(ValueError):
        read_private(private)
    private.chmod(0o600)
    assert read_private(private) == b"synthetic"
    link = tmp_path / "link"
    link.symlink_to(private)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, 0o600)
    for path in (link, fifo):
        with pytest.raises((OSError, ValueError)):
            read_private(path)


def test_journal_survives_reopen_and_second_writer_is_rejected(
    deployment_config: dict[str, object], tmp_path: Path
) -> None:
    workspace = Workspace.create(tmp_path / "deployment", validate_config(deployment_config))
    with workspace.lock():
        workspace.save_journal({"version": 1, "operations": {"prepare": {"id": "ivk-demo"}}})
        with pytest.raises(ValueError, match="in use"), workspace.lock():
            pass
    reopened = Workspace(workspace.path)
    assert reopened.journal()["operations"]["prepare"]["id"] == "ivk-demo"
    assert json.loads(read_private(workspace.path / "journal.json"))["version"] == 1


def test_fingerprint_excludes_terraform_runtime_but_detects_source_changes(
    deployment_config: dict[str, object], tmp_path: Path
) -> None:
    workspace = Workspace.create(tmp_path / "deployment", deployment_config)
    original = workspace.fingerprint()
    (workspace.module / ".terraform").mkdir()
    (workspace.module / ".terraform/provider").write_text("cache")
    (workspace.module / "terraform.tfstate").write_text("runtime state")
    (workspace.module / "terraform.tfvars.json").write_text("{}")
    assert workspace.fingerprint() == original
    source = workspace.module / "outputs.tf"
    source.write_text(source.read_text() + "\n")
    assert workspace.fingerprint() != original
    (workspace.module / "unexpected.tf").write_text("")
    with pytest.raises(ValueError, match="Unexpected"):
        workspace.fingerprint()


def test_fingerprint_rejects_source_symlink(
    deployment_config: dict[str, object], tmp_path: Path
) -> None:
    workspace = Workspace.create(tmp_path / "deployment", deployment_config)
    source = workspace.module / "outputs.tf"
    source.unlink()
    source.symlink_to(tmp_path / "outside")
    with pytest.raises((ValueError, OSError)):
        workspace.fingerprint()
