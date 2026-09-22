"""Poll and persist BytePlus VOD AI MediaKit enhancement tasks."""

from __future__ import annotations

from typing import Annotated, Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import AnyUrl, BaseModel, Field, UrlConstraints, model_validator

from ark_mcp.artifacts.store import ArtifactPersistenceError
from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.providers.vod_mediakit.enhancement import VodMediaKitEnhancementService
from ark_mcp.providers.vod_mediakit.schemas import EnhancementTask
from ark_mcp.runtime import get_principal, get_runtime
from ark_mcp.security.auth_context import PrincipalContext
from ark_mcp.security.media_policy import get_media_limits
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._local_export import (
    local_export,
    output_path_field,
    overwrite_field,
)
from ark_mcp.tools._local_export import (
    needs_persist_output as _needs_persist_output,
)
from ark_mcp.tools._task_execution import context_log, persistence_requires_task
from ark_mcp.tools._vod_shared import VodArtifactPersistenceIssue

HttpsUrl = Annotated[AnyUrl, UrlConstraints(allowed_schemes=["https"])]


class VodGetEnhancementTaskInput(BaseModel):
    """Input for ``vod_get_enhancement_task``."""

    task_id: str = Field(
        description="Provider task ID from the task-augmented result of vod_enhance_video."
    )
    persist_output: bool = Field(
        default=True,
        description=(
            "Whether to copy a completed output into durable artifact storage on first successful poll. "
            "The default true requires task-augmented execution; use false for a foreground status check."
        ),
    )
    output_path: str | None = output_path_field("the enhanced video")
    overwrite: bool = overwrite_field()


class VodEnhancementTaskFailure(BaseModel):
    """Safe provider failure detail for a failed enhancement task."""

    code: str | None = Field(default=None, description="Provider failure code, when returned.")
    message: str = Field(
        description="Safe provider failure explanation without credentials or signed URLs."
    )


class VodEnhancementTaskOutput(BaseModel):
    """Normalized status and output of a VOD AI MediaKit enhancement task."""

    provider: Literal["byteplus-vod-mediakit"] = Field(
        default="byteplus-vod-mediakit", description="Provider surface that enhanced the video."
    )
    task_id: str = Field(description="Provider enhancement task ID.")
    status: Literal["processing", "succeeded", "failed"] = Field(
        description="Normalized enhancement state: processing, succeeded, or failed."
    )
    provider_status: str | None = Field(
        default=None, description="Raw provider status label, when returned."
    )
    request_id: str | None = Field(
        default=None, description="Provider diagnostic request ID, when returned."
    )
    duration_seconds: float | None = Field(
        default=None, ge=0, description="Output duration in seconds, when reported."
    )
    fps: int | None = Field(
        default=None, ge=1, description="Output frame rate in frames per second, when reported."
    )
    resolution: str | None = Field(
        default=None, description="Output resolution label, such as '4k', when reported."
    )
    tool_version: str | None = Field(
        default=None, description="Enhancement tier reported by MediaKit, such as 'professional'."
    )
    created_at: str | None = Field(
        default=None,
        description="ISO-8601 task creation time normalized from the provider response.",
    )
    finished_at: str | None = Field(
        default=None,
        description="ISO-8601 task completion time normalized from the provider response.",
    )
    video: ArtifactRef | None = Field(
        default=None, description="Durable enhanced-video artifact when persistence succeeds."
    )
    source_url: HttpsUrl | None = Field(
        default=None,
        description="Expiring provider output URL for a succeeded task, preserved if persistence fails.",
    )
    source_expires_at: str | None = Field(
        default=None, description="ISO-8601 expiry for source_url, when reported."
    )
    persistence: Literal["not_applicable", "not_requested", "persisted", "failed"] = Field(
        description="Outcome of durable artifact persistence, independent of provider success."
    )
    persistence_issue: VodArtifactPersistenceIssue | None = Field(
        default=None, description="Safe explanation when the output could not be persisted."
    )
    error: VodEnhancementTaskFailure | None = Field(
        default=None, description="Provider failure code and safe message for a failed task."
    )

    @model_validator(mode="after")
    def validate_state(self) -> VodEnhancementTaskOutput:
        """Enforce state-specific output, persistence, and error fields."""
        if self.status == "processing":
            if self.source_url is not None or self.video is not None:
                raise ValueError("processing output must not carry output metadata")
            if self.persistence != "not_applicable":
                raise ValueError("processing output must set persistence=not_applicable")
        elif self.status == "succeeded":
            if self.source_url is None:
                raise ValueError("succeeded output requires source_url")
            if self.persistence == "not_applicable":
                raise ValueError("succeeded output must not use not_applicable persistence")
            if self.persistence == "persisted" and (
                self.video is None or self.persistence_issue is not None
            ):
                raise ValueError("persisted output requires video and forbids a persistence issue")
            if self.persistence == "failed" and self.persistence_issue is None:
                raise ValueError("failed persistence requires a persistence issue")
            if self.persistence == "not_requested" and (
                self.video is not None or self.persistence_issue is not None
            ):
                raise ValueError("not_requested persistence forbids video and persistence issue")
            if self.error is not None:
                raise ValueError("succeeded output must not carry a provider failure")
        elif self.error is None:
            raise ValueError("failed output requires provider failure detail")
        return self


