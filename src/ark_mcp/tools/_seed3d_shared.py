"""Shared Seed3D tool components.

Input type ``Seed3DImageInput`` and the shared create/get/list/cancel-delete
executors used by the Hyper3D and Hitem3d tool handlers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, model_validator

from ark_mcp.config.model_capabilities import Seed3DCapabilities
from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.media import MediaSource
from ark_mcp.domain.models import (
    Seed3DTaskError,
    Seed3DTaskStatus,
    Seed3DTaskSummary,
    Seed3DTaskUsage,
)
from ark_mcp.observability.logger import info as log_info
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.providers.modelark.seed3d import Seed3DService
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import billed_provider_slot, get_principal, get_runtime
from ark_mcp.tools._cost import log_cost_estimate
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._local_export import output_path_field, overwrite_field
from ark_mcp.tools._persistence import persist_from_url
from ark_mcp.tools._task_execution import context_log, persistence_requires_task


class Seed3DImageInput(MediaSource):
    """Image input for 3D generation (Hyper3D and Hitem3d)."""

    MEDIA_CATEGORY: ClassVar[MediaType] = MediaType.IMAGE


class Seed3DCreateTaskOutput(BaseModel):
    """Output model for 3D task creation tools."""

    task_id: str = Field(..., description="Provider task ID for polling and management.")
    status: Literal["queued"] = Field(
        "queued", description="Initial task status. Poll with the get tool for updates."
    )
    recommended_poll_after_ms: int = Field(
        ..., description="Suggested delay in milliseconds before first poll."
    )


class Seed3DGetTaskInput(BaseModel):
    """Input model for 3D task retrieval tools."""

    task_id: str = Field(
        ...,
        description="Provider task ID from the task-augmented result of the corresponding 3D create task tool.",
    )
    persist_output: bool = Field(
        True,
        description=(
            "Whether to copy provider output URLs into durable artifact storage on first successful retrieval. "
            "The default true requires task-augmented execution; use false for a foreground status check."
        ),
    )
    output_path: str | None = output_path_field("the 3D file")
    overwrite: bool = overwrite_field()


class Seed3DTaskOutput(BaseModel):
    """Output model for 3D task retrieval tools."""

    task_id: str = Field(..., description="Provider task ID.")
    model: str = Field(..., description="Model ID used for generation.")
    status: Seed3DTaskStatus = Field(
        ...,
        description="Current task status: queued, running, succeeded, failed, cancelled, or expired.",
    )
    created_at: str = Field(..., description="ISO-8601 timestamp of task creation.")
    updated_at: str = Field(..., description="ISO-8601 timestamp of last status update.")
    error: Seed3DTaskError | None = Field(None, description="Error details if the task failed.")
    file: ArtifactRef | None = Field(
        None, description="Durable artifact reference for the generated 3D file (on success)."
    )
    usage: Seed3DTaskUsage | None = Field(
        None, description="Token usage and billing information for the completed task."
    )


class Seed3DListTasksInput(BaseModel):
    """Input model for 3D task listing tools."""

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
    status: Seed3DTaskStatus | None = Field(
        None,
        description="Filter tasks by status (queued, running, succeeded, failed, cancelled, expired).",
    )
    task_ids: list[str] | None = Field(
        None,
        description="Filter to specific task IDs. Non-owner users can only query their own tasks.",
    )
    model: str | None = Field(
        None,
        description="Filter tasks by model ID. Omit to use the family default.",
    )


class Seed3DTaskPage(BaseModel):
    """Output model for 3D task listing tools."""

    tasks: list[Seed3DTaskSummary] = Field(..., description="Task summaries for the current page.")
    total: int = Field(..., description="Total number of matching tasks.")
    page: int = Field(..., description="Current page number (1-based).")
    page_size: int = Field(..., description="Number of tasks per page.")
    has_more: bool = Field(False, description="Whether more pages are available.")


class Seed3DCancelOrDeleteInput(BaseModel):
    """Input model for 3D cancel/delete tools."""

    task_id: str = Field(..., description="The task ID to cancel or delete.")
    mode: Literal["cancel", "delete"] = Field(
        ...,
        description="Action to perform: 'cancel' stops a queued task; 'delete' removes the record of a terminal task.",
    )
    expected_status: Literal["queued", "succeeded", "failed", "expired"] = Field(
        ...,
        description=(
            "The caller's expected current status. The handler verifies "
            "this matches the actual status before issuing DELETE, "
            "preventing accidental cancellation or deletion."
        ),
    )
    confirm: Literal[True] = Field(
        True,
        description="Must be True to confirm the destructive action. This is a safety guard.",
    )

    @model_validator(mode="after")
    def validate_mode_status_consistency(self) -> Seed3DCancelOrDeleteInput:
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


class Seed3DCancelOrDeleteOutput(BaseModel):
    """Output model for 3D cancel/delete tools."""

    task_id: str = Field(..., description="The task ID that was cancelled or deleted.")
    mode: Literal["cancel", "delete"] = Field(
        ..., description="The action performed: cancel or delete."
    )
    previous_status: str = Field(
        ..., description="The task's status before the action was applied."
    )
    message: str = Field(..., description="Human-readable confirmation message.")


_CANCELABLE_STATES: frozenset[str] = frozenset({"queued"})
_DELETABLE_STATES: frozenset[str] = frozenset({"succeeded", "failed", "expired"})


def _family_label(family: str) -> str:
    """Return a human-readable family label for logging and messages."""
    return "Hyper3D" if family == "hyper3d" else "Hitem3d"


_3D_MIME_BY_EXT: dict[str, str] = {
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".obj": "text/plain",
    ".stl": "application/octet-stream",
    ".fbx": "application/octet-stream",
    ".usdz": "model/vnd.usdz+zip",
    ".usd": "model/vnd.usdz+zip",
    ".zip": "application/zip",
}


def _infer_3d_mime(file_url: str) -> str:
    """Infer a MIME type for a generated 3D file from its URL extension."""
    lowered = file_url.lower().split("?")[0]
    for ext, mime in _3D_MIME_BY_EXT.items():
        if lowered.endswith(ext):
            return mime
    return "application/zip"


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


async def seed3d_get_task_impl(
    input: Seed3DGetTaskInput, ctx: Context, family: str
) -> Seed3DTaskOutput | ToolResult:
    """Retrieve a Seed3D task and persist the generated 3D file on success."""
    task_error = persistence_requires_task(ctx, input.persist_output)
    if task_error is not None:
        return task_error
    label = _family_label(family)
    await context_log(ctx, "info", f"Retrieving {label} task {input.task_id}")
    await ctx.report_progress(progress=20, total=100)
    runtime = get_runtime(ctx)
    owner = get_principal(ctx)
    await runtime.ownership_store.require_owner("modelark", input.task_id, owner)

    service = Seed3DService()
    try:
        task, request_id = await call_with_retry(lambda: service.get_task(input.task_id))
    except ProviderError as exc:
        await context_log(ctx, "error", f"Failed to retrieve task: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    await ctx.report_progress(progress=60, total=100)

    error_dict = None
    if task.error and (task.error.code or task.error.message):
        error_dict = Seed3DTaskError(
            code=task.error.code,
            message=task.error.message,
        )

    file_ref: ArtifactRef | None = None
    if task.status == "succeeded" and input.persist_output:
        cache = await runtime.task_artifact_cache.get("modelark", input.task_id)
        if cache:
            file_ref = cache.get("file")
        else:
            store = runtime.artifact_store
            source_expiry = (datetime.now(UTC) + timedelta(hours=24)).isoformat()

            if task.file_url:
                file_ref = await persist_from_url(
                    store,
                    url=task.file_url,
                    media_type=MediaType.THREE_D,
                    mime_type=_infer_3d_mime(task.file_url),
                    source_expires_at=source_expiry,
                    auth=owner,
                )
                if file_ref.persistence_error is not None:
                    await context_log(
                        ctx,
                        "warning",
                        f"Failed to persist 3D file artifact: {file_ref.persistence_error.message}",
                    )

            if task.file_url is None or (
                file_ref is not None and file_ref.persistence_error is None
            ):
                await runtime.task_artifact_cache.set(
                    "modelark",
                    input.task_id,
                    {"file": file_ref},
                )

    await ctx.report_progress(progress=100, total=100)
    log_info(
        "seed3d_task_retrieved",
        task_id=input.task_id,
        family=family,
        status=task.status,
        request_id=request_id,
    )

    return Seed3DTaskOutput(
        task_id=task.id,
        model=task.model,
        status=task.status,  # type: ignore[arg-type]
        created_at=Seed3DService.get_created_at(task),
        updated_at=Seed3DService.get_updated_at(task),
        error=error_dict,
        file=file_ref,
        usage=Seed3DService.extract_usage(task),
    )


async def seed3d_list_tasks_impl(
    input: Seed3DListTasksInput, ctx: Context, family: str
) -> Seed3DTaskPage | ToolResult:
    """List recent Seed3D tasks for a family."""
    label = _family_label(family)
    await context_log(ctx, "info", f"Listing {label} tasks")
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
            return Seed3DTaskPage(
                tasks=[],
                total=0,
                page=input.page or 1,
                page_size=input.page_size or 20,
                has_more=False,
            )

    service = Seed3DService()
    try:
        response, request_id = await call_with_retry(
            lambda: service.list_tasks(
                page=input.page or 1,
                page_size=input.page_size or 20,
                status=input.status,
                task_ids=requested_task_ids,
                model=input.model,
            )
        )
    except ProviderError as exc:
        await context_log(ctx, "error", f"Failed to list tasks: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    await ctx.report_progress(progress=100, total=100)
    log_info(
        "seed3d_tasks_listed",
        count=len(response.items),
        total=response.total,
        request_id=request_id,
    )

    return Seed3DTaskPage(
        tasks=[Seed3DService.to_task_summary(t) for t in response.items],
        total=response.total,
        page=input.page or 1,
        page_size=input.page_size or 20,
        has_more=response.has_more or False,
    )


async def seed3d_cancel_or_delete_impl(
    input: Seed3DCancelOrDeleteInput, ctx: Context, family: str
) -> Seed3DCancelOrDeleteOutput | ToolResult:
    """Cancel (queued) or delete (terminal) a Seed3D task."""
    label = _family_label(family)
    await context_log(
        ctx,
        "info",
        f"{label} {input.mode} task {input.task_id} (expected_status={input.expected_status})",
    )
    await ctx.report_progress(progress=20, total=100)
    await get_runtime(ctx).ownership_store.require_owner(
        "modelark",
        input.task_id,
        get_principal(ctx),
    )

    service = Seed3DService()
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

    service = Seed3DService()
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
        "seed3d_task_destructive_action",
        task_id=input.task_id,
        family=family,
        mode=input.mode,
        previous_status=actual_status,
        request_id=request_id,
    )

    return Seed3DCancelOrDeleteOutput(
        task_id=input.task_id,
        mode=input.mode,
        previous_status=actual_status,
        message=message,
    )
