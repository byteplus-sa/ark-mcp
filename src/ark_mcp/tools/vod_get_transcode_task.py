"""``vod_get_transcode_task`` tool — poll a BytePlus VOD AI MediaKit transcode task.

On the first successful poll of a completed task, copies the expiring provider
output URL into ``ArtifactStore`` and caches the artifact by task ID so repeated
polls do not download twice.
"""

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
from ark_mcp.providers.vod_mediakit.schemas import TranscodeTask
from ark_mcp.providers.vod_mediakit.transcode import VodMediaKitTranscodeService
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


class VodGetTranscodeTaskInput(BaseModel):
    """Input for ``vod_get_transcode_task``."""

    task_id: str = Field(
        description="Provider task ID from the task-augmented result of vod_transcode_video."
    )
    persist_output: bool = Field(
        default=True,
        description=(
            "Whether to copy a completed output into durable artifact storage on first successful poll. "
            "The default true requires task-augmented execution; use false for a foreground status check."
        ),
    )
    output_path: str | None = output_path_field("the transcoded video")
    overwrite: bool = overwrite_field()


class VodTranscodeTaskFailure(BaseModel):
    """Safe provider failure detail for a failed transcode task."""

    code: str | None = Field(default=None, description="Provider failure code, when returned.")
    message: str = Field(
        description="Safe provider failure explanation without credentials or signed URLs."
    )


class VodTranscodeTaskOutput(BaseModel):
    """Normalized status and output of a BytePlus VOD AI MediaKit transcode task."""

    provider: Literal["byteplus-vod-mediakit"] = Field(
        default="byteplus-vod-mediakit", description="Provider surface that processed the request."
    )
    task_id: str = Field(description="Provider task ID.")
    status: Literal["processing", "succeeded", "failed"] = Field(
        description="Normalized transcode state: processing until the provider reports completed, then succeeded or failed. queued/expired/cancelled are not documented by the provider.",
    )
    provider_status: str | None = Field(
        default=None, description="Raw provider status label, when returned."
    )
    request_id: str | None = Field(
        default=None, description="Provider diagnostic request ID, when returned."
    )
    duration_seconds: float | None = Field(
        default=None,
        ge=0,
        description="Output duration in seconds, when reported by the completed result.",
    )
    resolution: str | None = Field(
        default=None, description="Output resolution label (e.g. '720p'), when reported."
    )
    video_codec: str | None = Field(
        default=None, description="Output video codec label (e.g. 'h264'), when reported."
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
        default=None,
        description="Durable transcoded-video artifact when best-effort persistence succeeds.",
    )
    source_url: HttpsUrl | None = Field(
        default=None,
        description="Expiring provider output URL for a succeeded task; preserved even when durable persistence is skipped or fails.",
    )
    source_expires_at: str | None = Field(
        default=None,
        description="ISO-8601 expiry for source_url, normalized from the provider's expires_at.",
    )
    persistence: Literal["not_applicable", "not_requested", "persisted", "failed"] = Field(
        description="Outcome of durable artifact persistence, independent of provider success."
    )
    persistence_issue: VodArtifactPersistenceIssue | None = Field(
        default=None,
        description="Safe explanation when a succeeded output could not be persisted durably.",
    )
    error: VodTranscodeTaskFailure | None = Field(
        default=None, description="Provider failure code and safe message for a failed task."
    )

    @model_validator(mode="after")
    def _validate_state(self) -> VodTranscodeTaskOutput:
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
    task: TranscodeTask,
    persist_output: bool,
) -> tuple[
    ArtifactRef | None,
    VodArtifactPersistenceIssue | None,
    Literal["not_requested", "persisted", "failed"],
]:
    """Best-effort copy of a completed output into the artifact store."""
    output_url = task.output_url
    if output_url is None or not persist_output:
        return None, None, "not_requested"

    runtime = get_runtime(ctx)
    cache = await runtime.task_artifact_cache.get("vod-mediakit", task_id)
    if cache:
        video_ref = cache.get("video")
        if video_ref is not None:
            return video_ref, None, "persisted"

    try:
        video_ref = await runtime.artifact_store.copy_from_trusted_url(
            url=str(output_url),
            media_type=MediaType.VIDEO,
            mime_type="video/mp4",
            source_expires_at=task.source_expires_at,
            auth=owner,
        )
    except ArtifactPersistenceError as exc:
        issue = VodArtifactPersistenceIssue(
            code=exc.code,
            message=exc.safe_message,
            retryable=exc.retryable,
            artifact_limit_bytes=get_media_limits().video_max_bytes,
        )
        await context_log(
            ctx, "warning", f"VOD transcode output persistence failed: {exc.safe_message}"
        )
        return None, issue, "failed"
    except Exception:
        issue = VodArtifactPersistenceIssue(
            code="storage_failed",
            message="Provider output could not be written to artifact storage.",
            retryable=True,
            artifact_limit_bytes=get_media_limits().video_max_bytes,
        )
        await context_log(
            ctx,
            "warning",
            "VOD transcode output persistence failed due to an internal storage error.",
        )
        return None, issue, "failed"

    await runtime.task_artifact_cache.set("vod-mediakit", task_id, {"video": video_ref})
    return video_ref, None, "persisted"


