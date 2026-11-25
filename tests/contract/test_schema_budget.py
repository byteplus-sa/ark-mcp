"""Frozen size budgets for the tool schemas every MCP client loads.

With all services enabled, 32 tools occupy 69,985 model-facing characters and
119,819 output-schema characters. Lower the budgets when the surface shrinks.
Raise them only with a deliberate, reviewed reason.
"""

from __future__ import annotations

import json

from ark_mcp.config.env import get_settings
from ark_mcp.server import create_server

MAX_TOOLS = 32
MAX_MODEL_FACING_CHARS = 71_000
MAX_OUTPUT_SCHEMA_CHARS = 121_000


async def test_tool_schemas_stay_within_budget(all_services_enabled: None) -> None:
    tools = await create_server(get_settings()).list_tools()
    model_facing = sum(
        len(tool.description or "") + len(json.dumps(tool.parameters)) for tool in tools
    )
    output = sum(len(json.dumps(tool.output_schema or {})) for tool in tools)

    assert len(tools) <= MAX_TOOLS
    assert model_facing <= MAX_MODEL_FACING_CHARS, model_facing
    assert output <= MAX_OUTPUT_SCHEMA_CHARS, output
