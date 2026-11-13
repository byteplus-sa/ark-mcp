"""Tests for explicitly invoked disk, secret, and update helpers."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[2]
HOST = ROOT / "deploy/terraform/byteplus/scripts"


def load_helper(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, HOST / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "change",
    [
        {"serial": "another"},
        {"size": 1},
        {"type": "part"},
        {"children": [{}]},
        {"mountpoints": ["/"]},
        {"fstype": "ext4"},
    ],
)
def test_disk_initializer_rejects_wrong_or_used_disk(change: dict[str, object]) -> None:
    helper = load_helper("prepare_state")
    record = {
        "serial": "demo123",
        "size": 40 * 1024**3,
        "type": "disk",
        "mountpoints": [None],
        "fstype": None,
        **change,
    }
    with pytest.raises(ValueError):
        helper.validate_disk(record, "vol-demo123", 40, allow_existing=False)


def test_matching_empty_disk_is_accepted_but_signatures_are_not() -> None:
    helper = load_helper("prepare_state")
    record = {"serial": "demo123", "size": 40 * 1024**3, "type": "disk"}
    helper.validate_disk(record, "vol-demo123", 40, allow_existing=False)
    with pytest.raises(ValueError):
        helper.validate_signatures({"signatures": [{"type": "gpt"}]})
    helper.validate_signatures({"signatures": []})


@pytest.mark.parametrize("result", [{}, {"signatures": None}, {"signatures": ""}])
def test_missing_signature_evidence_never_authorizes_formatting(result: dict[str, object]) -> None:
    helper = load_helper("prepare_state")
    with pytest.raises(ValueError):
        helper.validate_signatures(result)


def test_matching_receipt_preserves_configuration_without_private_key(tmp_path: Path) -> None:
    from scripts.byteplus_deploy.private import configuration_digest

    helper = load_helper("receive_config")
    target = tmp_path / ".env"
    payload = b"existing-private-configuration"
    target.write_bytes(payload)
    target.chmod(0o600)
    assert helper.existing_matches(target, configuration_digest(payload), expected_uid=os.getuid())
    assert target.read_bytes() == payload
    with pytest.raises(ValueError):
        helper.existing_matches(target, "a" * 64, expected_uid=os.getuid())


def test_ciphertext_authentication_rejects_other_instance_and_tampering() -> None:
    from scripts.byteplus_deploy.private import encrypt_payload

    helper = load_helper("receive_config")
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    public = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    envelope = encrypt_payload(b"synthetic-secret", public, "i-demo")
    assert helper.decrypt_payload(envelope, private_key, "i-demo") == b"synthetic-secret"
    with pytest.raises(InvalidTag):
        helper.decrypt_payload(envelope, private_key, "i-other")
    envelope["ciphertext"] = envelope["ciphertext"][:-4] + "AAAA"
    with pytest.raises(InvalidTag):
        helper.decrypt_payload(envelope, private_key, "i-demo")


def test_update_preserves_other_services_and_persists_image(tmp_path: Path) -> None:
    helper = load_helper("update_image")
    import yaml

    compose = tmp_path / "compose.yaml"
    original = {
        "services": {
            "ark-mcp": {"image": "old", "stop_grace_period": "660s"},
            "redis": {"image": "redis"},
            "caddy": {"image": "caddy"},
        },
        "volumes": {"ark-state": {"driver_opts": {"device": "/srv/ark-mcp/ark-state"}}},
    }
    compose.write_text(yaml.safe_dump(original))
    image = "ghcr.io/example/ark-mcp@sha256:" + "a" * 64
    helper.set_image(compose, image)
    changed = yaml.safe_load(compose.read_text())
    assert changed["services"]["ark-mcp"]["image"] == image
    assert changed["services"]["redis"] == original["services"]["redis"]
    assert changed["volumes"] == original["volumes"]
    assert changed["services"]["ark-mcp"]["stop_grace_period"] == "660s"


def test_provider_configuration_excludes_legacy_and_account_secrets(tmp_path: Path) -> None:
    from scripts.byteplus_deploy.private import make_configuration

    provider = tmp_path / "providers.env"
    provider.write_text(
        "BYTEPLUS_MODELARK_API_KEY=synthetic-provider\nMCP_JWT_ISSUER=old\nS3_SECRET_KEY=unused\nBYTEPLUS_MODELARK_ACCESS_KEY=unused\n"
        "BYTEPLUS_MODELARK_SEEDANCE_2_5_PREMIUM_ENABLED=true\n"
    )
    provider.chmod(0o600)
    payload, token = make_configuration(provider, "mcp.team.test")
    values = dict(line.split("=", 1) for line in payload.decode().splitlines())
    assert values["BYTEPLUS_MODELARK_API_KEY"] == "synthetic-provider"
    assert "MCP_JWT_ISSUER" not in values
    assert "S3_SECRET_KEY" not in values
    assert "BYTEPLUS_MODELARK_ACCESS_KEY" not in values
    assert "BYTEPLUS_MODELARK_SEEDANCE_2_5_PREMIUM_ENABLED" not in values
    assert token not in payload.decode()
    assert values["REDIS_PASSWORD"] != values["FASTMCP_TASKS_ENCRYPTION_KEY"]
    assert json.loads(json.dumps(values)) == values


def test_configuration_is_published_atomically_and_receipt_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.byteplus_deploy.private import configuration_digest, make_configuration

    helper = load_helper("receive_config")
    monkeypatch.setitem(sys.modules, "check_env", load_helper("check_env"))
    directory = tmp_path / "config"
    monkeypatch.setattr(helper, "CONFIG", directory)
    provider = tmp_path / "providers"
    provider.write_text("BYTEPLUS_MODELARK_API_KEY=synthetic-provider\n")
    provider.chmod(0o600)
    payload, _ = make_configuration(provider, "mcp.team.test")
    digest = configuration_digest(payload)
    original_link = helper.os.link
    observed = []

    def publish(source: str, target: Path, **kwargs: object) -> None:
        assert not target.exists()
        assert Path(source).read_bytes() == payload
        observed.append("complete-before-publication")
        original_link(source, target, **kwargs)

    monkeypatch.setattr(helper.os, "link", publish)
    helper.install(payload, digest, expected_uid=os.getuid())
    assert observed == ["complete-before-publication"]
    assert (directory / ".env").read_bytes() == payload
    assert (directory / "installation.sha256").read_text() == digest
    (directory / "installation.sha256").unlink()
    helper.install(payload, digest, expected_uid=os.getuid())
    assert (directory / "installation.sha256").read_text() == digest
    changed = payload.replace(b"DAILY_BUDGET_USD=25", b"DAILY_BUDGET_USD=26")
    with pytest.raises(ValueError, match="differs"):
        helper.install(changed, configuration_digest(changed), expected_uid=os.getuid())
    assert (directory / ".env").read_bytes() == payload


def test_interrupted_configuration_publication_leaves_no_partial_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from scripts.byteplus_deploy.private import configuration_digest, make_configuration

    helper = load_helper("receive_config")
    monkeypatch.setitem(sys.modules, "check_env", load_helper("check_env"))
    directory = tmp_path / "config"
    monkeypatch.setattr(helper, "CONFIG", directory)
    provider = tmp_path / "providers"
    provider.write_text("BYTEPLUS_MODELARK_API_KEY=synthetic-provider\n")
    provider.chmod(0o600)
    payload, _ = make_configuration(provider, "mcp.team.test")

    def interrupted(*args: object, **kwargs: object) -> None:
        raise OSError("simulated publication interruption")

    monkeypatch.setattr(helper.os, "link", interrupted)
    with pytest.raises(OSError):
        helper.install(payload, configuration_digest(payload), expected_uid=os.getuid())
    assert not (directory / ".env").exists()
    assert not list(directory.glob(".configuration-*"))


def test_registry_credentials_are_cleaned_after_failed_pull(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    helper = load_helper("receive_config")
    original_temporary = helper.tempfile.TemporaryDirectory
    locations = []

    def temporary(**kwargs: object) -> object:
        directory = original_temporary(dir=tmp_path)
        locations.append(Path(directory.name))
        return directory

    commands = []

    def run(command: list[str], **kwargs: object) -> object:
        commands.append(command)
        return SimpleNamespace(returncode=0 if "login" in command else 1)

    monkeypatch.setattr(helper.tempfile, "TemporaryDirectory", temporary)
    monkeypatch.setattr(helper.subprocess, "run", run)
    with pytest.raises(ValueError, match="pull failed"):
        helper.pull_image(
            json.dumps({"username": "test-user", "token": "synthetic-token"}).encode(),
            "ghcr.io/example/ark-mcp@sha256:" + "a" * 64,
        )
    assert "login" in commands[0] and "pull" in commands[1]
    assert all(not path.exists() for path in locations)
    assert all("synthetic-token" not in command for command in commands)


def test_failed_image_update_restores_old_compose_and_recreates_only_app(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    from types import SimpleNamespace

    import yaml

    helper = load_helper("update_image")
    monkeypatch.setitem(
        sys.modules,
        "check_env",
        SimpleNamespace(load_environment=lambda _: {}, validate_environment=lambda _: None),
    )
    compose = tmp_path / "compose.yaml"
    original = yaml.safe_dump(
        {
            "services": {
                "ark-mcp": {"image": "old", "stop_grace_period": "660s"},
                "redis": {"image": "redis"},
            }
        }
    ).encode()
    compose.write_bytes(original)
    env = tmp_path / ".env"
    env.write_text("retained config")
    commands = []

    def run(command: list[str], **kwargs: object) -> object:
        commands.append(command)
        if "up" in command and kwargs["check"]:
            raise subprocess.CalledProcessError(1, command)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(helper.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        helper.update("ghcr.io/example/ark-mcp@sha256:" + "a" * 64, env=env, compose=compose)
    assert compose.read_bytes() == original
    assert env.read_text() == "retained config"
    inspected = next(index for index, command in enumerate(commands) if "inspect" in command)
    recreated = [command for command in commands if "up" in command]
    assert inspected < commands.index(recreated[0])
    assert len(recreated) == 2
    assert all(command[-1] == "ark-mcp" and "--no-deps" in command for command in recreated)


@pytest.mark.parametrize("invalid", [False, True])
def test_installer_extracts_only_the_complete_expected_archive(
    tmp_path: Path, invalid: bool
) -> None:
    import subprocess

    import yaml

    names = {
        "check_env.py",
        "prepare_state.py",
        "receive_config.py",
        "update_image.py",
        "update_config.py",
        "generate_mcp_key.py",
    }
    archive = {name: "synthetic helper source\n" for name in names}
    if invalid:
        archive["../unexpected.py"] = "refuse traversal"
    (tmp_path / "helpers.yaml").write_text(yaml.safe_dump(archive))
    installer = (HOST / "install.sh").read_text()
    snippet = installer.split("python3 - <<'PY'\n", 1)[1].split("\nPY", 1)[0]
    snippet = snippet.replace('Path("/opt/ark-mcp")', f"Path({str(tmp_path)!r})")
    result = subprocess.run(
        [sys.executable, "-c", snippet], capture_output=True, text=True, check=False
    )
    if invalid:
        assert result.returncode != 0
        assert not list(tmp_path.glob("*.py"))
    else:
        assert result.returncode == 0
        assert {path.name for path in tmp_path.glob("*.py")} == names
        assert all(path.stat().st_mode & 0o777 == 0o700 for path in tmp_path.glob("*.py"))
