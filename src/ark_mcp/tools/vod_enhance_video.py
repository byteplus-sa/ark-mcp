"""MCP tool for the BytePlus VOD AI MediaKit enhancement profile.

The upstream success schema remains provisional and is isolated in the
provider adapter; this tool exposes a stable, bounded persistence outcome.
"""

from __future__ import annotations

from typing import Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, model_validator

from ark_mcp.artifacts.store import ArtifactPersistenceError
from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.media import HttpsUrl
from ark_mcp.observability.logger import info as log_info
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.providers.vod_mediakit.enhancement import VodMediaKitEnhancementService
from ark_mcp.providers.vod_mediakit.schemas import (
    FULL_RESOLUTION_LIMIT_MAX,
    EnhancementResolution,
    EnhancementTier,
    VodMediaKitEnhancementRequest,
    validate_enhancement_profile,
)
from ark_mcp.runtime import get_principal, get_runtime
from ark_mcp.security.media_policy import get_media_limits
from ark_mcp.security.url_policy import UrlValidationError, validate_url
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._task_execution import context_log
from ark_mcp.tools._vod_shared import (
    VOD_OPERATION_DESCRIPTION,
    VodArtifactPersistenceIssue,
    VodOperation,
)


class VodEnhanceVideoInput(BaseModel):
    """Input for fast, standard, or professional MediaKit video enhancement."""

    video_url: HttpsUrl = Field(
        description="Public HTTPS source-video URL; private and link-local hosts are rejected."
    )
    tool_version: EnhancementTier = Field(
        default="professional",
        description=(
            "'fast': lightweight, fastest (input up to 2K). 'standard': balanced, honors 'scene'. "
            "'professional': highest quality, slower and costlier."
        ),
    )
    scene: Literal["common", "ugc", "short_series", "aigc", "old_film"] = Field(
        default="common",
        description="Scenario preset; only used when tool_version is 'standard'.",
    )
    enhance_style: Literal["hd", "natural"] | None = Field(
        default=None,
        description="'hd' (provider default) is sharper; 'natural' reduces sharpening artifacts. Not supported by 'fast'.",
    )
    resolution: EnhancementResolution | None = Field(
        default=None,
        description=(
            "Target resolution level; 'fast' tops out at 4k. Mutually exclusive with "
            "resolution_limit. Defaults to '4k' when neither is set."
        ),
    )
    resolution_limit: int | None = Field(
        default=None,
        ge=128,
        le=FULL_RESOLUTION_LIMIT_MAX,
        description=(
            "Target short-side pixels, aspect ratio preserved; max 2160 for 'fast'. Mutually "
            "exclusive with resolution."
        ),
    )
    bitrate_level: Literal["low", "medium", "high"] = Field(
        default="high",
        description="Target bitrate tier; ignored when bitrate is set.",
    )
    bitrate: int | None = Field(
        default=None,
        ge=10,
        le=150000,
        description="Exact average bitrate in kbps; overrides bitrate_level.",
    )
    fps: float | None = Field(
        default=24,
        ge=15,
        le=120,
        description="Target frames per second; above the source rate interpolates (keep within 4x). Null keeps the source rate.",
    )
    project: str = Field(
        default="default",
        min_length=1,
        max_length=128,
        description="MediaKit project label (case-sensitive).",
    )
    input_duration_seconds: float | None = Field(
        default=None,
        gt=0,
        description="Source duration in seconds; currently unused (reserved for pricing).",
    )
    persist: bool = Field(
        default=True,
        description="Copy a directly returned completed output into the durable artifact store (best effort).",
    )

    @model_validator(mode="after")
    def validate_profile(self) -> VodEnhanceVideoInput:
        """Reject combinations the selected enhancement tier does not support."""
        validate_enhancement_profile(
            tool_version=self.tool_version,
            resolution=self.resolution,
            resolution_limit=self.resolution_limit,
            enhance_style=self.enhance_style,
        )
        return self


class VodEnhanceVideoOutput(BaseModel):
    """Normalized successful MediaKit enhancement result."""

    provider: Literal["byteplus-vod-mediakit"] = Field(
        default="byteplus-vod-mediakit", description="Provider surface."
    )
    status: Literal["accepted", "succeeded"] = Field(
        description="accepted: task queued, poll vod_get_task; succeeded: output returned directly.",
    )
    request_id: str | None = Field(default=None, description="Provider request ID, when returned.")
    provider_log_id: str | None = Field(
        default=None, description="Provider x-tt-logid, when returned."
    )
    task_id: str | None = Field(
        default=None,
        description="Task ID to poll with vod_get_task (operation 'enhance').",
    )
    operation: VodOperation = Field(default="enhance", description=VOD_OPERATION_DESCRIPTION)
    provider_status: str | None = Field(
        default=None,
        description="Raw provider status label, when returned.",
    )
    video: ArtifactRef | None = Field(
        default=None,
        description="Durable enhanced-video artifact, when persisted.",
    )
    source_url: HttpsUrl | None = Field(
        default=None,
        description="Expiring provider output URL; absent while a task is still processing.",
    )
    source_expires_at: str | None = Field(
        default=None, description="ISO-8601 expiry of source_url, when returned."
    )
    output_size_bytes: int | None = Field(
        default=None, ge=0, description="Output size in bytes, when returned."
    )
    persistence: Literal["not_applicable", "not_requested", "persisted", "failed"] = Field(
        description="Durable persistence outcome, independent of provider success."
    )
    persistence_issue: VodArtifactPersistenceIssue | None = Field(
        default=None, description="Safe explanation when persistence failed."
    )
    estimated_cost_usd: float | None = Field(
        default=None,
        description="Always null; cost estimation is not available.",
    )


