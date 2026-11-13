from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from scripts.byteplus_deploy import cli, configuration
from scripts.byteplus_deploy.private import (
    PRODUCT_KEYS,
    configuration_digest,
    make_configuration_values,
    replace_provider_keys,
)
from scripts.byteplus_deploy.workspace import ROOT, Workspace, atomic_private

REFERENCE = {
    "region": "ap-southeast-1",
    "account_id": "1234567890",
    "project_name": "default",
    "secret_name": "ark-team-providers",
    "version_id": "b58a7e97-3000-4e6e-90c4-c01958333717",
}


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    result = Workspace.create(
        tmp_path / "deployment",
        {
            "name": "secret-demo",
            "zone_id": "ap-southeast-1a",
            "image_id": "image-demo",
            "instance_type": "ecs.c3i.large",
            "key_pair_name": "demo-public-key",
            "ark_mcp_image": "ghcr.io/example/ark-mcp@sha256:" + "a" * 64,
        },
    )
    payload, token = make_configuration_values(
        {"BYTEPLUS_MODELARK_API_KEY": "synthetic-original"}, "mcp.team.test"
    )
    atomic_private(result.path / "host.env", payload)
    atomic_private(result.path / "team.key", (token + "\n").encode())
    journal = result.journal()
    journal.update(
        phase="ready",
        source_fingerprint=result.fingerprint(),
        configuration_sha256=configuration_digest(payload),
        outputs={
            "instance_id": "i-demo",
            "state_volume": {"id": "vol-demo"},
            "public_hostname": "mcp.team.test",
            "mcp_url": "https://mcp.team.test/mcp",
        },
    )
    result.save_journal(journal)
    return result


def args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> argparse.Namespace:
    reference = tmp_path / "reference.json"
    atomic_private(reference, json.dumps(REFERENCE).encode())
    monkeypatch.setattr(
        configuration,
        "fetch_provider_keys",
        lambda _: {"BYTEPLUS_MODELARK_API_KEY": "synthetic-replacement"},
    )
    return argparse.Namespace(secret_reference_file=reference, retry_failed=False)


