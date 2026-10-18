"""``gen_task_cancel_or_delete`` tool — cancel or delete a ModelArk generation task.

The handler first retrieves the task, compares current and expected
status, enforces cancel-only-for-queued and delete-only-for-terminal
states, then calls DELETE. Registered with ``destructiveHint=True`` and
clear tool text describing the record-deletion behavior.

DELETE semantics (per official docs):
- ``queued``: cancel and transition to ``cancelled``
- ``running``: cannot cancel or delete
- ``succeeded``, ``failed``, ``expired``: delete the task record
- ``cancelled``: cannot delete
"""

from __future__ import annotations

from typing import Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, model_validator

from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.providers.modelark.generation_tasks import GenerationTaskService
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import get_principal, get_runtime
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._task_execution import context_log

# States where each mode is valid.
_CANCELABLE_STATES: frozenset[str] = frozenset({"queued"})
_DELETABLE_STATES: frozenset[str] = frozenset({"succeeded", "failed", "expired"})


class GenTaskCancelOrDeleteInput(BaseModel):
    """Input model for ``gen_task_cancel_or_delete``."""

    task_id: str = Field(
        ...,
        description="Generation task ID (Seedance, Hyper3D or Hitem3d).",
    )
    mode: Literal["cancel", "delete"] = Field(
        ...,
        description="'cancel' stops a queued task; 'delete' removes a terminal task's record.",
    )
    expected_status: Literal["queued", "succeeded", "failed", "expired"] = Field(
        ...,
        description="Status you expect the task to be in; the call is refused if it differs.",
    )
    confirm: Literal[True] = Field(
        True,
        description="Must be true to confirm this destructive action.",
    )

    @model_validator(mode="after")
    def validate_mode_status_consistency(self) -> GenTaskCancelOrDeleteInput:
        """Ensure the mode is valid for the expected status."""
        if self.mode == "cancel" and self.expected_status not in _CANCELABLE_STATES:
            raise ValueError(
                f"Cancel mode requires the task to be in 'queued' state, "
                f"but expected_status is '{self.expected_status}'. "
                f"Use mode='delete' for terminal states."
            )
        if self.mode == "delete" and self.expected_status not in _DELETABLE_STATES:
            raise ValueError(
                f"Delete mode requires the task to be in a terminal state "
                f"(succeeded, failed, expired), but expected_status is "
                f"'{self.expected_status}'. Use mode='cancel' for queued tasks."
            )
        return self


class GenTaskCancelOrDeleteOutput(BaseModel):
    """Output model for ``gen_task_cancel_or_delete``."""

    task_id: str = Field(..., description="The task ID that was cancelled or deleted.")
    mode: Literal["cancel", "delete"] = Field(
        ..., description="The action performed: cancel or delete."
    )
    previous_status: str = Field(
        ..., description="The task's status before the action was applied."
    )
    message: str = Field(..., description="Human-readable confirmation message.")


async def gen_task_cancel_or_delete(
    input: GenTaskCancelOrDeleteInput, ctx: Context
) -> GenTaskCancelOrDeleteOutput | ToolResult:
    """Cancel a queued task or delete a finished one (Seedance or 3D). Destructive.

    Cancelled tasks cannot be resumed; deleted tasks' outputs are no longer retrievable.
    The call is refused unless expected_status matches the task's actual status.
    """
    await context_log(
        ctx,
        "info",
        f"Generation task {input.mode} {input.task_id} (expected_status={input.expected_status})",
    )
    await ctx.report_progress(progress=20, total=100)
    await get_runtime(ctx).ownership_store.require_owner(
        "modelark",
        input.task_id,
        get_principal(ctx),
    )

    service = GenerationTaskService()

    # 1. Fetch current state.
    try:
        task, _ = await call_with_retry(lambda: service.get_task(input.task_id))
    except ProviderError as exc:
        await context_log(ctx, "error", f"Failed to fetch task state: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    await ctx.report_progress(progress=40, total=100)

    actual_status = task.status
    if actual_status != input.expected_status:
        raise ValueError(
            f"Task '{input.task_id}' has status '{actual_status}', "
            f"but expected '{input.expected_status}'. "
            f"Refusing to {input.mode} to prevent unintended action. "
            f"Re-fetch the task and update expected_status."
        )

    # 2. Enforce mode/state rules.
    if input.mode == "cancel" and actual_status not in _CANCELABLE_STATES:
        raise ValueError(
            f"Cannot cancel task in '{actual_status}' state. "
            f"Cancel is only allowed for 'queued' tasks."
        )
    if input.mode == "delete" and actual_status not in _DELETABLE_STATES:
        raise ValueError(
            f"Cannot delete task in '{actual_status}' state. "
            f"Delete is only allowed for terminal states "
            f"(succeeded, failed, expired). "
            f"Running tasks cannot be cancelled or deleted; cancelled tasks cannot be deleted."
        )

    await ctx.report_progress(progress=60, total=100)

    # 3. Issue DELETE.
    service = GenerationTaskService()
    try:
        request_id = await call_with_retry(lambda: service.delete_task(input.task_id))
    except ProviderError as exc:
        await context_log(ctx, "error", f"DELETE failed: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    await ctx.report_progress(progress=100, total=100)

    message = (
        f"Task '{input.task_id}' has been {'cancelled' if input.mode == 'cancel' else 'deleted'}."
    )
    log_warning(
        "generation_task_destructive_action",
        task_id=input.task_id,
        mode=input.mode,
        previous_status=actual_status,
        request_id=request_id,
    )

    return GenTaskCancelOrDeleteOutput(
        task_id=input.task_id,
        mode=input.mode,
        previous_status=actual_status,
        message=message,
    )


# Tool annotation constants — camelCase per MCP specification.
TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}
