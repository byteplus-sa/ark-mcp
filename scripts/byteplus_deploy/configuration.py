"""Private, explicitly approved provider-only Secret Manager sync transactions."""

from __future__ import annotations

import asyncio
import json
import os
import shlex
from typing import TYPE_CHECKING, Any

from .cloud import Cloud
from .private import PRODUCT_KEYS, configuration_digest, replace_provider_keys, validator
from .secrets import SecretReference, fetch_provider_keys
from .workspace import Workspace, atomic_private, read_private

if TYPE_CHECKING:
    import argparse


def _provenance(workspace: Workspace) -> None:
    if workspace.journal().get("source_fingerprint") != workspace.fingerprint():
        raise ValueError("Frozen deployment source changed; a reviewed migration is required.")


def _approval(workspace: Workspace, pending: dict[str, Any]) -> str:
    binding = {
        "source_fingerprint": workspace.fingerprint(),
        "instance": workspace.journal()["outputs"]["instance_id"],
        "source": pending["source"],
        "previous_digest": pending["previous_digest"],
        "next_digest": pending["next_digest"],
        "sequence": pending["sequence"],
    }
    return configuration_digest(json.dumps(binding, sort_keys=True).encode())


def status(workspace: Workspace) -> dict[str, Any]:
    """Report local key presence and source metadata; never retrieve or expose values."""
    values = validator().load_environment(workspace.path / "host.env", expected_uid=os.getuid())
    journal = workspace.journal()
    pending = journal.get("config_update")
    return {
        "configured_keys": {key: bool(values.get(key)) for key in sorted(PRODUCT_KEYS)},
        "source": journal.get("provider_secret_source"),
        "pending": (
            {
                "status": pending["status"],
                "source": pending["source"],
                "changed_keys": pending["changed_keys"],
                "approve_config": pending["approval"],
            }
            if pending
            else None
        ),
        "provider_credentials_verified": False,
    }


def plan(workspace: Workspace, args: argparse.Namespace) -> dict[str, Any]:
    """Freeze one exact version and candidate configuration without changing the host."""
    from .cli import prepare_configuration

    _provenance(workspace)
    journal = workspace.journal()
    if journal.get("phase") != "ready":
        raise ValueError("Configuration sync requires a verified CLI-managed deployment.")
    if any(journal.get(key) for key in ("update", "verification_pending", "config_update")):
        raise ValueError("Another update or verification is pending; finish or reconcile it first.")
    workspace.cleanup_temporaries()
    prepare_configuration(workspace, argparse_namespace())
    reference = SecretReference.load(args.secret_reference_file)
    keys = fetch_provider_keys(reference)
    original = read_private(workspace.path / "host.env")
    previous_values = validator().load_environment(
        workspace.path / "host.env", expected_uid=os.getuid()
    )
    candidate = replace_provider_keys(original, keys)
    sequence = journal.get("config_sequence", 0) + 1
    pending = {
        "source": reference.metadata(),
        "sequence": sequence,
        "previous_digest": configuration_digest(original),
        "next_digest": configuration_digest(candidate),
        "changed_keys": sorted(
            key for key in PRODUCT_KEYS if previous_values.get(key, "") != keys.get(key, "")
        ),
        "status": "planned",
        "host_operation": f"config-{sequence}-1",
    }
    atomic_private(workspace.path / "config.previous.env", original)
    atomic_private(workspace.path / "config.candidate.env", candidate)
    pending["approval"] = _approval(workspace, pending)
    journal.update(config_sequence=sequence, config_update=pending)
    workspace.save_journal(journal)
    return {
        "source": pending["source"],
        "changed_keys": pending["changed_keys"],
        "approve_config": pending["approval"],
        "maintenance": "Pause producers and drain jobs before apply. Only MCP is recreated; paid work is not guaranteed to survive interruption.",
    }


def argparse_namespace() -> argparse.Namespace:
    import argparse

    return argparse.Namespace(provider_env_file=None, secret_reference_file=None)


def _saved_plan(workspace: Workspace) -> tuple[dict[str, Any], bytes]:
    _provenance(workspace)
    pending = workspace.journal().get("config_update")
    if not isinstance(pending, dict):
        raise ValueError("No provider configuration plan exists.")
    candidate = read_private(workspace.path / "config.candidate.env")
    previous = read_private(workspace.path / "config.previous.env")
    if (
        configuration_digest(candidate) != pending["next_digest"]
        or configuration_digest(previous) != pending["previous_digest"]
        or _approval(workspace, pending) != pending["approval"]
        or configuration_digest(read_private(workspace.path / "host.env"))
        not in {pending["previous_digest"], pending["next_digest"]}
    ):
        raise ValueError("Saved configuration plan or retained configuration changed.")
    return pending, candidate


