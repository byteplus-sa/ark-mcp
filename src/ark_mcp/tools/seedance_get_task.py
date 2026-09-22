"""``seedance_get_task`` tool — retrieve the status and output of a Seedance task.

On first successful retrieval, copies 24-hour output URLs into
``ArtifactStore``. Persistence is serialized per task and cached by provider
task ID, so repeated or concurrent status checks do not download twice.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field

from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.models import (
    SeedanceQueueInfo,
    SeedanceTaskError,
    SeedanceTaskSettings,
    SeedanceTaskStatus,
    SeedanceTaskUsage,
)
from ark_mcp.observability.logger import info as log_info
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.providers.modelark.schemas import SeedanceTaskResponse
from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.providers.modelark.seedance_queue import queue_info
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import RuntimeServices, get_principal, get_runtime
from ark_mcp.security.auth_context import PrincipalContext
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._local_export import (
    local_export,
    output_path_field,
    overwrite_field,
)
from ark_mcp.tools._local_export import (
    needs_persist_output as _needs_persist_output,
)
from ark_mcp.tools._persistence import persist_from_url
from ark_mcp.tools._task_execution import context_log, persistence_requires_task


class SeedanceGetTaskInput(BaseModel):
    """Input model for ``seedance_get_task``."""

    task_id: str = Field(
        ...,
        description=(
            "Provider task ID from the task-augmented result of seedance_create_task, "
            "seedance_create_task_variations, seedance_2_5_create_task, "
            "or seedance_2_5_create_task_variations."
        ),
    )
    persist_output: bool = Field(
        True,
        description=(
            "Whether to copy provider output URLs into durable artifact storage on first successful retrieval. "
            "The default true requires task-augmented execution; use false for a foreground status check."
        ),
    )
    output_path: str | None = output_path_field(
        "the task video (a last frame is written beside it with a -last-frame suffix)"
    )
    overwrite: bool = overwrite_field()


class SeedanceTaskOutput(BaseModel):
    """Output model for ``seedance_get_task``."""

    task_id: str = Field(..., description="Provider task ID.")
    model: str = Field(..., description="Model ID used for generation.")
    status: SeedanceTaskStatus = Field(
        ...,
        description="Current task status: queued, running, succeeded, failed, cancelled, or expired.",
    )
    created_at: str = Field(..., description="ISO-8601 timestamp of task creation.")
    updated_at: str = Field(..., description="ISO-8601 timestamp of last status update.")
    error: SeedanceTaskError | None = Field(None, description="Error details if the task failed.")
    video: ArtifactRef | None = Field(
        None, description="Durable artifact reference for the generated video (on success)."
    )
    last_frame: ArtifactRef | None = Field(
        None,
        description="Durable artifact reference for the last frame, if return_last_frame was enabled.",
    )
    usage: SeedanceTaskUsage | None = Field(
        None, description="Token usage and billing information for the completed task."
    )
    settings: SeedanceTaskSettings = Field(
        default_factory=lambda: SeedanceTaskSettings(),
        description="Generation settings used for this task (resolution, ratio, duration, etc.).",
    )
    queue: SeedanceQueueInfo | None = Field(
        None,
        description=(
            "Server-derived queue timing for queued/running tasks (time queued, service tier, "
            "provider expiry deadline). ModelArk publishes no queue position or ETA. "
            "None once the task has finished."
        ),
    )


@local_export("video", "last_frame", precondition=_needs_persist_output)
async def seedance_get_task(
    input: SeedanceGetTaskInput, ctx: Context
) -> SeedanceTaskOutput | ToolResult:
    """Retrieve the status and output of a Seedance video generation task.

    On first successful retrieval with ``persist_output=True``, copies
    the 24-hour provider URLs into durable artifact storage. Subsequent
    calls return the cached artifact references without re-downloading. Supports
    optional MCP task-augmented execution for completed-output persistence.
    """
    task_error = persistence_requires_task(ctx, input.persist_output)
    if task_error is not None:
        return task_error
    await context_log(ctx, "info", f"Retrieving Seedance task {input.task_id}")
    await ctx.report_progress(progress=20, total=100)
    runtime = get_runtime(ctx)
    owner = get_principal(ctx)
    await runtime.ownership_store.require_owner("modelark", input.task_id, owner)

    service = SeedanceService()
    try:
        task, request_id = await call_with_retry(lambda: service.get_task(input.task_id))
    except ProviderError as exc:
        await context_log(ctx, "error", f"Failed to retrieve task: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    await ctx.report_progress(progress=60, total=100)

    # Persist video and last-frame on success (only once per task).
    video_ref: ArtifactRef | None = None
    last_frame_ref: ArtifactRef | None = None
    if task.status == "succeeded" and input.persist_output:
        video_ref, last_frame_ref = await persist_seedance_task_outputs(
            runtime, owner, input.task_id, task
        )

    await ctx.report_progress(progress=100, total=100)
    log_info(
        "seedance_task_retrieved",
        task_id=input.task_id,
        status=task.status,
        request_id=request_id,
    )

    return build_task_output(task, video_ref, last_frame_ref)


def build_task_output(
    task: SeedanceTaskResponse,
    video_ref: ArtifactRef | None,
    last_frame_ref: ArtifactRef | None,
) -> SeedanceTaskOutput:
    """Normalize a provider task (plus any persisted artifacts) into tool output."""
    error = None
    if task.error and (task.error.code or task.error.message):
        error = SeedanceTaskError(code=task.error.code, message=task.error.message)
    return SeedanceTaskOutput(
        task_id=task.id,
        model=task.model,
        status=task.status,  # type: ignore[arg-type]
        created_at=SeedanceService.get_created_at(task),
        updated_at=SeedanceService.get_updated_at(task),
        error=error,
        video=video_ref,
        last_frame=last_frame_ref,
        usage=SeedanceService.extract_usage(task),
        settings=SeedanceTaskSettings.model_validate(task.content or {}),
        queue=queue_info(task),
    )


def _is_durable(ref: ArtifactRef | None) -> bool:
    return ref is not None and ref.persistence_error is None


async def persist_seedance_task_outputs(
    runtime: RuntimeServices,
    owner: PrincipalContext,
    task_id: str,
    task: SeedanceTaskResponse,
) -> tuple[ArtifactRef | None, ArtifactRef | None]:
    """Persist a succeeded task's video and last frame exactly once.

    Serialized per task so overlapping polls never download the same output
    twice. Durable results are cached; if storage fails the temporary provider
    URL is returned with ``persistence_error`` and nothing is cached, so a later
    poll retries persistence.
    """
    async with runtime.task_artifact_locks.acquire("modelark", task_id) as singleflight:
        if singleflight.artifacts is not None:
            return singleflight.artifacts.get("video"), singleflight.artifacts.get("last_frame")
        cache = await runtime.task_artifact_cache.get("modelark", task_id)
        if cache:
            singleflight.artifacts = dict(cache)
            return cache.get("video"), cache.get("last_frame")

        store = runtime.artifact_store
        source_expiry = (datetime.now(UTC) + timedelta(hours=24)).isoformat()
        video_ref: ArtifactRef | None = None
        last_frame_ref: ArtifactRef | None = None
        if task.video_url:
            video_ref = await persist_from_url(
                store,
                url=task.video_url,
                media_type=MediaType.VIDEO,
                mime_type="video/mp4",
                source_expires_at=source_expiry,
                auth=owner,
            )
        if task.last_frame_url:
            last_frame_ref = await persist_from_url(
                store,
                url=task.last_frame_url,
                media_type=MediaType.IMAGE,
                mime_type="image/jpeg",
                source_expires_at=source_expiry,
                auth=owner,
            )

        video_ok = task.video_url is None or _is_durable(video_ref)
        last_frame_ok = task.last_frame_url is None or _is_durable(last_frame_ref)
        if video_ok and last_frame_ok:
            artifacts = {"video": video_ref, "last_frame": last_frame_ref}
            await runtime.task_artifact_cache.set("modelark", task_id, artifacts)
            singleflight.artifacts = artifacts
        else:
            log_warning("artifact_persist_failed", task_id=task_id, provider="modelark")
        return video_ref, last_frame_ref


# Tool annotation constants — camelCase per MCP specification.
TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
