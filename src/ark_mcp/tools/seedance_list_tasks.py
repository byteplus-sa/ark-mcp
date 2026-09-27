"""``seedance_list_tasks`` tool — list recent Seedance video generation tasks.

Queries only the previous seven days (provider limitation). The server
policy caps ``page_size`` at 100 even though the provider accepts 500,
avoiding oversized model context.
"""

from __future__ import annotations

from typing import Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field

from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.models import SeedanceTaskStatus, SeedanceTaskSummary
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import get_principal, get_runtime
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._task_execution import context_log


class SeedanceListTasksInput(BaseModel):
    """Input model for ``seedance_list_tasks``."""

    page: int | None = Field(
        None,
        ge=1,
        le=500,
        description="Page number for paginated results (1-based). Defaults to 1.",
    )
    page_size: int | None = Field(
        None,
        ge=1,
        le=100,
        description="Number of tasks per page. Server caps at 100. Defaults to 20.",
    )
    status: SeedanceTaskStatus | None = Field(
        None,
        description="Filter tasks by status (queued, running, succeeded, failed, cancelled, expired).",
    )
    task_ids: list[str] | None = Field(
        None,
        description="Filter to specific task IDs. Non-owner users can only query their own tasks.",
    )
    model: str | None = Field(
        None,
        description=(
            "Filter tasks by model ID. Available: 'dreamina-seedance-2-0-260128' (Standard), "
            "'dreamina-seedance-2-5-260628' (2.5), and "
            "'dreamina-seedance-2-5-premium-260915' (2.5 Premium, when enabled). "
            "Fast and Mini model IDs may also be configured via SEEDANCE_MODEL_BINDINGS."
        ),
    )
    service_tier: Literal["default", "flex"] | None = Field(
        None, description="Filter tasks by service tier: default or flex."
    )


class SeedanceTaskPage(BaseModel):
    """Output model for ``seedance_list_tasks``."""

    tasks: list[SeedanceTaskSummary] = Field(
        ..., description="Task summaries for the current page."
    )
    total: int = Field(..., description="Total number of matching tasks.")
    page: int = Field(..., description="Current page number (1-based).")
    page_size: int = Field(..., description="Number of tasks per page.")
    has_more: bool = Field(False, description="Whether more pages are available.")


async def seedance_list_tasks(
    input: SeedanceListTasksInput, ctx: Context
) -> SeedanceTaskPage | ToolResult:
    """List recent Seedance video generation tasks.

    Queries the previous seven days of tasks (provider limitation).
    Supports filtering by status, task IDs, model, and service tier.
    The server caps ``page_size`` at 100 to avoid oversized context.
    """
    await context_log(ctx, "info", "Listing Seedance tasks")
    await ctx.report_progress(progress=20, total=100)
    owner = get_principal(ctx)
    owned_task_ids = await get_runtime(ctx).ownership_store.list_task_ids("modelark", owner)

    requested_task_ids = input.task_ids
    if not owner.is_local:
        requested_task_ids = (
            sorted(owned_task_ids)
            if input.task_ids is None
            else sorted(set(input.task_ids) & owned_task_ids)
        )
        if not requested_task_ids:
            return SeedanceTaskPage(
                tasks=[],
                total=0,
                page=input.page or 1,
                page_size=input.page_size or 20,
                has_more=False,
            )

    service = SeedanceService()
    try:
        response, request_id = await call_with_retry(
            lambda: service.list_tasks(
                page=input.page or 1,
                page_size=input.page_size or 20,
                status=input.status,
                task_ids=requested_task_ids,
                model=input.model,
                service_tier=input.service_tier,
            )
        )
    except ProviderError as exc:
        await context_log(ctx, "error", f"Failed to list tasks: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    await ctx.report_progress(progress=100, total=100)
    log_info(
        "seedance_tasks_listed",
        count=len(response.data),
        total=response.total,
        request_id=request_id,
    )

    return SeedanceTaskPage(
        tasks=[SeedanceService.to_task_summary(t) for t in response.data],
        total=response.total,
        page=input.page or 1,
        page_size=input.page_size or 20,
        has_more=response.has_more or False,
    )


# Tool annotation constants — camelCase per MCP specification.
TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
