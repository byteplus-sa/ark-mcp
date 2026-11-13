"""Reviewed Terraform deployment followed by resumable ECS-agent operations."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import ssl
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from .cloud import Cloud
from .private import (
    configuration_digest,
    encrypt_payload,
    make_configuration,
    make_configuration_values,
    validator,
)
from .registry import check_image
from .secrets import SecretReference, fetch_provider_keys
from .workspace import Workspace, atomic_private, read_private, validate_config


def terraform(workspace: Workspace, binary: str, arguments: list[str]) -> str:
    require_default_workspace(workspace)
    environment = dict(os.environ)
    for key in list(environment):
        if key.startswith(("TF_LOG", "TF_CLI_ARGS")):
            environment.pop(key)
    environment.pop("TF_DATA_DIR", None)
    environment["TF_WORKSPACE"] = "default"
    mask = os.umask(0o077)
    try:
        result = subprocess.run(
            [binary, f"-chdir={workspace.module}", *arguments],
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )
    finally:
        os.umask(mask)
    if result.returncode:
        if "QuotaExceeded" in result.stderr:
            raise ValueError(
                "BytePlus quota exhausted. Partial state is preserved; explicitly select an existing network or resolve EIP quota, then review a new plan."
            )
        raise ValueError(
            "Terraform failed. State is preserved; inspect the plan/provider configuration without enabling secret-bearing debug logs."
        )
    return result.stdout


def require_terraform(binary: str) -> None:
    result = subprocess.run(
        [binary, "version", "-json"], capture_output=True, text=True, check=True
    )
    version = json.loads(result.stdout)["terraform_version"].split(".")
    if int(version[0]) != 1 or int(version[1]) < 9:
        raise ValueError("Terraform >=1.9 and <2.0 is required.")


def reject_destructive_changes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    changes = []
    for resource in plan.get("resource_changes", []):
        actions = resource["change"]["actions"]
        if "delete" in actions:
            raise ValueError("Plan contains deletion or replacement; this CLI will not apply it.")
        if actions not in (["no-op"], ["read"]):
            changes.append({"address": resource["address"], "actions": actions})
    return changes


def approval_digest(workspace: Workspace) -> str:
    require_default_workspace(workspace)
    digest = hashlib.sha256(workspace.fingerprint().encode())
    digest.update(read_private(workspace.path / "deployment.tfplan", limit=16777216))
    state = workspace.module / "terraform.tfstate"
    if state.exists():
        digest.update(read_private(state, limit=16777216))
    access_key = os.environ.get("BYTEPLUS_ACCESS_KEY", "")
    digest.update(hashlib.sha256(access_key.encode()).digest())
    return digest.hexdigest()


def require_default_workspace(workspace: Workspace) -> None:
    selected = workspace.module / ".terraform/environment"
    named = workspace.module / "terraform.tfstate.d"
    if selected.exists() and read_private(selected, limit=1024).strip() != b"default":
        raise ValueError("Nondefault Terraform workspace found; this CLI does not adopt it.")
    if named.is_symlink() or (named.exists() and any(named.iterdir())):
        raise ValueError("Named Terraform workspace state found; this CLI does not adopt it.")


def registry_credentials(args: argparse.Namespace) -> tuple[str | None, str | None]:
    path = getattr(args, "registry_token_file", None)
    username = getattr(args, "registry_user", None)
    if path is None:
        if username:
            raise ValueError("Registry user requires a protected token file.")
        return None, None
    token = read_private(path).decode().strip()
    if (
        not username
        or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", username)
        or not token
        or any(c in token for c in "\r\n")
    ):
        raise ValueError("Registry credentials are incomplete or malformed.")
    return username, token


def plan_deployment(workspace: Workspace, args: argparse.Namespace) -> dict[str, Any]:
    require_default_workspace(workspace)
    journal = workspace.journal()
    if (workspace.module / "terraform.tfstate").exists() and not journal.get("terraform_started"):
        raise ValueError("Unmanaged Terraform state found; this CLI does not adopt resources.")
    if journal.get("phase") == "applying":
        raise ValueError(
            "Previous Terraform acceptance is ambiguous; reconcile its state before replanning."
        )
    if journal.get("phase") in ("applied", "ready"):
        raise ValueError(
            "Existing deployment: use update for application changes; infrastructure maintenance needs a separate reviewed workflow."
        )
    username, token = registry_credentials(args)
    cloud = Cloud(workspace.config["region"])
    checks = cloud.preflight(workspace.config)
    check_image(workspace.config["ark_mcp_image"], username, token)
    require_terraform(args.terraform)
    atomic_private(
        workspace.module / "terraform.tfvars.json", json.dumps(workspace.config).encode()
    )
    terraform(
        workspace, args.terraform, ["init", "-input=false", "-no-color", "-lockfile=readonly"]
    )
    terraform(workspace, args.terraform, ["validate", "-no-color"])
    terraform(
        workspace,
        args.terraform,
        ["plan", "-input=false", "-no-color", f"-out={workspace.path / 'deployment.tfplan'}"],
    )
    plan = json.loads(
        terraform(
            workspace, args.terraform, ["show", "-json", str(workspace.path / "deployment.tfplan")]
        )
    )
    changes = reject_destructive_changes(plan)
    digest = approval_digest(workspace)
    journal.update(phase="planned", approval=digest, source_fingerprint=workspace.fingerprint())
    workspace.save_journal(journal)
    return {
        "preflight": checks,
        "changes": changes,
        "approve_plan": digest,
        "charges": "Compute, disks, EIP and traffic remain chargeable. Exact account pricing is not estimated.",
    }


def apply_deployment(workspace: Workspace, args: argparse.Namespace) -> None:
    journal = workspace.journal()
    if journal.get("phase") != "planned" or approval_digest(workspace) != journal.get("approval"):
        raise ValueError("Plan is missing or stale; review a fresh plan before applying.")
    approval = args.approve_plan
    if approval is None and sys.stdin.isatty():
        answer = input(
            f"Create/update chargeable infrastructure for {workspace.config['name']}? Type the deployment name: "
        )
        if answer == workspace.config["name"]:
            approval = journal["approval"]
    if approval != journal["approval"]:
        raise ValueError("Exact --approve-plan digest required outside interactive approval.")
    require_terraform(args.terraform)
    journal["phase"] = "applying"
    journal["terraform_started"] = True
    workspace.save_journal(journal)
    try:
        terraform(
            workspace,
            args.terraform,
            ["apply", "-input=false", "-no-color", str(workspace.path / "deployment.tfplan")],
        )
    except Exception:
        journal["phase"] = "apply_failed"
        workspace.save_journal(journal)
        raise
    outputs = json.loads(terraform(workspace, args.terraform, ["output", "-json"]))
    required = ("instance_id", "state_volume", "public_ip", "public_hostname", "mcp_url")
    journal["outputs"] = {name: outputs[name]["value"] for name in required}
    journal["phase"] = "applied"
    workspace.save_journal(journal)


def transfer_cleanup_command(name: str) -> str:
    label = hashlib.sha256(name.encode()).hexdigest()[:24]
    return shlex.join(
        [
            "python3",
            "/opt/ark-mcp/receive_config.py",
            "--cleanup-transfer",
            f"/srv/ark-mcp/config/.transfer-{label}",
        ]
    )


class Guest:
    """Journal accepted command IDs and never replay an ambiguous submission."""

    def __init__(self, workspace: Workspace, cloud: Cloud, *, retry_failed: bool = False) -> None:
        self.workspace = workspace
        self.cloud = cloud
        self.retry_failed = retry_failed
        self.instance = workspace.journal()["outputs"]["instance_id"]

    def operation(self, name: str, command: str, *, timeout: int = 600) -> str:
        journal = self.workspace.journal()
        operations = journal["operations"]
        digest = hashlib.sha256(command.encode()).hexdigest()
        operation = operations.get(name)
        if operation and operation["command_sha256"] != digest:
            raise ValueError("Recorded guest operation differs; refusing replay.")
        if operation and operation["status"] == "succeeded":
            return str(operation.get("output", ""))
        if operation and not operation.get("id"):
            raise ValueError(
                "Guest submission acceptance is ambiguous; reconcile the ECS invocation before resuming."
            )
        if operation and operation["status"] == "failed":
            if not self.retry_failed:
                raise ValueError(
                    "Recorded guest operation failed; inspect its error code and use --retry-failed only after reconciliation."
                )
            operation = None
        if operation is None:
            operation = {"status": "submitting", "command_sha256": digest}
            operations[name] = operation
            self.workspace.save_journal(journal)
            invocation = self.cloud.run_command(self.instance, command, name, timeout)
            operation.update(id=invocation, status="working")
            self.workspace.save_journal(journal)
        deadline = time.monotonic() + timeout + 120
        while True:
            result = self.cloud.get_result(self.instance, operation["id"])
            if result["status"] != "working":
                operation.update(result)
                self.workspace.save_journal(journal)
                if result["status"] != "succeeded":
                    raise ValueError(
                        "Guest command failed; its ID and safe error code remain in the journal."
                    )
                return str(result["output"])
            if time.monotonic() >= deadline:
                raise ValueError(
                    "Guest command is still pending; resume will poll its recorded ID, not resubmit."
                )
            time.sleep(2)

    def transfer(
        self, name: str, payload: bytes, arguments: list[str], *, timeout: int = 900
    ) -> None:
        completed = self.workspace.journal()["operations"].get(name)
        if completed and completed["status"] == "succeeded":
            return
        label = hashlib.sha256(name.encode()).hexdigest()[:24]
        directory = f"/srv/ark-mcp/config/.transfer-{label}"
        keyfile = directory + "/key.pem"
        envelope_path = directory + "/envelope.json"
        key_command = f"""set -eu