def test_plan_and_status_never_disclose_values_and_preserve_host(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = (workspace.path / "host.env").read_bytes()
    plan = configuration.plan(workspace, args(tmp_path, monkeypatch))
    output = json.dumps(plan) + json.dumps(configuration.status(workspace))
    assert "synthetic-" not in output
    assert plan["changed_keys"] == ["BYTEPLUS_MODELARK_API_KEY"]
    assert plan["source"] == REFERENCE
    assert (workspace.path / "host.env").read_bytes() == original
    candidate = (workspace.path / "config.candidate.env").read_bytes()
    assert b"synthetic-replacement" in candidate
    before = dict(line.split("=", 1) for line in original.decode().splitlines())
    after = dict(line.split("=", 1) for line in candidate.decode().splitlines())
    assert {k: v for k, v in before.items() if k not in PRODUCT_KEYS} == {
        k: v for k, v in after.items() if k not in PRODUCT_KEYS
    }


@pytest.mark.parametrize("pending", ["update", "verification_pending", "config_update"])
def test_config_plan_refuses_competing_or_ambiguous_work(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pending: str
) -> None:
    supplied = args(tmp_path, monkeypatch)
    journal = workspace.journal()
    journal[pending] = {"status": "working"}
    workspace.save_journal(journal)
    monkeypatch.setattr(configuration, "fetch_provider_keys", lambda _: pytest.fail("KMS read"))
    with pytest.raises(ValueError, match="pending"):
        configuration.plan(workspace, supplied)


def test_apply_needs_exact_approval_and_unchanged_candidate(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supplied = args(tmp_path, monkeypatch)
    plan = configuration.plan(workspace, supplied)
    supplied.approve_config = "wrong"
    with pytest.raises(ValueError, match="approval"):
        configuration.apply(workspace, supplied)
    supplied.approve_config = plan["approve_config"]
    atomic_private(workspace.path / "config.candidate.env", b"tampered")
    with pytest.raises(ValueError, match="changed"):
        configuration.apply(workspace, supplied)


def test_apply_and_resume_use_staged_bytes_without_another_secret_read(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    supplied = args(tmp_path, monkeypatch)
    plan = configuration.plan(workspace, supplied)
    supplied.approve_config = plan["approve_config"]
    candidate = (workspace.path / "config.candidate.env").read_bytes()
    original_key = (workspace.path / "team.key").read_bytes()
    transfers = []
    cloud = SimpleNamespace(verify_instance=lambda *args: None, close=lambda: None)
    monkeypatch.setattr(configuration, "Cloud", lambda _: cloud)

    def transfer(name: str, payload: bytes, arguments: list[str], **kwargs: Any) -> None:
        transfers.append((name, payload, arguments, kwargs))
        journal = workspace.journal()
        journal["operations"][name] = {"status": "succeeded"}
        workspace.save_journal(journal)

    monkeypatch.setattr(cli, "Guest", lambda *args, **kwargs: SimpleNamespace(transfer=transfer))
    monkeypatch.setattr(configuration, "fetch_provider_keys", lambda _: pytest.fail("KMS reread"))
    verification = AsyncMock(side_effect=[ValueError("temporary verification outage"), None])
    monkeypatch.setattr(cli, "verify_deployment", verification)
    with pytest.raises(ValueError, match="outage"):
        configuration.apply(workspace, supplied)
    cli.resume_deployment(workspace, argparse.Namespace(retry_failed=False))
    assert len(transfers) == 2
    assert transfers[0][0] == transfers[1][0]
    assert transfers[0][1] == transfers[1][1] == candidate
    assert transfers[0][3]["timeout"] == 2400
    assert (workspace.path / "host.env").read_bytes() == candidate
    assert (workspace.path / "team.key").read_bytes() == original_key
    assert workspace.journal()["provider_secret_source"] == REFERENCE
    assert "config_update" not in workspace.journal()
    assert not (workspace.path / "config.previous.env").exists()


def helper(name: str) -> ModuleType:
    path = ROOT / "deploy/terraform/byteplus/scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("failure", [False, True])
def test_host_recreates_only_app_and_restores_old_config_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: bool
) -> None:
    module = helper("update_config")
    monkeypatch.setitem(sys.modules, "check_env", helper("check_env"))
    receiver = helper("receive_config")
    monkeypatch.setitem(sys.modules, "receive_config", receiver)
    directory = tmp_path / "config"
    directory.mkdir(mode=0o700)
    env = directory / ".env"
    old, _ = make_configuration_values(
        {"BYTEPLUS_MODELARK_API_KEY": "synthetic-original"}, "mcp.team.test"
    )
    candidate = replace_provider_keys(old, {"BYTEPLUS_MODELARK_API_KEY": "synthetic-new"})
    atomic_private(env, old)
    receiver.receipt(directory, configuration_digest(old))
    commands = []

    def run(command: list[str], **kwargs: Any) -> SimpleNamespace:
        commands.append(command)
        if failure and len([item for item in commands if "up" in item]) == 1 and "up" in command:
            raise subprocess.CalledProcessError(1, command, output="synthetic-new")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", run)
    arguments = (
        candidate,
        configuration_digest(old),
        configuration_digest(candidate),
        "config-1-1",
    )
    options = {"env": env, "compose": tmp_path / "compose.yaml", "expected_uid": os.getuid()}
    if failure:
        with pytest.raises(ValueError, match="restored") as caught:
            module.update(*arguments, **options)
        assert "synthetic-new" not in str(caught.value)
        assert env.read_bytes() == old
    else:
        module.update(*arguments, **options)
        module.update(*arguments, **options)
        assert env.read_bytes() == candidate
    up = [item for item in commands if "up" in item]
    assert len(up) == (2 if failure else 1)
    assert all(item[-1] == "ark-mcp" and "--no-deps" in item for item in up)
    assert (directory / "installation.sha256").read_text() == configuration_digest(env.read_bytes())
    assert not list(directory.glob("*.backup"))


def test_host_rejects_nonprovider_changes_before_process_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = helper("update_config")
    monkeypatch.setitem(sys.modules, "check_env", helper("check_env"))
    receiver = helper("receive_config")
    monkeypatch.setitem(sys.modules, "receive_config", receiver)
    directory = tmp_path / "config"
    directory.mkdir(mode=0o700)
    env = directory / ".env"
    old, _ = make_configuration_values(
        {"BYTEPLUS_MODELARK_API_KEY": "synthetic-original"}, "mcp.team.test"
    )
    candidate = old.replace(b"DAILY_BUDGET_USD=25", b"DAILY_BUDGET_USD=26")
    atomic_private(env, old)
    receiver.receipt(directory, configuration_digest(old))
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: pytest.fail("mutation"))
    with pytest.raises(ValueError, match="provider"):
        module.update(
            candidate,
            configuration_digest(old),
            configuration_digest(candidate),
            "config-1-1",
            env=env,
            expected_uid=os.getuid(),
        )
    assert env.read_bytes() == old


@pytest.mark.parametrize("activated", [False, True])
def test_transport_failure_recovers_the_same_host_transaction(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, activated: bool
) -> None:
    supplied = args(tmp_path, monkeypatch)
    planned = configuration.plan(workspace, supplied)
    supplied.approve_config = planned["approve_config"]
    original = (workspace.path / "host.env").read_bytes()
    candidate = (workspace.path / "config.candidate.env").read_bytes()
    directory = tmp_path / "guest"
    directory.mkdir(mode=0o700)
    env = directory / ".env"
    atomic_private(env, original)
    receiver = helper("receive_config")
    updater = helper("update_config")
    monkeypatch.setitem(sys.modules, "check_env", helper("check_env"))
    monkeypatch.setitem(sys.modules, "receive_config", receiver)
    receiver.receipt(directory, configuration_digest(original))
    monkeypatch.setattr(
        updater.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    cloud = SimpleNamespace(verify_instance=lambda *args: None, close=lambda: None)
    monkeypatch.setattr(configuration, "Cloud", lambda _: cloud)
    monkeypatch.setattr(cli, "verify_deployment", AsyncMock())
    host_operations = []
    transfers = []
    cleanups = []

    class Guest:
        def operation(self, name: str, command: str, **kwargs: Any) -> str:
            if name.endswith("-cleanup"):
                cleanups.append(command)
                return "{}"
            return json.dumps({"status": "applied" if activated else "pending"})

        def transfer(self, name: str, payload: bytes, arguments: list[str], **kwargs: Any) -> None:
            operation = arguments[arguments.index("--operation") + 1]
            host_operations.append(operation)
            transfers.append(name)
            if len(transfers) == 1:
                if activated:
                    updater.update(
                        payload,
                        configuration_digest(original),
                        configuration_digest(candidate),
                        operation,
                        env=env,
                        expected_uid=os.getuid(),
                    )
                else:
                    atomic_private(directory / f".{operation}.backup", original)
                    atomic_private(
                        directory / f".{operation}.json",
                        json.dumps(
                            {
                                "previous_digest": configuration_digest(original),
                                "next_digest": configuration_digest(candidate),
                                "status": "pending",
                            }
                        ).encode(),
                    )
                    atomic_private(env, payload)
                journal = workspace.journal()
                journal["operations"][name] = {"status": "failed", "id": "ivk-demo"}
                workspace.save_journal(journal)
                raise ValueError("known failed transport")
            updater.update(
                payload,
                configuration_digest(original),
                configuration_digest(candidate),
                operation,
                env=env,
                expected_uid=os.getuid(),
            )
            journal = workspace.journal()
            journal["operations"][name] = {"status": "succeeded"}
            workspace.save_journal(journal)

    monkeypatch.setattr(cli, "Guest", lambda *args, **kwargs: Guest())
    with pytest.raises(ValueError, match="transport"):
        configuration.apply(workspace, supplied)
    supplied.retry_failed = True
    configuration.apply(workspace, supplied)
    assert transfers == ["config-1-1", "config-1-2"]
    assert host_operations == ["config-1-1", "config-1-1"]
    assert cleanups == [cli.transfer_cleanup_command("config-1-1")]
    assert env.read_bytes() == (workspace.path / "host.env").read_bytes() == candidate


@pytest.mark.parametrize("discard", [False, True])
@pytest.mark.parametrize("filename", ["config.previous.env", "config.candidate.env"])
def test_interrupted_staged_secret_cleanup_is_resumable_without_cloud_access(
    workspace: Workspace,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    discard: bool,
    filename: str,
) -> None:
    supplied = args(tmp_path, monkeypatch)
    planned = configuration.plan(workspace, supplied)
    supplied.approve_config = planned["approve_config"]
    candidate = (workspace.path / "config.candidate.env").read_bytes()
    if not discard:
        journal = workspace.journal()
        journal["config_update"]["status"] = "committing"
        workspace.save_journal(journal)
    unlink = Path.unlink
    interrupted = False

    def fail_once(path: Path, *args: Any, **kwargs: Any) -> None:
        nonlocal interrupted
        if path == workspace.path / filename and not interrupted:
            interrupted = True
            raise OSError("cleanup interrupted")
        unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_once)
    with pytest.raises(OSError, match="cleanup"):
        if discard:
            configuration.discard(workspace, supplied)
        else:
            configuration.apply(workspace, supplied)
    assert workspace.journal().get("config_update")
    monkeypatch.setattr(configuration, "Cloud", lambda _: pytest.fail("cloud access"))
    monkeypatch.setattr(configuration, "fetch_provider_keys", lambda _: pytest.fail("KMS read"))
    cli.resume_deployment(workspace, argparse.Namespace(retry_failed=False))
    assert "config_update" not in workspace.journal()
    assert not (workspace.path / "config.previous.env").exists()
    assert not (workspace.path / "config.candidate.env").exists()
    if not discard:
        assert (workspace.path / "host.env").read_bytes() == candidate


def test_initial_secret_deployment_reuses_keys_and_does_not_reread_kms(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (workspace.path / "host.env").unlink()
    (workspace.path / "team.key").unlink()
    reference = tmp_path / "reference.json"
    atomic_private(reference, json.dumps(REFERENCE).encode())
    calls = []

    def fetch(source: Any) -> dict[str, str]:
        calls.append(source)
        return {"BYTEPLUS_MODELARK_API_KEY": "synthetic-initial"}

    monkeypatch.setattr(cli, "fetch_provider_keys", fetch)
    supplied = argparse.Namespace(provider_env_file=None, secret_reference_file=reference)
    cli.prepare_configuration(workspace, supplied)
    original = (workspace.path / "host.env").read_bytes()
    team = (workspace.path / "team.key").read_bytes()
    cli.prepare_configuration(workspace, supplied)
    assert len(calls) == 1
    assert workspace.journal()["provider_secret_source"] == REFERENCE
    assert (workspace.path / "host.env").read_bytes() == original
    assert (workspace.path / "team.key").read_bytes() == team


def test_real_cli_config_status_and_help_never_read_kms_or_print_secrets(
    workspace: Workspace,
) -> None:
    entrypoint = ROOT / "scripts/deploy_byteplus.py"
    result = subprocess.run(
        [sys.executable, str(entrypoint), "config", "status", "--workspace", str(workspace.path)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["configured_keys"]["BYTEPLUS_MODELARK_API_KEY"] is True
    assert "synthetic-original" not in result.stdout + result.stderr
    help_result = subprocess.run(
        [sys.executable, str(entrypoint), "config", "plan", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--secret-reference-file" in help_result.stdout


def test_unapproved_config_plan_cannot_be_applied_by_resume(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration.plan(workspace, args(tmp_path, monkeypatch))
    monkeypatch.setattr(configuration, "Cloud", lambda _: pytest.fail("cloud access"))
    with pytest.raises(ValueError, match="Unapproved"):
        cli.resume_deployment(workspace, argparse.Namespace(retry_failed=False))


def test_pending_config_blocks_image_updates_and_premature_verification(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configuration.plan(workspace, args(tmp_path, monkeypatch))
    with pytest.raises(ValueError, match="pending"):
        cli.update_deployment(workspace, argparse.Namespace())
    with pytest.raises(ValueError, match="pending"):
        cli.asyncio.run(cli.verify_deployment(workspace))


def test_host_transaction_inspection_verifies_receipts_without_values(tmp_path: Path) -> None:
    updater = helper("update_config")
    original, _ = make_configuration_values(
        {"BYTEPLUS_MODELARK_API_KEY": "synthetic-original"}, "mcp.team.test"
    )
    candidate = replace_provider_keys(original, {"BYTEPLUS_MODELARK_API_KEY": "synthetic-new"})
    env = tmp_path / ".env"
    atomic_private(env, candidate)
    atomic_private(tmp_path / "installation.sha256", configuration_digest(original).encode())
    atomic_private(tmp_path / ".config-1-1.backup", original)
    atomic_private(
        tmp_path / ".config-1-1.json",
        json.dumps(
            {
                "status": "pending",
                "previous_digest": configuration_digest(original),
                "next_digest": configuration_digest(candidate),
            }
        ).encode(),
    )
    result = updater.inspect_transaction(
        configuration_digest(original),
        configuration_digest(candidate),
        "config-1-1",
        env=env,
        expected_uid=os.getuid(),
    )
    assert result == {"status": "pending"}
    assert "synthetic-" not in json.dumps(result)
    atomic_private(tmp_path / ".config-1-1.backup", b"wrong")
    with pytest.raises(ValueError, match="backup"):
        updater.inspect_transaction(
            configuration_digest(original),
            configuration_digest(candidate),
            "config-1-1",
            env=env,
            expected_uid=os.getuid(),
        )


@pytest.mark.parametrize("status", ["applied", "rolled_back"])
def test_terminal_host_reconciliation_durably_removes_staged_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
) -> None:
    updater = helper("update_config")
    receiver = helper("receive_config")
    monkeypatch.setitem(sys.modules, "receive_config", receiver)
    original, _ = make_configuration_values(
        {"BYTEPLUS_MODELARK_API_KEY": "synthetic-original"}, "mcp.team.test"
    )
    candidate = replace_provider_keys(original, {"BYTEPLUS_MODELARK_API_KEY": "synthetic-new"})
    current = candidate if status == "applied" else original
    env = tmp_path / ".env"
    atomic_private(env, current)
    receiver.receipt(tmp_path, configuration_digest(current))
    backup = tmp_path / ".config-1-1.backup"
    staged = tmp_path / ".config-1-1.candidate"
    atomic_private(backup, original)
    atomic_private(staged, candidate)
    atomic_private(
        tmp_path / ".config-1-1.json",
        json.dumps(
            {
                "status": status,
                "previous_digest": configuration_digest(original),
                "next_digest": configuration_digest(candidate),
            }
        ).encode(),
    )
    synced = []
    sync = receiver.sync_directory

    def record_sync(path: Path) -> None:
        synced.append((backup.exists(), staged.exists()))
        sync(path)

    monkeypatch.setattr(receiver, "sync_directory", record_sync)
    assert updater.inspect_transaction(
        configuration_digest(original),
        configuration_digest(candidate),
        "config-1-1",
        env=env,
        expected_uid=os.getuid(),
    ) == {"status": status}
    assert not backup.exists() and not staged.exists()
    assert synced[-1] == (False, False)


@pytest.mark.parametrize("replay", [False, True])
def test_successful_host_update_syncs_after_every_secret_file_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replay: bool
) -> None:
    updater = helper("update_config")
    receiver = helper("receive_config")
    monkeypatch.setitem(sys.modules, "receive_config", receiver)
    monkeypatch.setitem(sys.modules, "check_env", helper("check_env"))
    original, _ = make_configuration_values(
        {"BYTEPLUS_MODELARK_API_KEY": "synthetic-original"}, "mcp.team.test"
    )
    candidate = replace_provider_keys(original, {"BYTEPLUS_MODELARK_API_KEY": "synthetic-new"})
    env = tmp_path / ".env"
    atomic_private(env, original)
    receiver.receipt(tmp_path, configuration_digest(original))
    monkeypatch.setattr(updater.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    arguments = (
        candidate,
        configuration_digest(original),
        configuration_digest(candidate),
        "config-1-1",
    )
    if replay:
        updater.update(*arguments, env=env, expected_uid=os.getuid())
        atomic_private(tmp_path / ".config-1-1.backup", original)
        atomic_private(tmp_path / ".config-1-1.candidate", candidate)
    sync = receiver.sync_directory
    synced = []

    def record_sync(path: Path) -> None:
        synced.append(
            sorted(item.name for item in path.glob(".config-1-1.*") if item.suffix != ".json")
        )
        sync(path)

    monkeypatch.setattr(receiver, "sync_directory", record_sync)
    updater.update(*arguments, env=env, expected_uid=os.getuid())
    assert synced[-1] == []


@pytest.mark.parametrize("failure", [False, True])
def test_provider_receive_destroys_decryptable_transfer_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: bool
) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from scripts.byteplus_deploy.private import encrypt_payload

    receiver = helper("receive_config")
    directory = tmp_path / (".transfer-" + "a" * 24)
    directory.mkdir(mode=0o700)
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    key_path = directory / "key.pem"
    atomic_private(
        key_path,
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    envelope = directory / "envelope.json"
    content = json.dumps(encrypt_payload(b"synthetic-private-config", public, "i-demo")).encode()
    atomic_private(envelope, content)
    atomic_private(directory / "envelope.json.b64", content)
    real_uid = os.getuid()
    cleanup = receiver.cleanup_transfer
    monkeypatch.setattr(
        receiver, "cleanup_transfer", lambda path: cleanup(path, expected_uid=real_uid)
    )
    monkeypatch.setattr(receiver, "CONFIG", tmp_path)
    monkeypatch.setattr(receiver.os, "getuid", lambda: 0)
    monkeypatch.setattr(receiver.subprocess, "run", lambda *a, **kw: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "receive_config.py",
            "--instance-id",
            "i-demo",
            "--key",
            str(key_path),
            "--envelope",
            str(envelope),
            "--kind",
            "provider-update",
        ],
    )

    def update(payload: bytes, *arguments: Any) -> None:
        assert payload == b"synthetic-private-config"
        if failure:
            raise ValueError("startup failed")

    monkeypatch.setitem(sys.modules, "update_config", SimpleNamespace(update=update))
    if failure:
        with pytest.raises(ValueError, match="startup"):
            receiver.main()
    else:
        receiver.main()
    assert not directory.exists()


@pytest.mark.parametrize("unsafe", ["symlink", "extra", "mode", "path"])
def test_transfer_cleanup_rejects_unmanaged_or_unprotected_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe: str
) -> None:
    receiver = helper("receive_config")
    monkeypatch.setattr(receiver, "CONFIG", tmp_path)
    directory = tmp_path / (".transfer-" + "b" * 24)
    directory.mkdir(mode=0o700)
    key = directory / "key.pem"
    atomic_private(key, b"synthetic-private-key")
    if unsafe == "symlink":
        (directory / "envelope.json").symlink_to(key)
    elif unsafe == "extra":
        atomic_private(directory / "unmanaged", b"preserve")
    elif unsafe == "mode":
        key.chmod(0o644)
    else:
        directory = tmp_path
    with pytest.raises((ValueError, OSError)):
        receiver.cleanup_transfer(directory, expected_uid=os.getuid())
    assert key.exists()


def test_transfer_cleanup_is_durable_and_resumes_partial_deletion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receiver = helper("receive_config")
    monkeypatch.setattr(receiver, "CONFIG", tmp_path)
    directory = tmp_path / (".transfer-" + "b" * 24)
    directory.mkdir(mode=0o700)
    for name in ("key.pem", "envelope.json", "envelope.json.b64"):
        atomic_private(directory / name, b"synthetic-private-evidence")
    unlink = Path.unlink
    interrupted = False

    def fail_once(path: Path, *arguments: Any, **options: Any) -> None:
        nonlocal interrupted
        if path.name == "envelope.json" and not interrupted:
            interrupted = True
            raise OSError("cleanup interrupted")
        unlink(path, *arguments, **options)

    monkeypatch.setattr(Path, "unlink", fail_once)
    with pytest.raises(OSError, match="interrupted"):
        receiver.cleanup_transfer(directory, expected_uid=os.getuid())
    receiver.cleanup_transfer(directory, expected_uid=os.getuid())
    receiver.cleanup_transfer(directory, expected_uid=os.getuid())
    assert not directory.exists()


def test_host_inspection_refuses_concurrent_update(tmp_path: Path) -> None:
    updater = helper("update_config")
    with updater.transaction_lock(tmp_path, os.getuid()), pytest.raises(ValueError, match="active"):
        updater.inspect_transaction(
            "a" * 64, "b" * 64, "config-1-1", env=tmp_path / ".env", expected_uid=os.getuid()
        )


def test_recovery_failed_host_evidence_is_preserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    updater = helper("update_config")
    receiver = helper("receive_config")
    monkeypatch.setitem(sys.modules, "receive_config", receiver)
    old, _ = make_configuration_values(
        {"BYTEPLUS_MODELARK_API_KEY": "synthetic-original"}, "mcp.team.test"
    )
    env = tmp_path / ".env"
    atomic_private(env, old)
    receiver.receipt(tmp_path, configuration_digest(old))
    backup = tmp_path / ".config-1-1.backup"
    atomic_private(backup, old)
    atomic_private(
        tmp_path / ".config-1-1.json",
        json.dumps(
            {
                "status": "recovery_failed",
                "previous_digest": configuration_digest(old),
                "next_digest": "b" * 64,
            }
        ).encode(),
    )
    assert updater.inspect_transaction(
        configuration_digest(old), "b" * 64, "config-1-1", env=env, expected_uid=os.getuid()
    ) == {"status": "recovery_failed"}
    assert backup.read_bytes() == old


@pytest.mark.parametrize("host", [False, True])
def test_hard_crash_atomic_temporaries_are_removed_by_reconciliation(
    workspace: Workspace, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, host: bool
) -> None:
    original = (workspace.path / "host.env").read_bytes()
    directory = tmp_path if host else workspace.path
    target = directory / (".env" if host else "config.candidate.env")
    if host:
        atomic_private(target, original)
        atomic_private(directory / "installation.sha256", configuration_digest(original).encode())
    source = (
        f"sys.path.insert(0, {str(ROOT / 'deploy/terraform/byteplus/scripts')!r}); "
        "from update_config import write_private; "
        f"write_private(Path({str(target)!r}), {original!r}, os.getuid())"
        if host
        else "from scripts.byteplus_deploy.workspace import atomic_private; "
        f"atomic_private(Path({str(target)!r}), {original!r})"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os,sys; from pathlib import Path; "
            "os.replace=lambda *args: os._exit(17); " + source,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 17, result.stderr
    pattern = ".provider-????????" if host else ".deploy-????????"
    assert list(directory.glob(pattern))
    if host:
        receiver = helper("receive_config")
        monkeypatch.setitem(sys.modules, "receive_config", receiver)
        updater = helper("update_config")
        assert updater.inspect_transaction(
            configuration_digest(original),
            "b" * 64,
            "config-1-1",
            env=target,
            expected_uid=os.getuid(),
        ) == {"status": "absent"}
    else:
        supplied = args(tmp_path, monkeypatch)
        planned = configuration.plan(workspace, supplied)
        supplied.approve_config = planned["approve_config"]
        configuration.discard(workspace, supplied)
    assert not list(directory.glob(pattern))
