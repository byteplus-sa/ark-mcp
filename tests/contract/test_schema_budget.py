"""Frozen size budgets for the tool schemas every MCP client loads.

With all services enabled, the 32 tools occupy 73,590 model-facing characters
and 129,596 output-schema characters. Seed Audio 1.5 adds mode-specific inputs,
discovery limits, and schemas for audio tracks, video, and subtitle outputs.
Audio artifact recovery exposes a bounded download limit.
Lower the budgets when the surface shrinks and review any increase.
"""

from __future__ import annotations

import json

from ark_mcp.config.env import get_settings
from ark_mcp.server import create_server

MAX_TOOLS = 32
MAX_MODEL_FACING_CHARS = 74_500
MAX_OUTPUT_SCHEMA_CHARS = 131_000


async def test_tool_schemas_stay_within_budget(all_services_enabled: None) -> None:
    tools = await create_server(get_settings()).list_tools()
    model_facing = sum(
        len(tool.description or "") + len(json.dumps(tool.parameters)) for tool in tools
    )
    output = sum(len(json.dumps(tool.output_schema or {})) for tool in tools)

    assert len(tools) <= MAX_TOOLS
    assert model_facing <= MAX_MODEL_FACING_CHARS, model_facing
    assert output <= MAX_OUTPUT_SCHEMA_CHARS, output
