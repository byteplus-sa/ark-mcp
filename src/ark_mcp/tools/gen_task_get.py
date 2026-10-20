"""``gen_task_get`` tool — check ModelArk generation tasks (Seedance, Hyper3D, Hitem3d).

Tasks are fetched with one ``list_tasks(filter.task_ids=...)`` call per page;
any task the list omits, and any succeeded task whose list entry lacks output
URLs, is re-fetched individually.
"""

from __future__ import annotations

import asyncio

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field

from ark_mcp.artifacts.export import export_ref, prepare_local_export
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.models import GenTaskLookupError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.generation_tasks import GenerationTaskService
from ark_mcp.providers.modelark.schemas import GenerationTaskResponse
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import get_principal, get_runtime
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._generation_tasks_shared import (
    RECOMMENDED_POLL_AFTER_MS,
    TERMINAL_STATUSES,
    GenTask,
    build_gen_task,
    build_outputs,
    classify_task,
    needs_full_fetch,
    persist_task_outputs,
)
from ark_mcp.tools._local_export import output_dir_field, overwrite_field
from ark_mcp.tools._task_execution import context_log, persistence_requires_task

_MAX_CONCURRENT_FETCHES = 5
_LIST_PAGE_SIZE = 20
_MAX_LIST_PAGES = 3


class GenTaskGetInput(BaseModel):
    """Input model for ``gen_task_get``."""

    task_ids: list[str] = Field(
        ...,
        min_length=1,
        max_length=50,
        description=(
            "1-50 ModelArk generation task IDs returned by seedance_create_task, "
            "hyper3d_create_task, or hitem3d_create_task (not ark_job IDs). Duplicates are "
            "ignored."
        ),
    )
    persist_output: bool = Field(
        False,
        description=(
            "Copy each succeeded task's outputs into durable artifact storage, once per task. "
            "Requires background execution (ark_job_submit or native task augmentation); leave "
            "false for a foreground status poll."
        ),
    )
    output_dir: str | None = output_dir_field("succeeded tasks' outputs")
    overwrite: bool = overwrite_field()


class GenTaskGetOutput(BaseModel):
    """Output model for ``gen_task_get``."""

    tasks: list[GenTask] = Field(..., description="One entry per found task, in request order.")
    errors: list[GenTaskLookupError] = Field(
        default_factory=list,
        description="Task IDs that could not be checked (NOT_OWNED, NOT_FOUND, or a provider code).",
    )
    all_terminal: bool = Field(
        ..., description="True when every found task has finished, so polling can stop."
    )
    active_tasks: int = Field(..., description="Number of found tasks still queued or running.")
    recommended_poll_after_ms: int | None = Field(
        None,
        description="Suggested delay in milliseconds before the next poll; null when all_terminal.",
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


async def gen_task_get(input: GenTaskGetInput, ctx: Context) -> GenTaskGetOutput | ToolResult:
    """Check status and outputs of ModelArk generation tasks (Seedance video, 3D).

    Pass every task ID in one call. queued or running is not a failure: poll again after
    recommended_poll_after_ms, never resubmit the create tool (that bills a second task).
    Provider output URLs expire in about 24 hours; once all_terminal is true, call again
    through ark_job_submit with persist_output=true to keep durable artifacts. IDs the
    caller does not own are listed in errors.
    """
    task_error = persistence_requires_task(ctx, input.persist_output)
    if task_error is not None:
        return task_error
    if input.output_dir is not None and not input.persist_output:
        raise ValueError("output_dir requires persist_output=true.")
    runtime = get_runtime(ctx)
    owner = get_principal(ctx)
    target = await prepare_local_export(
        output_path=None,
        output_dir=input.output_dir,
        overwrite=input.overwrite,
        ctx=ctx,
        settings=runtime.settings,
    )

    task_ids = list(dict.fromkeys(input.task_ids))
    await context_log(ctx, "info", f"Checking {len(task_ids)} generation tasks")
    errors: list[GenTaskLookupError] = []
    owned: list[str] = []
    for task_id in task_ids:
        try:
            await runtime.ownership_store.require_owner("modelark", task_id, owner)
        except (PermissionError, LookupError, ValueError) as exc:
            errors.append(GenTaskLookupError(task_id=task_id, code="NOT_OWNED", message=str(exc)))
        else:
            owned.append(task_id)

    try:
        found = await _fetch_tasks(owned, errors) if owned else {}
    except ProviderError as exc:
        await context_log(ctx, "error", f"Failed to list tasks: {exc.message}")
        return provider_error_result(exc)

    tasks: list[GenTask] = []
    for task_id in owned:
        task = found.get(task_id)
        if task is None:
            continue
        family, kind = classify_task(task.model)
        persisted = None
        if input.persist_output and task.status == "succeeded" and kind != "unknown":
            persisted = await persist_task_outputs(runtime, owner, task, kind)
            for role, ref in persisted.items():
                persisted[role] = await export_ref(runtime.artifact_store, ref, target, auth=owner)
        outputs = build_outputs(task, kind, persisted)
        tasks.append(build_gen_task(task, family=family, kind=kind, outputs=outputs))

    active = sum(1 for task in tasks if task.status not in TERMINAL_STATUSES)
    log_info("generation_tasks_retrieved", requested=len(task_ids), found=len(tasks), active=active)
    return GenTaskGetOutput(
        tasks=tasks,
        errors=errors,
        all_terminal=active == 0,
        active_tasks=active,
        recommended_poll_after_ms=RECOMMENDED_POLL_AFTER_MS if active else None,
    )


async def _fetch_tasks(
    owned: list[str], errors: list[GenTaskLookupError]
) -> dict[str, GenerationTaskResponse]:
    service = GenerationTaskService()
    found: dict[str, GenerationTaskResponse] = {}
    try:
        for page_num in range(1, _MAX_LIST_PAGES + 1):
            page, _ = await call_with_retry(
                lambda page_num=page_num: service.list_tasks(  # type: ignore[misc]
                    page=page_num, page_size=_LIST_PAGE_SIZE, task_ids=owned
                )
            )
            found.update({task.id: task for task in page.data if task.id in owned})
            if len(found) >= len(owned) or not page.data:
                break

        refetch = [
            task_id
            for task_id in owned
            if task_id not in found
            or needs_full_fetch(found[task_id], classify_task(found[task_id].model)[1])
        ]
        limiter = asyncio.Semaphore(_MAX_CONCURRENT_FETCHES)

        async def fetch(task_id: str) -> tuple[str, GenerationTaskResponse | ProviderError]:
            async with limiter:
                try:
                    task, _request_id = await call_with_retry(lambda: service.get_task(task_id))
                except ProviderError as exc:
                    return task_id, exc
                return task_id, task

        for task_id, outcome in await asyncio.gather(*(fetch(t) for t in refetch)):
            if isinstance(outcome, ProviderError):
                found.pop(task_id, None)
                code = (
                    "NOT_FOUND"
                    if outcome.http_status == 404
                    else (outcome.code or "PROVIDER_ERROR")
                )
                errors.append(
                    GenTaskLookupError(task_id=task_id, code=code, message=outcome.message)
                )
            else:
                found[task_id] = outcome
    finally:
        await service.close()
    return found
