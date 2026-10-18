"""``omnihuman_get_task`` tool — poll an OmniHuman 2.0 task and persist its video.

On the first successful poll of a completed task, copies the one-hour provider
video URL into ``ArtifactStore`` and caches the artifact by task ID so repeated
polls do not download twice.
"""

from __future__ import annotations

from typing import Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, model_validator

from ark_mcp.artifacts.store import ArtifactPersistenceError
from ark_mcp.domain.artifacts import ArtifactPersistenceIssue, ArtifactRef, MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.providers.vision_cv.omnihuman import OmniHumanService
from ark_mcp.providers.vision_cv.schemas import OmniHumanTaskResult, OmniHumanTaskStatus
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
from ark_mcp.tools._omnihuman_shared import OUTPUT_HOST_SUFFIXES
from ark_mcp.tools._task_execution import context_log, persistence_requires_task

Persistence = Literal["not_applicable", "not_requested", "persisted", "failed"]


class OmniHumanGetTaskInput(BaseModel):
    """Input for ``omnihuman_get_task``."""

    task_id: str = Field(min_length=1, description="Task ID returned by omnihuman_create_task.")
    persist_output: bool = Field(
        default=True,
        description=(
            "Copy a completed video into artifact storage. true requires background execution "
            "(ark_job_submit); use false for a foreground status check."
        ),
    )
    output_path: str | None = output_path_field("the generated video")
    overwrite: bool = overwrite_field()


class OmniHumanTaskFailure(BaseModel):
    """Provider failure detail for a failed OmniHuman task."""

    code: str | None = Field(default=None, description="Provider failure code, when returned.")
    message: str = Field(description="Provider failure explanation.")


class OmniHumanTaskOutput(BaseModel):
    """Normalized status and output of an OmniHuman 2.0 task."""

    provider: Literal["byteplus-vision-cv"] = Field(
        default="byteplus-vision-cv", description="Provider surface."
    )
    task_id: str = Field(description="Task ID.")
    status: OmniHumanTaskStatus = Field(
        description=(
            "queued, running, succeeded (video ready), failed (see error), canceled, "
            "expired (provider dropped the task; resubmit), or not_found (unknown or older "
            "than 12 h)."
        )
    )
    request_id: str | None = Field(
        default=None, description="Provider diagnostic request ID, when returned."
    )
    video: ArtifactRef | None = Field(
        default=None,
        description="Durable video artifact when persistence succeeded.",
    )
    source_url: str | None = Field(
        default=None,
        description="Provider video URL for a succeeded task; valid about one hour.",
    )
    source_expires_at: str | None = Field(
        default=None,
        description="ISO-8601 estimate of when source_url expires (first poll plus one hour).",
    )
    persistence: Persistence = Field(description="Artifact persistence outcome.")
    persistence_issue: ArtifactPersistenceIssue | None = Field(
        default=None,
        description="Why a succeeded video could not be persisted.",
    )
    error: OmniHumanTaskFailure | None = Field(
        default=None, description="Provider failure detail for a failed task."
    )

    @model_validator(mode="after")
    def _validate_state(self) -> OmniHumanTaskOutput:
        if self.status == "succeeded":
            if self.source_url is None:
                raise ValueError("succeeded output requires source_url")
            if self.persistence == "not_applicable":
                raise ValueError("succeeded output must not use not_applicable persistence")
            if self.persistence == "persisted" and self.video is None:
                raise ValueError("persisted output requires video")
            if self.persistence == "failed" and self.persistence_issue is None:
                raise ValueError("failed persistence requires a persistence issue")
            if self.error is not None:
                raise ValueError("succeeded output must not carry a provider failure")
        else:
            if self.source_url is not None or self.video is not None:
                raise ValueError("only a succeeded task carries output")
            if self.persistence != "not_applicable":
                raise ValueError("non-succeeded output must set persistence=not_applicable")
            if (self.status == "failed") != (self.error is not None):
                raise ValueError("error is required for, and only for, a failed task")
        return self