@local_export("video", precondition=_needs_persist_output)
async def vod_get_transcode_task(
    input: VodGetTranscodeTaskInput, ctx: Context
) -> VodTranscodeTaskOutput | ToolResult:
    """Poll the status and output of a BytePlus VOD AI MediaKit transcode task.

    On the first successful poll with ``persist_output=True``, copies the
    expiring provider output URL into durable artifact storage. Subsequent
    calls return the cached artifact reference without re-downloading. Supports
    optional MCP task-augmented execution for completed-output persistence.
    """
    task_error = persistence_requires_task(ctx, input.persist_output)
    if task_error is not None:
        return task_error
    await context_log(ctx, "info", f"Retrieving VOD AI MediaKit transcode task {input.task_id}")
    await ctx.report_progress(progress=20, total=100)
    runtime = get_runtime(ctx)
    owner = get_principal(ctx)
    await runtime.ownership_store.require_owner("vod-mediakit", input.task_id, owner)

    service = VodMediaKitTranscodeService()
    try:
        task = await call_with_retry(lambda: service.get(input.task_id))
    except ProviderError as exc:
        await context_log(ctx, "error", f"Failed to retrieve transcode task: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    await ctx.report_progress(progress=60, total=100)

    if task.status == "succeeded":
        video_ref, issue, persistence = await _persist_output(
            ctx, owner, input.task_id, task, input.persist_output
        )
        log_info(
            "vod_get_transcode_task_complete",
            task_id=task.task_id,
            status="succeeded",
            persistence=persistence,
        )
        return VodTranscodeTaskOutput(
            provider="byteplus-vod-mediakit",
            task_id=task.task_id,
            status="succeeded",
            provider_status=task.provider_status,
            request_id=task.request_id,
            duration_seconds=task.duration_seconds,
            resolution=task.resolution,
            video_codec=task.video_codec,
            created_at=task.created_at,
            finished_at=task.finished_at,
            video=video_ref,
            source_url=task.output_url,
            source_expires_at=task.source_expires_at,
            persistence=persistence,
            persistence_issue=issue,
        )

    if task.status == "failed":
        log_info(
            "vod_get_transcode_task_complete",
            task_id=task.task_id,
            status="failed",
            failure_code=task.failure_code,
        )
        return VodTranscodeTaskOutput(
            provider="byteplus-vod-mediakit",
            task_id=task.task_id,
            status="failed",
            provider_status=task.provider_status,
            request_id=task.request_id,
            created_at=task.created_at,
            finished_at=task.finished_at,
            persistence="not_applicable",
            error=VodTranscodeTaskFailure(
                code=task.failure_code,
                message=task.failure_message or "MediaKit reported the transcode task failed.",
            ),
        )

    log_info(
        "vod_get_transcode_task_complete",
        task_id=task.task_id,
        status="processing",
    )
    return VodTranscodeTaskOutput(
        provider="byteplus-vod-mediakit",
        task_id=task.task_id,
        status="processing",
        provider_status=task.provider_status,
        request_id=task.request_id,
        created_at=task.created_at,
        persistence="not_applicable",
    )


# Tool annotation constants — camelCase per MCP specification.
# Not read-only: output_path/output_dir can write local files.
TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
