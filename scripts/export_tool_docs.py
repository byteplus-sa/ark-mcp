"""Export the complete static tool catalog as deterministic JSON on stdout.

Run with the installed project Python in a fresh process:
    .venv/bin/python -I -B scripts/export_tool_docs.py

The build orchestrator captures stdout; this script writes no output files.
Each tool includes its FastMCP description and exact inputSchema wrapper,
the catalog's explicit outputSchema, scope, background mode (null means
foreground-only), and a human-readable registration_gate. Gates describe
configuration requirements, not the current environment or provider entitlement.
"""

from __future__ import annotations

import json
import os
import sys
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any

_ENV_ALLOWLIST = ("PATH", "SYSTEMROOT", "WINDIR", "TMPDIR", "TEMP", "TMP")
_REGISTRATION_GATES = {
    "_always": "Always registered; no provider credentials required.",
    "_modelark": "Requires BYTEPLUS_MODELARK_API_KEY.",
    "_seed3d": "Requires BYTEPLUS_MODELARK_3D_ENABLED=true and BYTEPLUS_MODELARK_API_KEY.",
    "_seed_audio": "Requires BYTEPLUS_SEED_SPEECH_API_KEY.",
    "_stt": "Requires BYTEPLUS_SEED_SPEECH_API_KEY.",
    "_object_storage": (
        "Requires the selected object-storage backend: OBJECT_STORAGE_BACKEND=tos (default) "
        "with TOS_ACCESS_KEY, TOS_SECRET_KEY and TOS_BUCKET; or OBJECT_STORAGE_BACKEND=s3 "
        "with S3_ACCESS_KEY, S3_SECRET_KEY and S3_BUCKET."
    ),
    "_vod": "Requires BYTEPLUS_VOD_MEDIAKIT_API_KEY.",
    "_omnihuman": "Requires BYTEPLUS_OMNIHUMAN_ENABLED=true and BYTEPLUS_CV_API_KEY.",
    "_assets": "Requires BYTEPLUS_MODELARK_ACCESS_KEY and BYTEPLUS_MODELARK_SECRET_KEY.",
    "_asset_delete": (
        "Requires BYTEPLUS_MODELARK_ASSETS_ALLOW_DELETE=true, BYTEPLUS_MODELARK_ACCESS_KEY "
        "and BYTEPLUS_MODELARK_SECRET_KEY."
    ),
}


def _isolate_package() -> None:
    """Bypass the root package's deployment entrypoint without changing app code."""
    if "ark_mcp" in sys.modules:
        raise RuntimeError("Run the tool documentation exporter in a fresh Python process.")
    package_path = str(Path(__file__).resolve().parents[1] / "src" / "ark_mcp")
    package = ModuleType("ark_mcp")
    package.__package__ = "ark_mcp"
    package.__path__ = [package_path]
    package.__spec__ = ModuleSpec("ark_mcp", loader=None, is_package=True)
    package.__spec__.submodule_search_locations = package.__path__
    sys.modules["ark_mcp"] = package


def export_tool_docs() -> dict[str, Any]:
    """Resolve all catalog schemas without evaluating gates or calling handlers."""
    from datetime import timedelta

    from fastmcp.tools import FunctionTool
    from fastmcp.utilities.tasks import TaskConfig

    from ark_mcp.tools._catalog import CATALOG, resolve_tool_definition

    tools = []
    for entry in CATALOG:
        gate_name = entry.gate.__name__
        if gate_name not in _REGISTRATION_GATES:
            raise ValueError(f"Undocumented registration gate: {gate_name}")
        definition = resolve_tool_definition(entry)
        task = (
            TaskConfig(mode=entry.background, poll_interval=timedelta(seconds=2))
            if entry.background
            else None
        )
        tool = FunctionTool.from_function(
            definition.handler,
            name=entry.name,
            output_schema=definition.output_model.model_json_schema(),
            task=task,
        )
        tools.append(
            {
                "name": entry.name,
                "module": entry.module,
                "description": tool.description,
                "inputSchema": tool.parameters,
                "outputSchema": tool.output_schema,
                "scope": entry.scope,
                "background": entry.background,
                "registration_gate": _REGISTRATION_GATES[gate_name],
            }
        )
    return {"tools": tools}


def main() -> int:
    """Prepare the isolated build process and write only the JSON document."""
    _isolate_package()
    environment = {key: os.environ[key] for key in _ENV_ALLOWLIST if key in os.environ}
    environment.update(
        FASTMCP_ENV_FILE=os.devnull,
        FASTMCP_LOG_ENABLED="false",
        FASTMCP_TELEMETRY_MODE="off",
    )
    os.environ.clear()
    os.environ.update(environment)
    sys.dont_write_bytecode = True
    document = json.dumps(export_tool_docs(), indent=2, sort_keys=True, allow_nan=False)
    sys.stdout.write(document + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