async def _persist_video(
    ctx: Context,
    owner: PrincipalContext,
    task_id: str,
    video_url: str,
    expires_at: str | None,
) -> tuple[ArtifactRef | None, ArtifactPersistenceIssue | None, Persistence]:
    runtime = get_runtime(ctx)
    async with runtime.task_artifact_locks.acquire("vision-cv", task_id):
        cache = await runtime.task_artifact_cache.get("vision-cv", task_id)
        if cache and cache.get("video") is not None:
            return cache["video"], None, "persisted"
        try:
            video_ref = await runtime.artifact_store.copy_from_trusted_url(
                url=video_url,
                media_type=MediaType.VIDEO,
                mime_type="video/mp4",
                source_expires_at=expires_at,
                auth=owner,
                extra_trusted_host_suffixes=OUTPUT_HOST_SUFFIXES,
            )
        except ArtifactPersistenceError as exc:
            issue = ArtifactPersistenceIssue(
                code=exc.code,
                message=exc.safe_message,
                retryable=exc.retryable,
                artifact_limit_bytes=get_media_limits().video_max_bytes,
                source_url_expires_at=expires_at,
            )
            await context_log(
                ctx, "warning", f"OmniHuman output persistence failed: {exc.safe_message}"
            )
            return None, issue, "failed"
        except Exception:
            issue = ArtifactPersistenceIssue(
                code="storage_failed",
                message="Provider output could not be written to artifact storage.",
                retryable=True,
                artifact_limit_bytes=get_media_limits().video_max_bytes,
                source_url_expires_at=expires_at,
            )
            await context_log(
                ctx, "warning", "OmniHuman output persistence failed due to an internal error."
            )
            return None, issue, "failed"
        await runtime.task_artifact_cache.set("vision-cv", task_id, {"video": video_ref})
        return video_ref, None, "persisted"


def _succeeded_output(
    task_id: str,
    task: OmniHumanTaskResult,
    video: ArtifactRef | None,
    issue: ArtifactPersistenceIssue | None,
    persistence: Persistence,
) -> OmniHumanTaskOutput:
    return OmniHumanTaskOutput(
        task_id=task_id,
        status="succeeded",
        request_id=task.request_id,
        video=video,
        source_url=task.video_url,
        source_expires_at=(
            task.video_url_expires_at.isoformat() if task.video_url_expires_at else None
        ),
        persistence=persistence,
        persistence_issue=issue,
    )


@local_export("video", precondition=_needs_persist_output)
async def omnihuman_get_task(
    input: OmniHumanGetTaskInput, ctx: Context
) -> OmniHumanTaskOutput | ToolResult:
    """Poll an OmniHuman 2.0 task and retrieve the generated video.

    Keep polling while status is queued or running. With persist_output=true the
    finished video is copied to a durable artifact on the first successful poll
    (background execution, ark_job_submit); later polls return the cached artifact.
    """
    task_error = persistence_requires_task(ctx, input.persist_output)
    if task_error is not None:
        return task_error
    runtime = get_runtime(ctx)
    owner = get_principal(ctx)
    await runtime.ownership_store.require_owner("vision-cv", input.task_id, owner)
    await context_log(ctx, "info", f"Retrieving OmniHuman task {input.task_id}")
    await ctx.report_progress(progress=20, total=100)

    service = OmniHumanService()
    try:
        task = await call_with_retry(lambda: service.get(input.task_id))
    except ProviderError as exc:
        await context_log(ctx, "error", f"Failed to retrieve OmniHuman task: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()
    await ctx.report_progress(progress=60, total=100)

    log_info("omnihuman_get_task_complete", task_id=input.task_id, status=task.status)
    if task.status == "succeeded" and task.video_url is not None:
        expires_at = task.video_url_expires_at.isoformat() if task.video_url_expires_at else None
        if not input.persist_output:
            return _succeeded_output(input.task_id, task, None, None, "not_requested")
        video, issue, persistence = await _persist_video(
            ctx, owner, input.task_id, task.video_url, expires_at
        )
        return _succeeded_output(input.task_id, task, video, issue, persistence)

    error = (
        OmniHumanTaskFailure(
            code=task.error_code,
            message=task.error_message or "OmniHuman reported that generation failed.",
        )
        if task.status == "failed"
        else None
    )
    return OmniHumanTaskOutput(
        task_id=input.task_id,
        status=task.status,
        request_id=task.request_id,
        persistence="not_applicable",
        error=error,
    )


# Not read-only: output_path can write local files.
TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