def apply(workspace: Workspace, args: argparse.Namespace) -> None:
    """Apply only an approved staged version, preserving accepted invocation evidence."""
    from . import cli

    cleanup = workspace.journal().get("config_update")
    if cleanup and cleanup["status"] in {"cleanup", "discarding"}:
        _cleanup(workspace, args)
        return
    pending, candidate = _saved_plan(workspace)
    journal = workspace.journal()
    if (
        getattr(args, "approve_config", None) != pending["approval"]
        or journal.get("phase") != "ready"
        or journal.get("update")
        or pending["status"] not in {"planned", "applying", "verifying", "committing"}
    ):
        raise ValueError("Configuration apply requires exact approval and no competing update.")
    workspace.cleanup_temporaries()
    if pending["status"] != "committing":
        cloud = Cloud(workspace.config["region"])
        try:
            cloud.verify_instance(
                journal["outputs"]["instance_id"],
                journal["outputs"]["state_volume"]["id"],
                workspace.config,
            )
            guest = cli.Guest(workspace, cloud, retry_failed=args.retry_failed)
            phase = cli.transfer_attempt(
                workspace, f"config-{pending['sequence']}", retry_failed=args.retry_failed
            )
            host_operation = pending["host_operation"]
            if pending.get("transfer") and phase != pending["transfer"]:
                command = shlex.join(
                    [
                        "python3",
                        "/opt/ark-mcp/update_config.py",
                        "--inspect",
                        "--previous-digest",
                        pending["previous_digest"],
                        "--digest",
                        pending["next_digest"],
                        "--operation",
                        host_operation,
                    ]
                )
                result = json.loads(guest.operation(phase + "-reconcile", command))
                recovered = result.get("status") if isinstance(result, dict) else None
                if recovered not in {
                    "absent",
                    "pending",
                    "applied",
                    "rolled_back",
                    "recovery_failed",
                }:
                    raise ValueError("Host configuration transaction requires reconciliation.")
                if recovered in {"rolled_back", "recovery_failed"}:
                    generation = int(host_operation.rsplit("-", 1)[1]) + 1
                    host_operation = f"config-{pending['sequence']}-{generation}"
                guest.operation(
                    phase + "-cleanup", cli.transfer_cleanup_command(pending["transfer"])
                )
            journal = workspace.journal()
            journal["config_update"].update(
                status="applying", transfer=phase, host_operation=host_operation
            )
            workspace.save_journal(journal)
            guest.transfer(
                phase,
                candidate,
                [
                    "--kind",
                    "provider-update",
                    "--previous-digest",
                    pending["previous_digest"],
                    "--digest",
                    pending["next_digest"],
                    "--operation",
                    host_operation,
                ],
                timeout=2400,
            )
            journal = workspace.journal()
            journal["config_update"]["status"] = "verifying"
            workspace.save_journal(journal)
            asyncio.run(cli.verify_deployment(workspace))
            journal = workspace.journal()
            journal["config_update"]["status"] = "committing"
            workspace.save_journal(journal)
        finally:
            cloud.close()
    atomic_private(workspace.path / "host.env", candidate)
    journal = workspace.journal()
    journal.update(
        configuration_sha256=pending["next_digest"], provider_secret_source=pending["source"]
    )
    journal["config_update"]["status"] = "cleanup"
    workspace.save_journal(journal)
    _cleanup(workspace, args)


def _cleanup(workspace: Workspace, args: argparse.Namespace) -> None:
    _provenance(workspace)
    journal = workspace.journal()
    pending = journal["config_update"]
    expected = (
        pending["previous_digest"] if pending["status"] == "discarding" else pending["next_digest"]
    )
    if (
        pending["status"] not in {"cleanup", "discarding"}
        or args.approve_config != pending["approval"]
        or _approval(workspace, pending) != pending["approval"]
        or configuration_digest(read_private(workspace.path / "host.env")) != expected
        or journal["configuration_sha256"] != expected
    ):
        raise ValueError(
            "Provider cleanup requires the exact reconciled configuration and approval."
        )
    workspace.cleanup_temporaries()
    for filename in ("config.previous.env", "config.candidate.env"):
        (workspace.path / filename).unlink(missing_ok=True)
    directory = os.open(workspace.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    journal.pop("config_update")
    workspace.save_journal(journal)


def discard(workspace: Workspace, args: argparse.Namespace) -> None:
    """Discard only an unsubmitted plan with explicit approval, never an accepted update."""
    pending, _ = _saved_plan(workspace)
    if pending["status"] != "planned" or args.approve_config != pending["approval"]:
        raise ValueError("Only an explicitly approved, unsubmitted plan may be discarded.")
    journal = workspace.journal()
    journal["config_update"]["status"] = "discarding"
    workspace.save_journal(journal)
    _cleanup(workspace, args)
