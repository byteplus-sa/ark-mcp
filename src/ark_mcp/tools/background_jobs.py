"""Ordinary MCP tools: the default route for long-running Ark work."""

from __future__ import annotations

from typing import cast

from fastmcp import Context
from fastmcp.exceptions import ToolError
from mcp.shared.exceptions import MCPError
from pydantic import BaseModel, Field, JsonValue

from ark_mcp.background_jobs import BACKGROUND_TOOL_SPECS, background_job_bridge
from ark_mcp.domain.background_jobs import (
    BackgroundJobAccepted,
    BackgroundJobCancelled,
    BackgroundJobCapabilities,
    BackgroundJobSnapshot,
    BackgroundJobTarget,
)
from ark_mcp.observability.logger import info as log_info
from ark_mcp.observability.metrics import record_background_job_submission
from ark_mcp.runtime import get_principal
from ark_mcp.security.tasks import claim_mcp_task_owner, require_mcp_task_owner
from ark_mcp.tools._task_execution import context_log


class BackgroundJobSubmitInput(BaseModel):
    """Input model for ``ark_job_submit``."""

    tool_name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description="Tool to run in the background; must be listed by ark_job_capabilities.",
    )
    arguments: dict[str, JsonValue] = Field(
        ...,
        description="Full arguments object for the target tool, as in its input schema (usually {'input': {...}}).",
    )


class BackgroundJobIdInput(BaseModel):
    """Input model for Ark background job retrieval and cancellation."""

    job_id: str = Field(
        ...,
        min_length=20,
        max_length=128,
        pattern=r"^[A-Za-z0-9_-]+$",
        description="job_id returned by ark_job_submit (not a provider task ID).",
    )


def _is_local_owner(ctx: Context) -> bool:
    """Return whether the caller is the trusted local principal."""
    try:
        return get_principal(ctx).is_local
    except PermissionError:
        return False


def _unavailable_message(is_local: bool) -> str:
    """Explain an unavailable job without leaking remote principal identities."""
    if is_local:
        return (
            "Background job is not available in this server instance. The job ID is "
            "unknown, expired, or was created by a different ark-mcp process. Local "
            "background jobs are process-local; submit and poll through the same "
            "server, or run one shared HTTP server for all local clients."
        )
    return "Background job is not available to this principal."


async def ark_job_capabilities(ctx: Context) -> BackgroundJobCapabilities:
    """List the tools ark_job_submit can run in the background, with each one's input schema.

    Only tools enabled by the server configuration and authorized for the caller appear.
    """
    owner = get_principal(ctx)
    targets: list[BackgroundJobTarget] = []
    for tool_name, spec in sorted(BACKGROUND_TOOL_SPECS.items()):
        if not owner.is_local and spec.required_scope not in owner.scopes:
            continue
        target = await ctx.fastmcp.get_tool(tool_name)
        if target is None:
            continue
        mcp_tool = target.to_mcp_tool()
        targets.append(
            BackgroundJobTarget(
                tool_name=tool_name,
                task_mode=spec.mode,
                required_scope=spec.required_scope,
                description=mcp_tool.description or "",
                input_schema=cast("dict[str, JsonValue]", mcp_tool.input_schema),
            )
        )
    return BackgroundJobCapabilities(targets=targets)


async def ark_job_submit(
    input: BackgroundJobSubmitInput,
    ctx: Context,
) -> BackgroundJobAccepted:
    """Run a long-running tool as a background job and return a job_id immediately.

    Required for tools marked as requiring background execution (e.g. media_upload, vod_*); no MCP
    task support needed. Poll ark_job_get for the tool's result. Do not auto-retry a timeout or
    disconnect: the job may already be queued.
    """
    spec = BACKGROUND_TOOL_SPECS.get(input.tool_name)
    if spec is None:
        raise ToolError("The requested background job target is not supported.")

    try:
        owner = get_principal(ctx)
        if not owner.is_local and spec.required_scope not in owner.scopes:
            raise ToolError("The caller is not authorized for the requested background job target.")

        target = await ctx.fastmcp.get_tool(input.tool_name)
        if target is None:
            raise ToolError("The requested background job target is not available.")
        if target.task_config.mode != spec.mode:
            raise ToolError("The requested background job target has an invalid execution policy.")

        await context_log(ctx, "info", f"Submitting background job for {input.tool_name}")
        accepted = await background_job_bridge.submit(
            ctx,
            target,
            input.arguments,
        )
        try:
            await claim_mcp_task_owner(ctx, accepted.job_id, owner)
        except PermissionError as exc:
            await background_job_bridge.cancel(ctx.fastmcp, accepted.job_id)
            raise ToolError("The background job could not be assigned to the caller.") from exc
    except Exception:
        record_background_job_submission(
            target=input.tool_name,
            status="rejected",
            path="compatibility",
        )
        raise

    record_background_job_submission(
        target=input.tool_name,
        status="accepted",
        path="compatibility",
    )

    log_info(
        "background_job_accepted",
        job_id=accepted.job_id,
        target_tool=input.tool_name,
        path="compatibility",
    )
    return accepted


async def ark_job_get(
    input: BackgroundJobIdInput,
    ctx: Context,
) -> BackgroundJobSnapshot:
    """Return a background job's status and, once finished, the target tool's full result.

    Poll no faster than poll_after_ms. Provider task IDs appear inside the result, distinct from
    job_id.
    """
    try:
        await require_mcp_task_owner(ctx, input.job_id)
    except PermissionError as exc:
        raise ToolError(_unavailable_message(_is_local_owner(ctx))) from exc
    try:
        snapshot = await background_job_bridge.get(ctx.fastmcp, input.job_id)
    except MCPError as exc:
        if exc.code == -32602:
            raise ToolError(_unavailable_message(_is_local_owner(ctx))) from exc
        raise
    log_info(
        "background_job_polled",
        job_id=input.job_id,
        status=snapshot.status,
        path="compatibility",
    )
    return snapshot


async def ark_job_cancel(
    input: BackgroundJobIdInput,
    ctx: Context,
) -> BackgroundJobCancelled:
    """Cancel one of your background jobs.

    Stops the local worker only; a provider operation already accepted by BytePlus is not
    cancelled.
    """
    try:
        await require_mcp_task_owner(ctx, input.job_id)
    except PermissionError as exc:
        raise ToolError(_unavailable_message(_is_local_owner(ctx))) from exc
    try:
        await background_job_bridge.cancel(ctx.fastmcp, input.job_id)
    except MCPError as exc:
        if exc.code == -32602:
            raise ToolError(_unavailable_message(_is_local_owner(ctx))) from exc
        raise
    log_info(
        "background_job_cancelled",
        job_id=input.job_id,
        path="compatibility",
    )
    return BackgroundJobCancelled(job_id=input.job_id)


CAPABILITIES_TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

SUBMIT_TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}

GET_TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}

CANCEL_TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}