umask 077
install -d -m 0700 {directory}
python3 - <<'PY'
import os
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
path = Path({keyfile!r})
if path.exists():
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
else:
    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        stream.flush()
        os.fsync(stream.fileno())
print(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode())
PY
"""
        public = self.operation(name + "-key", key_command).encode()
        local = self.workspace.path / f"{label}.envelope"
        if not local.exists():
            atomic_private(
                local, json.dumps(encrypt_payload(payload, public, self.instance)).encode()
            )
        encoded = base64.b64encode(read_private(local, limit=131072)).decode()
        for index, offset in enumerate(range(0, len(encoded), 2048)):
            chunk = encoded[offset : offset + 2048]
            chunk_command = f"""set -eu
umask 077
python3 - <<'PY'
import os
path = {envelope_path!r} + ".b64"
data = {chunk!r}.encode()
descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
try:
    size = os.fstat(descriptor).st_size
    existing = os.pread(descriptor, len(data), {offset})
    if existing != data:
        if size != {offset}:
            raise RuntimeError("Encrypted chunk differs")
        os.pwrite(descriptor, data, {offset})
        os.fsync(descriptor)
finally:
    os.close(descriptor)
PY
"""
            self.operation(f"{name}-chunk-{index}", chunk_command)
        finalize = f"""set -eu
