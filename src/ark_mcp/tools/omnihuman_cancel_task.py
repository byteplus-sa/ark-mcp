"""``omnihuman_cancel_task`` tool — cancel a queued OmniHuman 2.0 task."""

from __future__ import annotations

from typing import Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field

from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.vision_cv.omnihuman import OmniHumanService
from ark_mcp.runtime import get_principal, get_runtime
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._task_execution import context_log


class OmniHumanCancelTaskInput(BaseModel):
    """Input for ``omnihuman_cancel_task``."""

    task_id: str = Field(min_length=1, description="Task ID returned by omnihuman_create_task.")


class OmniHumanCancelTaskOutput(BaseModel):
    """Outcome of an OmniHuman 2.0 cancel request."""

    provider: Literal["byteplus-vision-cv"] = Field(
        default="byteplus-vision-cv", description="Provider surface that handled the request."
    )
    task_id: str = Field(description="Task ID the cancel request targeted.")
    canceled: bool = Field(
        description=(
            "True when the task was canceled. False when it could not be canceled because "
            "it had already started or finished; see message."
        )
    )
    request_id: str | None = Field(
        default=None, description="Provider diagnostic request ID, when returned."
    )
    message: str | None = Field(
        default=None, description="Explanation when canceled is false; otherwise null."
    )


async def omnihuman_cancel_task(
    input: OmniHumanCancelTaskInput, ctx: Context
) -> OmniHumanCancelTaskOutput | ToolResult:
    """Cancel an OmniHuman 2.0 task that is still waiting in the queue.

    Only queued tasks can be canceled. A task that already started generating
    returns canceled=false with an explanation rather than an error. Right after
    omnihuman_create_task the provider may not know the task yet and rejects the
    ID as invalid; wait a few seconds and retry.
    """
    runtime = get_runtime(ctx)
    owner = get_principal(ctx)
    await runtime.ownership_store.require_owner("vision-cv", input.task_id, owner)

    service = OmniHumanService()
    await context_log(ctx, "info", f"Canceling OmniHuman task {input.task_id}")
    try:
        result = await service.cancel(input.task_id)
    except ProviderError as exc:
        await context_log(ctx, "error", f"OmniHuman cancel failed: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    log_info("omnihuman_cancel_task_complete", task_id=input.task_id, canceled=result.canceled)
    return OmniHumanCancelTaskOutput(
        task_id=input.task_id,
        canceled=result.canceled,
        request_id=result.request_id,
        message=result.message,
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}
