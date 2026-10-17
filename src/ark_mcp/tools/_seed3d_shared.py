"""Shared Seed3D tool components.

Input type ``Seed3DImageInput``, the create output model, and the shared
create executor used by the Hyper3D and Hitem3d tool handlers. Task polling,
listing and cancellation are the ``gen_task_*`` tools.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field

from ark_mcp.config.model_capabilities import Seed3DCapabilities
from ark_mcp.domain.artifacts import MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.media import MediaSource
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.seed3d import Seed3DService
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import billed_provider_slot, get_principal, get_runtime
from ark_mcp.tools._cost import log_cost_estimate
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._task_execution import context_log


class Seed3DImageInput(MediaSource):
    """Image input for 3D generation (Hyper3D and Hitem3d)."""

    MEDIA_CATEGORY: ClassVar[MediaType] = MediaType.IMAGE


class Seed3DCreateTaskOutput(BaseModel):
    """Output model for 3D task creation tools."""

    task_id: str = Field(
        ...,
        description="Provider task ID; pass it to gen_task_get to poll and gen_task_cancel_or_delete to manage.",
    )
    status: Literal["queued"] = Field(
        "queued",
        description="Initial task status. Poll with gen_task_get until the task finishes.",
    )
    recommended_poll_after_ms: int = Field(
        ..., description="Suggested delay in milliseconds before first poll."
    )


async def execute_seed3d_create(
    *,
    ctx: Context,
    caps: Seed3DCapabilities,
    prompt: str | None,
    images: list[Seed3DImageInput] | None,
    seed: int | None,
    callback_url: str | None,
    command_params: dict[str, Any],
) -> tuple[str, str | None] | ToolResult:
    """Execute a Seed3D task creation using the resolved capabilities."""
    await ctx.report_progress(progress=30, total=100)

    images_data = None
    if images:
        images_data = [img.model_dump() for img in images]

    content = Seed3DService.build_content(
        prompt=prompt,
        command_params=command_params,
        images=images_data,
    )

    request = Seed3DService.build_request(
        model=caps.model_id,
        content=content,
        seed=seed,
        callback_url=callback_url,
    )

    await ctx.report_progress(progress=50, total=100)

    estimated_cost = log_cost_estimate(
        product="3d",
        variations=1,
        model_id=caps.model_id,
        seed3d_family=caps.family.value,
    )

    service = Seed3DService()
    try:
        async with billed_provider_slot(
            ctx,
            provider="modelark",
            product="3d",
            estimated_cost_usd=estimated_cost,
        ):
            task_id, request_id = await call_with_retry(lambda: service.create_task(request))
    except ProviderError as exc:
        await context_log(ctx, "error", f"3D task creation failed: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    await get_runtime(ctx).ownership_store.record("modelark", task_id, get_principal(ctx))

    await ctx.report_progress(progress=100, total=100)
    log_info(
        "seed3d_task_created",
        task_id=task_id,
        model=caps.model_id,
        request_id=request_id,
    )

    return task_id, request_id