async def _persist_output(
    ctx: Context,
    owner: PrincipalContext,
    task_id: str,
    task: EnhancementTask,
    persist_output: bool,
) -> tuple[
    ArtifactRef | None,
    VodArtifactPersistenceIssue | None,
    Literal["not_requested", "persisted", "failed"],
]:
    """Best-effort copy of a completed enhancement into the artifact store."""
    if task.output_url is None or not persist_output:
        return None, None, "not_requested"

    runtime = get_runtime(ctx)
    async with runtime.task_artifact_locks.acquire("vod-mediakit", task_id) as singleflight:
        if singleflight.artifacts and singleflight.artifacts.get("video") is not None:
            return singleflight.artifacts["video"], None, "persisted"

        try:
            cached = await runtime.task_artifact_cache.get("vod-mediakit", task_id)
            cached_video = cached.get("video") if cached else None
        except Exception:
            cached_video = None
            await context_log(ctx, "warning", "VOD enhancement artifact cache lookup failed.")
        if cached_video is not None:
            singleflight.artifacts = {"video": cached_video}
            return cached_video, None, "persisted"

        try:
            video_ref = await runtime.artifact_store.copy_from_trusted_url(
                url=str(task.output_url),
                media_type=MediaType.VIDEO,
                mime_type="video/mp4",
                source_expires_at=task.source_expires_at,
                auth=owner,
            )
        except ArtifactPersistenceError as exc:
            await context_log(
                ctx, "warning", f"VOD enhancement output persistence failed: {exc.safe_message}"
            )
            return (
                None,
                VodArtifactPersistenceIssue(
                    code=exc.code,
                    message=exc.safe_message,
                    retryable=exc.retryable,
                    artifact_limit_bytes=get_media_limits().video_max_bytes,
                ),
                "failed",
            )
        except Exception:
            await context_log(
                ctx,
                "warning",
                "VOD enhancement output persistence failed due to an internal storage error.",
            )
            return (
                None,
                VodArtifactPersistenceIssue(
                    code="storage_failed",
                    message="Provider output could not be written to artifact storage.",
                    retryable=True,
                    artifact_limit_bytes=get_media_limits().video_max_bytes,
                ),
                "failed",
            )

        singleflight.artifacts = {"video": video_ref}
        try:
            await runtime.task_artifact_cache.set("vod-mediakit", task_id, {"video": video_ref})
        except Exception:
            await context_log(
                ctx,
                "warning",
                "VOD enhancement artifact cache update failed; the artifact remains available.",
            )
        return video_ref, None, "persisted"


@local_export("video", precondition=_needs_persist_output)
async def vod_get_enhancement_task(
    input: VodGetEnhancementTaskInput, ctx: Context
) -> VodEnhancementTaskOutput | ToolResult:
    """Poll and retrieve a BytePlus VOD AI MediaKit enhancement task.

    On the first successful poll with ``persist_output=True``, copies the
    expiring output into durable artifact storage and caches the artifact.
    Supports optional MCP task-augmented execution for completed-output
    persistence.
    """
    task_error = persistence_requires_task(ctx, input.persist_output)
    if task_error is not None:
        return task_error
    await context_log(ctx, "info", f"Retrieving VOD AI MediaKit enhancement task {input.task_id}")
    await ctx.report_progress(progress=20, total=100)
    runtime = get_runtime(ctx)
    owner = get_principal(ctx)
    await runtime.ownership_store.require_owner("vod-mediakit", input.task_id, owner)

    service = VodMediaKitEnhancementService()
    try:
        task = await call_with_retry(lambda: service.get(input.task_id))
    except ProviderError as exc:
        await context_log(ctx, "error", f"Failed to retrieve enhancement task: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    await ctx.report_progress(progress=60, total=100)

    if task.status == "succeeded":
        video_ref, issue, persistence = await _persist_output(
            ctx, owner, input.task_id, task, input.persist_output
        )
        log_info(
            "vod_get_enhancement_task_complete",
            task_id=task.task_id,
            status="succeeded",
            persistence=persistence,
        )
        return VodEnhancementTaskOutput(
            task_id=task.task_id,
            status="succeeded",
            provider_status=task.provider_status,
            request_id=task.request_id,
            duration_seconds=task.duration_seconds,
            fps=task.fps,
            resolution=task.resolution,
            tool_version=task.tool_version,
            created_at=task.created_at,
            finished_at=task.finished_at,
            video=video_ref,
            source_url=task.output_url,
            source_expires_at=task.source_expires_at,
            persistence=persistence,
            persistence_issue=issue,
        )

    if task.status == "failed":
        return VodEnhancementTaskOutput(
            task_id=task.task_id,
            status="failed",
            provider_status=task.provider_status,
            request_id=task.request_id,
            created_at=task.created_at,
            finished_at=task.finished_at,
            persistence="not_applicable",
            error=VodEnhancementTaskFailure(
                code=task.failure_code,
                message=task.failure_message or "MediaKit reported the enhancement task failed.",
            ),
        )

    return VodEnhancementTaskOutput(
        task_id=task.task_id,
        status="processing",
        provider_status=task.provider_status,
        request_id=task.request_id,
        created_at=task.created_at,
        persistence="not_applicable",
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
