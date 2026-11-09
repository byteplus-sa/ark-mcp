"""Frozen size budgets for the tool schemas every MCP client loads.

Measured with every service enabled, including the AK/SK asset library, so
no tool escapes the count. Lower the budgets when the surface shrinks; raise
them only with a deliberate, reviewed reason. Cached catalog pricing and
Files API delivery metadata add 1,000 characters to the output allowance.
Seedream 5.5 typed per-call image metadata, usage and model-specific discovery
limits add 6,000 characters; the tool count and input budget are unchanged.
"""

from __future__ import annotations

import json

from ark_mcp.config.env import get_settings
from ark_mcp.server import create_server

MAX_TOOLS = 38
MAX_MODEL_FACING_CHARS = 88_000
MAX_OUTPUT_SCHEMA_CHARS = 134_000


async def test_tool_schemas_stay_within_budget(all_services_enabled: None) -> None:
    tools = await create_server(get_settings()).list_tools()
    model_facing = sum(
        len(tool.description or "") + len(json.dumps(tool.parameters)) for tool in tools
    )
    output = sum(len(json.dumps(tool.output_schema or {})) for tool in tools)

    assert len(tools) <= MAX_TOOLS
    assert model_facing <= MAX_MODEL_FACING_CHARS, model_facing
    assert output <= MAX_OUTPUT_SCHEMA_CHARS, output
