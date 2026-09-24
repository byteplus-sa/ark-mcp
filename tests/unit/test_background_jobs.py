from __future__ import annotations

from inspect import signature

import pytest
from fastmcp_tasks.creation import create_task
from fastmcp_tasks.handlers import tasks_cancel, tasks_get

from ark_mcp.background_jobs import (
    BACKGROUND_TOOL_SPECS,
    optional_background_tool_names,
    required_background_tool_names,
)
from ark_mcp.domain.background_jobs import BackgroundJobSnapshot
from ark_mcp.tools import background_jobs as background_jobs_module
from ark_mcp.tools.background_jobs import _unavailable_message


def test_background_tool_registry_modes_partition_all_targets() -> None:
    required = required_background_tool_names()
    optional = optional_background_tool_names()

    assert required
    assert optional
    assert len(required) == 21
    assert len(optional) == 12
    assert required.isdisjoint(optional)
    assert required | optional == BACKGROUND_TOOL_SPECS.keys()


def test_fastmcp_task_adapter_contract_matches_pinned_minor() -> None:
    assert tuple(signature(create_task).parameters) == ("tool", "arguments", "context")
    assert tuple(signature(tasks_get).parameters) == ("server", "task_id")
    assert tuple(signature(tasks_cancel).parameters) == ("server", "task_id")


def test_background_job_snapshot_describes_configured_ttl() -> None:
    ttl_schema = BackgroundJobSnapshot.model_json_schema()["properties"]["ttl_ms"]

    assert ttl_schema["description"] == (
        "Configured result retention duration in milliseconds, or null when unlimited."
    )


def test_local_unavailable_message_names_process_locality() -> None:
    message = _unavailable_message(True)

    assert "server instance" in message
    assert "process-local" in message


def test_remote_unavailable_message_stays_principal_scoped() -> None:
    assert _unavailable_message(False) == ("Background job is not available to this principal.")


def test_local_owner_detection_falls_back_when_identity_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_permission_error(_ctx: object) -> None:
        raise PermissionError("missing identity")

    monkeypatch.setattr(background_jobs_module, "get_principal", raise_permission_error)

    assert background_jobs_module._is_local_owner(object()) is False