async def vod_enhance_video(
    input: VodEnhanceVideoInput, ctx: Context
) -> VodEnhanceVideoOutput | ToolResult:
    """Upscale and enhance a public video with BytePlus VOD AI MediaKit.

    Defaults to professional tier, 4K, 24 fps. Not retried automatically. An
    accepted result carries a task_id: poll it with vod_get_task (operation
    'enhance'). Requires background execution (ark_job_submit).
    """
    runtime = get_runtime(ctx)
    settings = runtime.settings
    if not settings.has_vod_mediakit:
        raise ValueError(
            "BYTEPLUS_VOD_MEDIAKIT_API_KEY is not configured. Set it to enable this tool."
        )

    try:
        validated_source = validate_url(str(input.video_url))
    except UrlValidationError as exc:
        log_warning("vod_enhance_video_invalid_source_url", error=str(exc))
        return ToolResult(
            content=[{"type": "text", "text": UrlValidationError.safe_message}],
            is_error=True,
        )
    request = VodMediaKitEnhancementRequest.model_validate(
        {
            "video_url": validated_source.url,
            "scene": input.scene,
            "tool_version": input.tool_version,
            "enhance_style": input.enhance_style,
            "resolution": input.resolution,
            "resolution_limit": input.resolution_limit,
            "bitrate_level": input.bitrate_level,
            "bitrate": input.bitrate,
            "fps": input.fps,
            "project": input.project,
        }
    )
    owner = get_principal(ctx)
    service = VodMediaKitEnhancementService()
    await context_log(ctx, "info", "Starting VOD AI MediaKit enhancement")
    await ctx.report_progress(progress=20, total=100)
    try:
        async with runtime.provider_limiters.acquire("vod-mediakit", owner):
            submission = await service.enhance(request)
    except ProviderError as exc:
        await context_log(ctx, "error", f"VOD AI MediaKit enhancement failed: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    await ctx.report_progress(progress=75, total=100)
    if submission.status == "accepted":
        task_id = submission.task_id
        if task_id is None:
            raise RuntimeError("accepted MediaKit submission is missing task_id")
        await runtime.ownership_store.record("vod-mediakit", task_id, owner)
        await ctx.report_progress(progress=100, total=100)
        return VodEnhanceVideoOutput(
            status="accepted",
            request_id=submission.request_id,
            provider_log_id=submission.provider_log_id,
            task_id=task_id,
            provider_status=submission.provider_status,
            persistence="not_applicable",
        )

    output_url = submission.output_url
    if output_url is None:
        raise RuntimeError("succeeded MediaKit submission is missing output_url")
    artifact: ArtifactRef | None = None
    issue: VodArtifactPersistenceIssue | None = None
    persistence: Literal["not_requested", "persisted", "failed"] = "not_requested"
    if input.persist:
        try:
            artifact = await runtime.artifact_store.copy_from_trusted_url(
                url=str(output_url),
                media_type=MediaType.VIDEO,
                mime_type=submission.mime_type or "video/mp4",
                source_expires_at=submission.expires_at,
                auth=owner,
            )
            persistence = "persisted"
        except ArtifactPersistenceError as exc:
            persistence = "failed"
            issue = VodArtifactPersistenceIssue(
                code=exc.code,
                message=exc.safe_message,
                retryable=exc.retryable,
                artifact_limit_bytes=get_media_limits().video_max_bytes,
            )
            await context_log(ctx, "warning", f"VOD output persistence failed: {exc.safe_message}")
        except Exception:
            # Provider success may already be billable. Preserve its source URL even
            # when a future/custom artifact backend violates the typed error contract.
            persistence = "failed"
            issue = VodArtifactPersistenceIssue(
                code="storage_failed",
                message="Provider output could not be written to artifact storage.",
                retryable=True,
                artifact_limit_bytes=get_media_limits().video_max_bytes,
            )
            await context_log(
                ctx, "warning", "VOD output persistence failed due to an internal storage error."
            )

    await ctx.report_progress(progress=100, total=100)
    log_info(
        "vod_enhance_video_complete",
        status=submission.status,
        task_id=submission.task_id,
        persistence=persistence,
    )
    return VodEnhanceVideoOutput(
        status="succeeded",
        request_id=submission.request_id,
        provider_log_id=submission.provider_log_id,
        task_id=submission.task_id,
        provider_status=submission.provider_status,
        video=artifact,
        source_url=output_url,
        source_expires_at=submission.expires_at,
        output_size_bytes=submission.output_size_bytes,
        persistence=persistence,
        persistence_issue=issue,
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
