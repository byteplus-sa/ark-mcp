"""Protect moderation input values from framework validation logs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastmcp.server.middleware import Middleware
from fastmcp.tools import ToolResult
from pydantic import ValidationError

from ark_mcp.runtime import get_principal
from ark_mcp.tools.ark_moderation_get import ModerationGetInput

if TYPE_CHECKING:
    from fastmcp.server.middleware import CallNext, MiddlewareContext
    from mcp.types import CallToolRequestParams


class ModerationInputPrivacyMiddleware(Middleware):
    """Reject malformed moderation calls before FastMCP logs raw validation inputs."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        if context.message.name != "ark_moderation_get" or context.fastmcp_context is None:
            return await call_next(context)
        get_principal(context.fastmcp_context)
        if await context.fastmcp_context.fastmcp.get_tool(context.message.name) is None:
            return await call_next(context)
        arguments = context.message.arguments or {}
        valid = set(arguments) == {"input"}
        if valid:
            try:
                ModerationGetInput.model_validate(arguments["input"])
            except ValidationError:
                valid = False
        if not valid:
            return ToolResult(
                content=(
                    "INVALID_INPUT: Supply only input.id (a bare provider ID, 1-256 characters, "
                    "no internal whitespace, controls, or URI scheme) and input.type "
                    "(task_id, asset_id, or request_id)."
                ),
                is_error=True,
            )
        return await call_next(context)
