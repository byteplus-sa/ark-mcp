"""Application ownership and execution safeguards for MCP background tasks."""

from __future__ import annotations

from dataclasses import replace
from functools import wraps
from inspect import signature
from typing import TYPE_CHECKING, Any

from fastmcp import Context
from fastmcp.exceptions import ToolError
from fastmcp.server.dependencies import get_context
from fastmcp_tasks import TasksExtension
from fastmcp_tasks.context import get_task_context
from fastmcp_tasks.encryption import snapshot_codec
from fastmcp_tasks.models import CreateTaskResult
from mcp.shared.exceptions import MCPError

from ark_mcp.runtime import get_principal, get_runtime

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from fastmcp.server.extensions import (
        ExtensionRequestHandler,
        MethodBinding,
        ToolCallContinuation,
        ToolCallOutcome,
    )
    from mcp_types import CallToolRequestParams

    from ark_mcp.config.env import Settings
    from ark_mcp.security.auth_context import AuthContext


class TenantTasksExtension(TasksExtension):
    """Enforce application tenant ownership on every task request."""

    def __init__(self, settings: Settings) -> None:
        super().__init__()
        if (
            settings.mcp_auth_mode.value != "local"
            and self.docket_settings.url.startswith(("redis://", "rediss://"))
            and not snapshot_codec().protected
        ):
            raise ValueError("Authenticated Redis tasks require FASTMCP_TASKS_ENCRYPTION_KEY.")

    def methods(self) -> Sequence[MethodBinding]:
        return [
            replace(binding, handler=self._owned_handler(binding.handler))
            for binding in super().methods()
        ]

    def _owned_handler(self, handler: ExtensionRequestHandler) -> ExtensionRequestHandler:
        async def guarded(request: Any, params: Any) -> Any:
            async with Context(self.server) as context:
                try:
                    await require_mcp_task_owner(context, params.task_id)
                except PermissionError:
                    raise MCPError(
                        code=-32602, message="Task is not available to this principal."
                    ) from None
                return await handler(request, params)

        return guarded

    async def intercept_tool_call(
        self,
        params: CallToolRequestParams,
        context: Context,
        call_next: ToolCallContinuation,
    ) -> ToolCallOutcome:
        owner = get_principal(context)
        result = await super().intercept_tool_call(params, context, call_next)
        if isinstance(result, CreateTaskResult):
            await claim_mcp_task_owner(context, result.task_id, owner)
        return result


async def claim_mcp_task_owner(
    context: Context,
    task_id: str,
    owner: AuthContext | None = None,
) -> None:
    """Atomically claim and verify an MCP task for the application principal."""
    resolved_owner = owner or get_principal(context)
    store = get_runtime(context).ownership_store
    await store.claim("mcp", task_id, resolved_owner)
    await store.require_owner("mcp", task_id, resolved_owner)


async def require_mcp_task_owner(context: Context, task_id: str) -> None:
    """Require the application principal that owns an MCP task."""
    await get_runtime(context).ownership_store.require_owner("mcp", task_id, get_principal(context))


def guard_task_execution[**P, R](handler: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
    """Claim each background execution before starting potentially billable work."""

    @wraps(handler)
    async def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        task = get_task_context()
        if task is not None:
            context = get_context()
            owner = get_principal(context)
            store = get_runtime(context).ownership_store
            if not await store.claim("mcp-execution", task.task_id, owner):
                raise ToolError(
                    "This background operation already started and will not be replayed. "
                    "Its provider outcome may be unknown; reconcile existing provider tasks "
                    "before submitting another operation."
                )
        return await handler(*args, **kwargs)

    guarded.__dict__["__signature__"] = signature(handler, eval_str=True)
    return guarded