umask 077
base64 --decode {envelope_path}.b64 > {envelope_path}
python3 /opt/ark-mcp/receive_config.py {shlex.join(["--instance-id", self.instance, "--envelope", envelope_path, "--key", keyfile, *arguments])}
"""
        self.operation(name, finalize, timeout=timeout)


def prepare_configuration(workspace: Workspace, args: argparse.Namespace) -> None:
    env_path, key_path = workspace.path / "host.env", workspace.path / "team.key"
    reference_path = getattr(args, "secret_reference_file", None)
    if env_path.exists() or key_path.exists():
        if not (env_path.exists() and key_path.exists()):
            raise ValueError(
                "Partial local secret creation; recover the original files, never regenerate keys."
            )
        payload = read_private(env_path)
        if workspace.journal().get("configuration_sha256") != configuration_digest(payload):
            raise ValueError("Retained local configuration changed; refusing secret replacement.")
        token = read_private(key_path).decode().strip()
        values = validator().load_environment(env_path, expected_uid=os.getuid())
        validator().validate_environment(values)
        if values["MCP_API_KEY_SHA256"] != configuration_digest(token.encode()):
            raise ValueError("Shared key and retained configuration differ.")
        if values["MCP_PUBLIC_HOST"] != workspace.journal()["outputs"]["public_hostname"]:
            raise ValueError("Retained configuration hostname differs from the deployment.")
        if reference_path is not None and (
            SecretReference.load(reference_path).metadata()
            != workspace.journal().get("provider_secret_source")
        ):
            raise ValueError("Retained secret source differs; use an approved configuration sync.")
        return
    hostname = workspace.journal()["outputs"]["public_hostname"]
    source = None
    if reference_path is not None:
        reference = SecretReference.load(reference_path)
        payload, token = make_configuration_values(fetch_provider_keys(reference), hostname)
        source = reference.metadata()
    elif args.provider_env_file is not None:
        payload, token = make_configuration(args.provider_env_file, hostname)
    else:
        raise ValueError(
            "Provide a protected --provider-env-file or --secret-reference-file for the first deployment."
        )
    atomic_private(key_path, (token + "\n").encode())
    atomic_private(env_path, payload)
    journal = workspace.journal()
    journal["configuration_sha256"] = configuration_digest(payload)
    if source is not None:
        journal["provider_secret_source"] = source
    workspace.save_journal(journal)


def finish_deployment(workspace: Workspace, args: argparse.Namespace) -> None:
    journal = workspace.journal()
    if journal.get("phase") not in ("applied", "ready"):
        raise ValueError(
            "A completed reviewed Terraform apply is required; partial apply needs a fresh reviewed plan."
        )
    if journal.get("source_fingerprint") != workspace.fingerprint():
        raise ValueError(
            "Frozen deployment source/configuration changed; reconcile before resuming."
        )
    outputs = journal["outputs"]
    cloud = Cloud(workspace.config["region"])
    cloud.verify_instance(outputs["instance_id"], outputs["state_volume"]["id"], workspace.config)
    guest = Guest(workspace, cloud, retry_failed=args.retry_failed)
    preparation = shlex.join(
        [
            "python3",
            "/opt/ark-mcp/prepare_state.py",
            "--volume-id",
            outputs["state_volume"]["id"],
            "--size-gib",
            str(workspace.config["data_disk_size_gib"]),
            "--name",
            workspace.config["name"],
        ]
    )
    guest.operation(
        "prepare",
        f"set -eu\nsystemctl is-active ark-mcp-install.service || test -f /var/lib/ark-mcp-installed\npython3 -c 'import cryptography, yaml'\n{preparation}\n",
    )
    prepare_configuration(workspace, args)
    payload = read_private(workspace.path / "host.env")
    phase = transfer_attempt(workspace, "configuration", retry_failed=args.retry_failed)
    guest.transfer(phase, payload, ["--kind", "config", "--digest", configuration_digest(payload)])
    guest.operation("docker-start", "set -eu\nsystemctl start docker.service\n", timeout=900)
    image_phase(workspace, guest, args, workspace.config["ark_mcp_image"])
    guest.operation("activate", "set -eu\n/opt/ark-mcp/activate.sh\n", timeout=900)
    asyncio.run(verify_deployment(workspace))


def image_phase(
    workspace: Workspace,
    guest: Guest,
    args: argparse.Namespace,
    image: str,
    *,
    prefix: str = "image",
) -> None:
    phase = transfer_attempt(workspace, prefix, retry_failed=args.retry_failed)
    journal = workspace.journal()
    final = journal["operations"].get(phase)
    if final and final["status"] == "succeeded":
        return
    if final and final["status"] in ("working", "submitting"):
        username, token = None, None
    else:
        username, token = registry_credentials(args)
        check_image(image, username, token)
    guest.transfer(
        phase,
        json.dumps({"username": username or "", "token": token or ""}).encode(),
        ["--kind", "image", "--image", image],
    )


def transfer_attempt(workspace: Workspace, prefix: str, *, retry_failed: bool) -> str:
    journal = workspace.journal()
    attempt_key = prefix + "-attempt"
    attempt = journal.get(attempt_key, 1)
    phase = f"{prefix}-{attempt}"
    operations = {
        name: entry
        for name, entry in journal["operations"].items()
        if name == phase or name.startswith(phase + "-")
    }
    final = operations.get(phase)
    if not final or final["status"] != "succeeded":
        active = any(entry["status"] in ("submitting", "working") for entry in operations.values())
        failed = any(entry["status"] == "failed" for entry in operations.values())
        if failed and retry_failed and not active:
            attempt += 1
            phase = f"{prefix}-{attempt}"
    journal[attempt_key] = attempt
    workspace.save_journal(journal)
    return phase


async def verify_deployment(workspace: Workspace) -> None:
    from fastmcp import Client

    journal = workspace.journal()
    pending_config = journal.get("config_update")
    if pending_config and pending_config["status"] not in {"verifying", "committing"}:
        raise ValueError(
            "Provider configuration update is pending; apply or resume it before verification."
        )
    update = journal.get("update")
    if (
        update
        and journal["operations"].get(f"update-{update['sequence']}-recreate", {}).get("status")
        != "succeeded"
    ):
        raise ValueError("Image update is pending; resume it before verification.")
    pending = journal.get("verification_pending")
    if pending is None:
        sequence = journal.get("verification_sequence", 0) + 1
        pending = {"sequence": sequence}
        journal.update(verification_sequence=sequence, verification_pending=pending)
        workspace.save_journal(journal)
    url = journal["outputs"]["mcp_url"]
    base = url.removesuffix("/mcp")
    token = read_private(workspace.path / "team.key").decode().strip()
    context = ssl.create_default_context()
    with httpx.Client(verify=context, timeout=20) as probe:
        health = probe.get(base + "/health")
        health.raise_for_status()
        if health.json().get("status") != "healthy":
            raise ValueError("Public health is not healthy.")
        for invalid in (None, "incorrect"):
            headers = {"Accept": "application/json, text/event-stream"}
            if invalid:
                headers["Authorization"] = f"Bearer {invalid}"
            response = probe.post(
                url, headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}
            )
            if response.status_code != 401:
                raise ValueError("Missing/wrong bearer key was not rejected.")
        for path in ("/ready", "/metrics"):
            if probe.get(base + path).status_code not in (403, 404):
                raise ValueError("Internal endpoint is publicly accessible.")
        origin = probe.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": "https://untrusted.invalid",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        if origin.status_code not in (400, 403):
            raise ValueError("Untrusted Origin was not rejected.")
    async with (
        Client(url, auth=token, verify=context) as first,
        Client(url, auth=token, verify=context) as second,
    ):
        tools = {tool.name for tool in await first.list_tools()}
        if tools != {tool.name for tool in await second.list_tools()} or any(
            name.startswith("ark_asset_") or name == "ark_moderation_get" for name in tools
        ):
            raise ValueError("Shared discovery or administrative tool policy differs.")
        await first.call_tool("ark_list_models", {"input": {}})
        if "seedream_generate_image" in tools:
            smoke = pending.get("smoke_job")
            if smoke and not smoke.get("id"):
                raise ValueError(
                    "Dry-run submission acceptance is ambiguous; reconcile its job ID."
                )
            if smoke is None:
                pending["smoke_job"] = {"status": "submitting"}
                workspace.save_journal(journal)
                result = await first.call_tool(
                    "ark_job_submit",
                    {
                        "input": {
                            "tool_name": "seedream_generate_image",
                            "arguments": {
                                "input": {
                                    "prompt": "A blue ceramic cup on a white table",
                                    "dry_run": True,
                                }
                            },
                        }
                    },
                )
                accepted = result.structured_content
                if not isinstance(accepted, dict) or not isinstance(accepted.get("job_id"), str):
                    raise ValueError("Dry-run acceptance is missing; reconcile before retrying.")
                smoke = {"id": accepted["job_id"]}
                pending["smoke_job"] = smoke
                workspace.save_journal(journal)
            deadline = time.monotonic() + 120
            while True:
                result = await second.call_tool("ark_job_get", {"input": {"job_id": smoke["id"]}})
                snapshot = result.structured_content
                if not isinstance(snapshot, dict) or "status" not in snapshot:
                    raise ValueError("Dry-run status is malformed.")
                if snapshot["status"] != "working":
                    break
                if time.monotonic() >= deadline:
                    raise ValueError(
                        "Dry-run still running; resume polling rather than resubmitting."
                    )
                await asyncio.sleep(max(snapshot.get("poll_after_ms", 1000), 500) / 1000)
            outcome = snapshot.get("result")
            content = outcome.get("structured_content") if isinstance(outcome, dict) else None
            if (
                snapshot["status"] != "completed"
                or not isinstance(outcome, dict)
                or outcome.get("is_error") is not False
                or not isinstance(content, dict)
                or content.get("dry_run") is not True
            ):
                raise ValueError("Shared dry-run job failed.")
    journal.update(
        phase="ready",
        verification={
            "https": True,
            "authentication": True,
            "shared_clients": True,
            "dry_run": "seedream_generate_image" in tools,
            "paid_generation": False,
            "sequence": pending["sequence"],
            "job_id": pending.get("smoke_job", {}).get("id"),
        },
    )
    journal.pop("verification_pending", None)
    workspace.save_journal(journal)


def resume_deployment(workspace: Workspace, args: argparse.Namespace) -> None:
    from . import configuration

    pending_config = workspace.journal().get("config_update")
    if pending_config:
        if pending_config["status"] == "planned":
            raise ValueError(
                "Unapproved configuration plan; use config apply with its exact approval."
            )
        args.approve_config = pending_config["approval"]
        configuration.apply(workspace, args)
        return
    pending = workspace.journal().get("update")
    if pending:
        args.image = pending["image"]
        args.approve_update = True
        update_deployment(workspace, args)
    else:
        finish_deployment(workspace, args)


def update_deployment(workspace: Workspace, args: argparse.Namespace) -> None:
    journal = workspace.journal()
    if journal.get("config_update"):
        raise ValueError("Provider configuration update is pending; finish or reconcile it first.")
    if journal.get("phase") != "ready" or not args.approve_update:
        raise ValueError("Update requires a verified deployment and explicit --approve-update.")
    if not re.fullmatch(r"ghcr\.io/[a-z0-9][a-z0-9./_-]*@sha256:[a-f0-9]{64}", args.image):
        raise ValueError("Update requires a pinned GHCR digest.")
    if journal.get("source_fingerprint") != workspace.fingerprint():
        raise ValueError("Frozen source/configuration changed; reconcile before updating.")
    pending = journal.get("update")
    if pending and pending["image"] != args.image:
        raise ValueError("Another image update is pending; finish or reconcile it first.")
    if pending is None:
        if journal.get("verification_pending"):
            raise ValueError("Finish the pending verification before starting another update.")
        sequence = journal.get("update_sequence", 0) + 1
        journal.update(update_sequence=sequence, update={"image": args.image, "sequence": sequence})
        workspace.save_journal(journal)
    cloud = Cloud(workspace.config["region"])
    cloud.verify_instance(
        journal["outputs"]["instance_id"],
        journal["outputs"]["state_volume"]["id"],
        workspace.config,
    )
    guest = Guest(workspace, cloud, retry_failed=args.retry_failed)
    prefix = f"update-{journal['update']['sequence']}"
    image_phase(workspace, guest, args, args.image, prefix=prefix)
    guest.operation(
        prefix + "-recreate",
        shlex.join(["python3", "/opt/ark-mcp/update_image.py", "--image", args.image]),
        timeout=2400,
    )
    journal = workspace.journal()
    journal["runtime_image"] = args.image
    workspace.save_journal(journal)
    asyncio.run(verify_deployment(workspace))
    journal = workspace.journal()
    journal.pop("update", None)
    workspace.save_journal(journal)


def main(argv: list[str] | None = None) -> int:
    from . import configuration

    parser = argparse.ArgumentParser(
        description="Reviewed, resumable BytePlus Ark MCP deployment. No paid generation or infrastructure destruction."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    config_parser = subparsers.add_parser(
        "config", help="Plan and apply pinned Secret Manager provider-key sync."
    )
    config_actions = config_parser.add_subparsers(dest="config_action", required=True)
    for action in ("status", "plan", "apply", "discard"):
        command = config_actions.add_parser(action)
        command.add_argument("--workspace", required=True, type=Path)
        if action == "plan":
            command.add_argument("--secret-reference-file", required=True, type=Path)
        if action in ("apply", "discard"):
            command.add_argument("--approve-config", required=True)
        if action == "apply":
            command.add_argument("--retry-failed", action="store_true")
    for name in ("init", "plan", "deploy", "resume", "status", "verify", "update"):
        command = subparsers.add_parser(name)
        command.add_argument("--workspace", required=True, type=Path)
        if name == "init":
            command.add_argument("--config", required=True, type=Path)
        if name in ("plan", "deploy"):
            command.add_argument("--terraform", default=shutil.which("terraform") or "terraform")
        if name in ("plan", "deploy", "resume", "update"):
            command.add_argument("--registry-user")
            command.add_argument("--registry-token-file", type=Path)
        if name in ("deploy", "resume"):
            source = command.add_mutually_exclusive_group()
            source.add_argument("--provider-env-file", type=Path)
            source.add_argument("--secret-reference-file", type=Path)
        if name in ("deploy", "resume", "update"):
            command.add_argument("--retry-failed", action="store_true")
        if name == "deploy":
            command.add_argument("--approve-plan")
        if name == "update":
            command.add_argument("--image", required=True)
            command.add_argument("--approve-update", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            config = validate_config(json.loads(read_private(args.config)))
            Workspace.create(args.workspace, config)
            print("Private deployment workspace initialized. Next: plan.")
            return 0
        workspace = Workspace(args.workspace)
        with workspace.lock():
            if args.command == "plan":
                print(json.dumps(plan_deployment(workspace, args), indent=2))
            elif args.command == "deploy":
                apply_deployment(workspace, args)
                finish_deployment(workspace, args)
            elif args.command == "resume":
                resume_deployment(workspace, args)
            elif args.command == "update":
                update_deployment(workspace, args)
            elif args.command == "config":
                if args.config_action == "status":
                    print(json.dumps(configuration.status(workspace), indent=2))
                elif args.config_action == "plan":
                    print(json.dumps(configuration.plan(workspace, args), indent=2))
                elif args.config_action == "apply":
                    configuration.apply(workspace, args)
                    print("Provider configuration synced and nonbillable verification completed.")
                elif args.config_action == "discard":
                    configuration.discard(workspace, args)
                    print("Unsubmitted provider configuration plan discarded.")
            elif args.command == "verify":
                asyncio.run(verify_deployment(workspace))
            elif args.command == "status":
                journal = workspace.journal()
                print(
                    json.dumps(
                        {
                            "phase": journal.get("phase"),
                            "outputs": journal.get("outputs", {}),
                            "verification": journal.get("verification", {}),
                            "pending_update": journal.get("update"),
                            "provider_configuration": (
                                {
                                    "status": journal["config_update"]["status"],
                                    "source": journal["config_update"]["source"],
                                }
                                if journal.get("config_update")
                                else None
                            ),
                            "operations": {
                                key: {
                                    field: value
                                    for field, value in item.items()
                                    if field in ("id", "status", "error_code", "exit_code")
                                }
                                for key, item in journal["operations"].items()
                            },
                        },
                        indent=2,
                    )
                )
            if args.command in ("deploy", "resume", "update", "verify"):
                print(
                    json.dumps(
                        {
                            "mcp_url": workspace.journal()["outputs"]["mcp_url"],
                            "key_file": str(workspace.path / "team.key"),
                            "status": workspace.journal()["phase"],
                        },
                        indent=2,
                    )
                )
        return 0
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception:
        print(
            "Deployment operation failed; preserve the workspace and reconcile its journal. No secret values were logged.",
            file=sys.stderr,
        )
        return 1
