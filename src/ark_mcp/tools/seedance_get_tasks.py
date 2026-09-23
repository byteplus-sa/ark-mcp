"""``seedance_get_tasks`` tool — check many Seedance tasks in one call.

Replaces a round of N ``seedance_get_task`` calls. Tasks are fetched with one
``list_tasks(filter.task_ids=...)`` provider call per page of 20; any task the
list omits, and any succeeded task whose list entry lacks an output URL, is
re-fetched individually. Persistence
reuses the per-task single-flight helper, so overlapping polls never
download the same video twice.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field

from ark_mcp.artifacts.export import export_ref, prepare_local_export
from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.schemas import SeedanceTaskResponse
from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import get_principal, get_runtime
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._local_export import output_dir_field, overwrite_field
from ark_mcp.tools._task_execution import context_log, persistence_requires_task
from ark_mcp.tools.seedance_get_task import (
    SeedanceTaskOutput,
    build_task_output,
    persist_seedance_task_outputs,
)

_TERMINAL = frozenset({"succeeded", "failed", "expired", "cancelled"})
_MAX_CONCURRENT_FETCHES = 5
# The provider's documented list page size; batches larger than this are paged.
_LIST_PAGE_SIZE = 20
_MAX_LIST_PAGES = 3


class SeedanceGetTasksInput(BaseModel):
    """Input model for ``seedance_get_tasks``."""

    task_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=50,
        description="1-50 Seedance task IDs to check. Duplicates are ignored.",
    )
    persist_output: bool = Field(
        False,
        description=(
            "Copy each succeeded task's video (and last frame) into durable artifact storage, "
            "once per task. Requires task-augmented execution (or ark_job_submit)."
        ),
    )
    output_dir: str | None = output_dir_field("each succeeded task's video and last frame")
    overwrite: bool = overwrite_field()


class SeedanceTaskLookupError(BaseModel):
    """A task ID that could not be checked."""

    task_id: str = Field(..., description="The task ID that failed.")
    code: str = Field(
        ..., description="NOT_OWNED (not created by this caller), NOT_FOUND, or a provider code."
    )
    message: str = Field(..., description="Human-readable reason.")


class SeedanceGetTasksOutput(BaseModel):
    """Output model for ``seedance_get_tasks``."""

    tasks: list[SeedanceTaskOutput] = Field(
        ...,
        description="Full status for each found task, in request order (same shape as seedance_get_task).",
    )
    errors: list[SeedanceTaskLookupError] = Field(
        default_factory=list, description="Task IDs that could not be checked."
    )
    counts: dict[str, int] = Field(
        ..., description="Number of tasks per status (queued, running, succeeded, failed, ...)."
    )
    all_terminal: bool = Field(
        ...,
        description="True when every found task has finished, so polling can stop.",
    )
    active_tasks: int = Field(
        ..., description="Number of tasks in this batch that are still queued or running."
    )


async def seedance_get_tasks(
    input: SeedanceGetTasksInput, ctx: Context
) -> SeedanceGetTasksOutput | ToolResult:
    """Check the status of several Seedance tasks in one call.

    Returns the same per-task shape as seedance_get_task (status, errors,
    usage, settings, derived queue timing, and persisted video/last-frame
    artifacts when persist_output=true), plus per-status counts and
    all_terminal to decide whether to keep polling. Use output_dir to write
    finished videos straight to a local directory. Task IDs not created by
    the caller are reported in errors instead of failing the batch.
    """
    task_error = persistence_requires_task(ctx, input.persist_output)
    if task_error is not None:
        return task_error
    runtime = get_runtime(ctx)
    owner = get_principal(ctx)
    if input.output_dir is not None and not input.persist_output:
        raise ValueError("output_dir requires persist_output=true.")
    target = await prepare_local_export(
        output_path=None,
        output_dir=input.output_dir,
        overwrite=input.overwrite,
        ctx=ctx,
        settings=runtime.settings,
    )

    task_ids = list(dict.fromkeys(input.task_ids))
    await context_log(ctx, "info", f"Checking {len(task_ids)} Seedance tasks")

    errors: list[SeedanceTaskLookupError] = []
    owned: list[str] = []
    for task_id in task_ids:
        try:
            await runtime.ownership_store.require_owner("modelark", task_id, owner)
        except (PermissionError, LookupError, ValueError) as exc:
            errors.append(
                SeedanceTaskLookupError(task_id=task_id, code="NOT_OWNED", message=str(exc))
            )
        else:
            owned.append(task_id)

    service = SeedanceService()
    found: dict[str, SeedanceTaskResponse] = {}
    try:
        if owned:
            # The provider may cap page_size below the batch size, so page until
            # every requested task is seen or a page comes back empty.
            for page_num in range(1, _MAX_LIST_PAGES + 1):
                page, _ = await call_with_retry(
                    lambda page_num=page_num: service.list_tasks(  # type: ignore[misc]
                        page=page_num, page_size=_LIST_PAGE_SIZE, task_ids=owned
                    )
                )
                found.update({task.id: task for task in page.data if task.id in owned})
                if len(found) >= len(owned) or not page.data:
                    break

            # Fall back to a full fetch where the list entry is missing or has no output URL.
            refetch = [
                task_id
                for task_id in owned
                if task_id not in found
                or (found[task_id].status == "succeeded" and not found[task_id].video_url)
            ]
            limiter = asyncio.Semaphore(_MAX_CONCURRENT_FETCHES)

            async def fetch(task_id: str) -> tuple[str, SeedanceTaskResponse | ProviderError]:
                async with limiter:
                    try:
                        task, _request_id = await call_with_retry(lambda: service.get_task(task_id))
                    except ProviderError as exc:
                        return task_id, exc
                    return task_id, task

            for task_id, outcome in await asyncio.gather(*(fetch(t) for t in refetch)):
                if isinstance(outcome, ProviderError):
                    found.pop(task_id, None)
                    code: Literal["NOT_FOUND"] | str = (
                        "NOT_FOUND"
                        if outcome.http_status == 404
                        else (outcome.code or "PROVIDER_ERROR")
                    )
                    errors.append(
                        SeedanceTaskLookupError(task_id=task_id, code=code, message=outcome.message)
                    )
                else:
                    found[task_id] = outcome
    except ProviderError as exc:
        await context_log(ctx, "error", f"Failed to list tasks: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    tasks: list[SeedanceTaskOutput] = []
    for task_id in owned:
        task = found.get(task_id)
        if task is None:
            continue
        video_ref = last_frame_ref = None
        if task.status == "succeeded" and input.persist_output:
            video_ref, last_frame_ref = await persist_seedance_task_outputs(
                runtime, owner, task_id, task
            )
            video_ref = await export_ref(runtime.artifact_store, video_ref, target, auth=owner)
            last_frame_ref = await export_ref(
                runtime.artifact_store, last_frame_ref, target, auth=owner
            )
        tasks.append(build_task_output(task, video_ref, last_frame_ref))

    counts = dict(Counter(str(task.status) for task in tasks))
    active = sum(1 for task in tasks if task.status not in _TERMINAL)
    log_info("seedance_tasks_retrieved", requested=len(task_ids), found=len(tasks), active=active)
    return SeedanceGetTasksOutput(
        tasks=tasks,
        errors=errors,
        counts=counts,
        all_terminal=active == 0,
        active_tasks=active,
    )


# Not read-only: output_path/output_dir can write local files.
TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
