"""Offline state-machine and real entrypoint checks for deployment automation."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from scripts.byteplus_deploy import cli
from scripts.byteplus_deploy.workspace import ROOT, Workspace, atomic_private

IMAGE = "ghcr.io/example/ark-mcp@sha256:" + "a" * 64


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    result = Workspace.create(
        tmp_path / "deployment",
        {
            "name": "team-demo",
            "zone_id": "ap-southeast-1a",
            "image_id": "image-demo",
            "instance_type": "ecs.c3i.large",
            "key_pair_name": "demo-public-key",
            "ark_mcp_image": IMAGE,
        },
    )
    journal = result.journal()
    journal.update(
        phase="applied",
        source_fingerprint=result.fingerprint(),
        outputs={
            "instance_id": "i-demo",
            "state_volume": {"id": "vol-demo"},
            "public_hostname": "mcp.team.test",
            "mcp_url": "https://mcp.team.test/mcp",
        },
    )
    result.save_journal(journal)
    return result


class FakeCloud:
    def __init__(self, *, accepted: bool = True) -> None:
        self.calls: list[str] = []
        self.accepted = accepted
        self.results = [{"status": "succeeded", "output": "safe", "exit_code": 0}]

    def run_command(self, instance: str, command: str, name: str, timeout: int) -> str:
        self.calls.append(command)
        if not self.accepted:
            raise RuntimeError("Connection lost after submission")
        return "ivk-demo"

    def get_result(self, instance: str, invocation: str) -> dict[str, Any]:
        assert instance == "i-demo" and invocation == "ivk-demo"
        return self.results.pop(0)


def test_ambiguous_guest_acceptance_never_replays(workspace: Workspace) -> None:
    cloud = FakeCloud(accepted=False)
    guest = cli.Guest(workspace, cloud)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        guest.operation("prepare", "safe probe")
    assert workspace.journal()["operations"]["prepare"]["status"] == "submitting"
    with pytest.raises(ValueError, match="ambiguous"):
        guest.operation("prepare", "safe probe")
    assert cloud.calls == ["safe probe"]


def test_pending_invocation_resumes_polling_without_submission(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = "safe probe"
    journal = workspace.journal()
    journal["operations"]["prepare"] = {
        "id": "ivk-demo",
        "status": "working",
        "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
    }
    workspace.save_journal(journal)
    cloud = FakeCloud()
    cloud.results.insert(0, {"status": "working"})
    monkeypatch.setattr(cli.time, "sleep", lambda _: None)
    assert cli.Guest(workspace, cloud).operation("prepare", command) == "safe"  # type: ignore[arg-type]
    assert cloud.calls == []
    assert workspace.journal()["operations"]["prepare"]["status"] == "succeeded"


def test_failed_guest_needs_explicit_retry_and_same_command(workspace: Workspace) -> None:
    cloud = FakeCloud()
    cloud.results = [{"status": "failed", "exit_code": 1, "output": ""}]
    guest = cli.Guest(workspace, cloud)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="failed"):
        guest.operation("prepare", "safe probe")
    with pytest.raises(ValueError, match="retry-failed"):
        guest.operation("prepare", "safe probe")
    cloud.results = [{"status": "succeeded", "output": "", "exit_code": 0}]
    retry = cli.Guest(workspace, cloud, retry_failed=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="differs"):
        retry.operation("prepare", "different command")
    retry.operation("prepare", "safe probe")
    assert len(cloud.calls) == 2


def test_poll_timeout_preserves_accepted_id(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloud = FakeCloud()
    cloud.results = [{"status": "working"}]
    clock = iter([0, 1000])
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(clock))
    with pytest.raises(ValueError, match="still pending"):
        cli.Guest(workspace, cloud).operation("prepare", "safe probe", timeout=1)  # type: ignore[arg-type]
    assert workspace.journal()["operations"]["prepare"]["id"] == "ivk-demo"
    assert len(cloud.calls) == 1


@pytest.mark.parametrize("actions", [["delete"], ["delete", "create"], ["create", "delete"]])
def test_plan_rejects_deletes_and_replacements(actions: list[str]) -> None:
    with pytest.raises(ValueError, match="deletion"):
        cli.reject_destructive_changes(
            {"resource_changes": [{"address": "vm", "change": {"actions": actions}}]}
        )


def test_noninteractive_apply_needs_exact_approval(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    atomic_private(workspace.path / "deployment.tfplan", b"saved binary plan")
    journal = workspace.journal()
    journal.update(phase="planned", approval=cli.approval_digest(workspace))
    workspace.save_journal(journal)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    args = argparse.Namespace(approve_plan="wrong", terraform="unused")
    with pytest.raises(ValueError, match="Exact"):
        cli.apply_deployment(workspace, args)
    assert workspace.journal()["phase"] == "planned"
    atomic_private(workspace.module / "terraform.tfstate", b"changed")
    args.approve_plan = journal["approval"]
    with pytest.raises(ValueError, match="stale"):
        cli.apply_deployment(workspace, args)


def test_quota_failure_preserves_state_and_does_not_retry(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def failed(*args: Any, **kwargs: Any) -> SimpleNamespace:
        calls.append(args)
        return SimpleNamespace(returncode=1, stdout="", stderr="QuotaExceeded")

    atomic_private(workspace.module / "terraform.tfstate", b"partial state")
    monkeypatch.setattr(cli.subprocess, "run", failed)
    with pytest.raises(ValueError, match="quota"):
        cli.terraform(workspace, "terraform", ["apply"])
    assert len(calls) == 1
    assert (workspace.module / "terraform.tfstate").read_bytes() == b"partial state"


def test_secrets_reused_and_mismatch_refused(workspace: Workspace, tmp_path: Path) -> None:
    provider = tmp_path / "provider.env"
    atomic_private(provider, b"BYTEPLUS_MODELARK_API_KEY=synthetic-provider\n")
    args = argparse.Namespace(provider_env_file=provider)
    cli.prepare_configuration(workspace, args)
    original = (workspace.path / "host.env").read_bytes()
    key = (workspace.path / "team.key").read_bytes()
    args.provider_env_file = None
    cli.prepare_configuration(workspace, args)
    assert (workspace.path / "host.env").read_bytes() == original
    assert (workspace.path / "team.key").read_bytes() == key
    atomic_private(workspace.path / "team.key", b"incorrect\n")
    with pytest.raises(ValueError, match="differ"):
        cli.prepare_configuration(workspace, args)
    assert (workspace.path / "host.env").read_bytes() == original


def test_completed_transfer_does_not_recreate_deleted_ephemeral_keys(
    workspace: Workspace,
) -> None:
    journal = workspace.journal()
    journal["operations"]["configuration"] = {"status": "succeeded"}
    workspace.save_journal(journal)
    cloud = FakeCloud()
    cli.Guest(workspace, cloud).transfer("configuration", b"private", [])  # type: ignore[arg-type]
    assert cloud.calls == []


def test_failed_image_uses_new_transfer_without_rotating_configuration(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = workspace.journal()
    journal["operations"]["image-1"] = {"status": "failed"}
    workspace.save_journal(journal)
    atomic_private(workspace.path / "host.env", b"retained secret")
    transfers = []
    guest = SimpleNamespace(transfer=lambda *args: transfers.append(args))
    monkeypatch.setattr(cli, "check_image", lambda *args: None)
    args = argparse.Namespace(retry_failed=True, registry_user=None, registry_token_file=None)
    cli.image_phase(workspace, guest, args, IMAGE)  # type: ignore[arg-type]
    assert transfers[0][0] == "image-2"
    assert (workspace.path / "host.env").read_bytes() == b"retained secret"


def test_update_sequence_preserves_infrastructure_and_secrets(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = workspace.journal()
    journal["phase"] = "ready"
    workspace.save_journal(journal)
    atomic_private(workspace.path / "host.env", b"retained configuration")
    operations = []
    cloud = SimpleNamespace(verify_instance=lambda *args: None)
    monkeypatch.setattr(cli, "Cloud", lambda *args: cloud)
    guest = SimpleNamespace(operation=lambda *args, **kwargs: operations.append((args, kwargs)))
    monkeypatch.setattr(cli, "Guest", lambda *args, **kwargs: guest)
    monkeypatch.setattr(cli, "image_phase", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "verify_deployment", AsyncMock())
    args = argparse.Namespace(approve_update=True, image=IMAGE, retry_failed=False)
    cli.update_deployment(workspace, args)
    cli.update_deployment(workspace, args)
    assert [entry[0][0] for entry in operations] == ["update-1-recreate", "update-2-recreate"]
    assert all(entry[1]["timeout"] == 2400 for entry in operations)
    assert (workspace.path / "host.env").read_bytes() == b"retained configuration"
    assert not (workspace.module / "terraform.tfvars.json").exists()
    assert workspace.journal()["runtime_image"] == IMAGE


def test_entrypoint_help_and_private_status_smoke(workspace: Workspace) -> None:
    entrypoint = ROOT / "scripts/deploy_byteplus.py"
    help_result = subprocess.run(
        [sys.executable, str(entrypoint), "--help"], capture_output=True, text=True, check=True
    )
    assert "resume" in help_result.stdout and "update" in help_result.stdout
    atomic_private(workspace.path / "team.key", b"synthetic-secret-must-not-print")
    result = subprocess.run(
        [sys.executable, str(entrypoint), "status", "--workspace", str(workspace.path)],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["phase"] == "applied"
    assert "synthetic-secret" not in result.stdout + result.stderr


def test_standalone_import_does_not_initialize_server() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from scripts.byteplus_deploy import cli; "
            "assert 'ark_mcp.server' not in sys.modules; print('standalone')",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "standalone"


def test_entrypoint_initializes_real_private_workspace(
    workspace: Workspace, tmp_path: Path
) -> None:
    config = tmp_path / "config.json"
    atomic_private(config, json.dumps(workspace.config).encode())
    destination = tmp_path / "initialized-by-cli"
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/deploy_byteplus.py"),
            "init",
            "--workspace",
            str(destination),
            "--config",
            str(config),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "initialized" in result.stdout
    initialized = Workspace(destination)
    assert initialized.fingerprint()
    assert initialized.journal()["phase"] == "initialized"


def test_unmanaged_state_refused_before_cloud_access(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = workspace.journal()
    journal["phase"] = "initialized"
    workspace.save_journal(journal)
    atomic_private(workspace.module / "terraform.tfstate", b"foreign state")
    with pytest.raises(ValueError, match="Unmanaged"):
        cli.plan_deployment(workspace, argparse.Namespace())


def test_named_terraform_state_is_refused_before_cloud_access(
    workspace: Workspace,
) -> None:
    journal = workspace.journal()
    journal["phase"] = "initialized"
    workspace.save_journal(journal)
    named = workspace.module / "terraform.tfstate.d/foreign"
    named.mkdir(parents=True)
    atomic_private(named / "terraform.tfstate", b"foreign state")
    with pytest.raises(ValueError, match="workspace"):
        cli.plan_deployment(workspace, argparse.Namespace())


def test_terraform_forces_default_workspace(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TF_WORKSPACE", "foreign")
    environments = []

    def run(*args: Any, **kwargs: Any) -> SimpleNamespace:
        environments.append(kwargs["env"])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(cli.subprocess, "run", run)
    cli.terraform(workspace, "terraform", ["validate"])
    assert environments[0]["TF_WORKSPACE"] == "default"


def test_resume_dispatches_recorded_update(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    journal = workspace.journal()
    journal["update"] = {"image": IMAGE, "sequence": 1}
    workspace.save_journal(journal)
    invoked = []
    monkeypatch.setattr(cli, "update_deployment", lambda ws, args: invoked.append(args.image))
    monkeypatch.setattr(cli, "finish_deployment", lambda *args: pytest.fail("initial flow"))
    args = argparse.Namespace(retry_failed=False)
    cli.resume_deployment(workspace, args)
    assert invoked == [IMAGE] and args.approve_update is True


def test_failed_chunk_starts_fresh_transfer_only_after_reconciliation(
    workspace: Workspace,
) -> None:
    journal = workspace.journal()
    journal["operations"]["configuration-1-key"] = {"status": "succeeded"}
    journal["operations"]["configuration-1-chunk-0"] = {"status": "failed", "id": "ivk-demo"}
    workspace.save_journal(journal)
    assert cli.transfer_attempt(workspace, "configuration", retry_failed=False) == "configuration-1"
    assert cli.transfer_attempt(workspace, "configuration", retry_failed=True) == "configuration-2"
    journal = workspace.journal()
    journal["operations"]["configuration-2-chunk-0"] = {"status": "submitting"}
    journal["operations"]["configuration-2-key"] = {"status": "failed"}
    workspace.save_journal(journal)
    assert cli.transfer_attempt(workspace, "configuration", retry_failed=True) == "configuration-2"


def test_initial_flow_starts_guarded_docker_before_pull(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    provider = tmp_path / "providers"
    atomic_private(provider, b"BYTEPLUS_MODELARK_API_KEY=synthetic-provider\n")
    calls = []
    cloud = SimpleNamespace(verify_instance=lambda *args: calls.append("identity"))
    guest = SimpleNamespace(
        operation=lambda name, *args, **kwargs: calls.append(name),
        transfer=lambda *args: calls.append("configuration"),
    )
    monkeypatch.setattr(cli, "Cloud", lambda *args: cloud)
    monkeypatch.setattr(cli, "Guest", lambda *args, **kwargs: guest)
    monkeypatch.setattr(cli, "image_phase", lambda *args: calls.append("pull"))
    monkeypatch.setattr(cli, "verify_deployment", AsyncMock())
    cli.finish_deployment(
        workspace, argparse.Namespace(retry_failed=False, provider_env_file=provider)
    )
    assert calls == ["identity", "prepare", "configuration", "docker-start", "pull", "activate"]


def test_verification_rounds_submit_fresh_nonbillable_jobs(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    import fastmcp
    import httpx

    submitted = []
    clients = []
    atomic_private(workspace.path / "team.key", b"synthetic-team-key\n")

    class Probe:
        def __enter__(self) -> Probe:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def get(self, url: str) -> httpx.Response:
            status = 200 if url.endswith("/health") else 404
            return httpx.Response(
                status, json={"status": "healthy"}, request=httpx.Request("GET", url)
            )

        def post(self, url: str, **kwargs: Any) -> httpx.Response:
            status = 403 if kwargs["headers"].get("Origin") else 401
            return httpx.Response(status, request=httpx.Request("POST", url))

    class Client:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            clients.append(self)

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def list_tools(self) -> list[SimpleNamespace]:
            return [SimpleNamespace(name="seedream_generate_image")]

        async def call_tool(self, name: str, arguments: dict[str, Any]) -> SimpleNamespace:
            if name == "ark_job_submit":
                assert arguments["input"]["arguments"]["input"]["dry_run"] is True
                submitted.append(arguments)
                return SimpleNamespace(structured_content={"job_id": f"job-{len(submitted)}"})
            if name == "ark_job_get":
                return SimpleNamespace(
                    structured_content={
                        "status": "completed",
                        "result": {"is_error": False, "structured_content": {"dry_run": True}},
                    }
                )
            return SimpleNamespace(structured_content={})

    monkeypatch.setattr(cli.httpx, "Client", lambda **kwargs: Probe())
    monkeypatch.setattr(fastmcp, "Client", Client)
    cli.asyncio.run(cli.verify_deployment(workspace))
    cli.asyncio.run(cli.verify_deployment(workspace))
    assert len(submitted) == 2 and len(clients) == 4
    assert workspace.journal()["verification"]["sequence"] == 2
    assert workspace.journal()["verification"]["job_id"] == "job-2"
    assert "verification_pending" not in workspace.journal()
