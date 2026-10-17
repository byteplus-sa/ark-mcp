"""MCP tool for MediaKit precision subtitle and text erasure."""

from __future__ import annotations

from typing import Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, model_validator

from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.media import HttpsUrl
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.providers.vod_mediakit.schemas import (
    VodMediaKitEraseLocation,
    VodMediaKitRemoveSubtitlesRequest,
    VodMediaKitSubtitleFilter,
    VodMediaKitTimeSegmentFilter,
)
from ark_mcp.providers.vod_mediakit.subtitles import VodMediaKitSubtitleRemovalService
from ark_mcp.runtime import get_principal, get_runtime
from ark_mcp.security.url_policy import UrlValidationError, validate_url
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._task_execution import context_log
from ark_mcp.tools._vod_shared import VOD_OPERATION_DESCRIPTION, VodOperation


class VodRemoveSubtitlesInput(BaseModel):
    """Input for a MediaKit precision-erasure task."""

    video_url: HttpsUrl = Field(
        description="Public HTTPS source-video URL that MediaKit can fetch; private and link-local destinations are rejected."
    )
    mode: Literal["subtitle", "text"] = Field(
        default="subtitle",
        description="subtitle removes recognized dialogue subtitles; text removes recognized on-screen text across the frame and can also affect titles, labels, and watermarks.",
    )
    output_encode_mode: Literal["quality", "size"] = Field(
        default="quality",
        description="Output encoding priority: quality uses higher bitrate; size stays closer to the source bitrate.",
    )
    erase_ratio_location: list[VodMediaKitEraseLocation] | None = Field(
        default=None,
        min_length=1,
        max_length=20,
        description="Optional one-to-twenty normalized rectangular regions limiting where text is erased.",
    )
    time_segment_filter: VodMediaKitTimeSegmentFilter | None = Field(
        default=None,
        description="Optional selected or skipped time segments controlling when erasure runs.",
    )
    subtitle_filter: VodMediaKitSubtitleFilter | None = Field(
        default=None,
        description="Optional OCR subtitle-size and horizontal-centering thresholds; applies only in subtitle mode.",
    )
    client_token: str | None = Field(
        default=None,
        pattern=r"^[\x20-\x7E]{1,64}$",
        description="Optional case-sensitive idempotency token of at most 64 printable ASCII characters. Reuse it when reconciling an ambiguous submission.",
    )
    callback_args: str | None = Field(
        default=None,
        description="Optional callback payload returned unchanged by MediaKit; maximum 512 UTF-8 bytes.",
    )
    callback_url: HttpsUrl | None = Field(
        default=None,
        description="Optional public HTTPS task-specific event callback URL; overrides the console callback.",
    )
    queue_id: str | None = Field(
        default=None,
        min_length=1,
        description="Optional MediaKit queue ID; omission uses the default queue for the API key's project.",
    )
    model_version: Literal["v4", "v5"] | None = Field(
        default=None,
        description="Optional legacy precision-erasure model version accepted by the convenience endpoint.",
    )
    project: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Optional legacy MediaKit project label serialized upstream as case-sensitive Project; omit for the API key's configured project.",
    )

    @model_validator(mode="after")
    def validate_callback_args(self) -> VodRemoveSubtitlesInput:
        if self.callback_args is not None and len(self.callback_args.encode("utf-8")) > 512:
            raise ValueError("callback_args must not exceed 512 bytes")
        return self


class VodRemoveSubtitlesOutput(BaseModel):
    """Accepted asynchronous precision-erasure submission."""

    provider: Literal["byteplus-vod-mediakit"] = Field(
        default="byteplus-vod-mediakit",
        description="Provider surface that accepted the request.",
    )
    status: Literal["accepted"] = Field(
        description="Always accepted because precision erasure runs asynchronously."
    )
    request_id: str | None = Field(
        default=None, description="Provider diagnostic request ID, when returned."
    )
    provider_log_id: str | None = Field(
        default=None, description="Provider x-tt-logid diagnostic identifier, when returned."
    )
    task_id: str = Field(
        description="Provider task ID to pass to vod_get_task (operation 'remove_subtitles')."
    )
    operation: VodOperation = Field(
        default="remove_subtitles", description=VOD_OPERATION_DESCRIPTION
    )
    recommended_poll_after_ms: int = Field(
        description="Suggested initial polling delay in milliseconds; a heuristic, not a provider guarantee."
    )


async def vod_remove_subtitles(
    input: VodRemoveSubtitlesInput, ctx: Context
) -> VodRemoveSubtitlesOutput | ToolResult:
    """Remove hardcoded subtitles or recognized on-screen text with MediaKit.

    The text mode has a broader visual effect than subtitle mode and may erase
    titles, labels, or watermarks. Returns a task ID; poll it with
    vod_get_task using the returned task_id and operation ('remove_subtitles').
    The request is not automatically retried after ambiguous failures; reuse
    client_token when reconciling a timeout. Long-running work runs through
    ark_job_submit (or native MCP task augmentation).
    """
    runtime = get_runtime(ctx)
    if not runtime.settings.has_vod_mediakit:
        raise ValueError(
            "BYTEPLUS_VOD_MEDIAKIT_API_KEY is not configured. Set it to enable this tool."
        )

    try:
        video_url = validate_url(str(input.video_url)).url
        callback_url = (
            validate_url(str(input.callback_url)).url if input.callback_url is not None else None
        )
    except UrlValidationError as exc:
        log_warning("vod_remove_subtitles_invalid_url", error=str(exc))
        return ToolResult(
            content=[{"type": "text", "text": UrlValidationError.safe_message}],
            is_error=True,
        )

    request = VodMediaKitRemoveSubtitlesRequest.model_validate(
        {
            "video_url": video_url,
            "mode": "Subtitle" if input.mode == "subtitle" else "Text",
            "output_encode_mode": ("Quality" if input.output_encode_mode == "quality" else "Size"),
            "erase_ratio_location": input.erase_ratio_location,
            "time_segment_filter": input.time_segment_filter,
            "subtitle_filter": input.subtitle_filter,
            "client_token": input.client_token,
            "callback_args": input.callback_args,
            "callback_url": callback_url,
            "queue_id": input.queue_id,
            "model_version": input.model_version,
            "project": input.project,
        }
    )
    owner = get_principal(ctx)
    service = VodMediaKitSubtitleRemovalService()
    await context_log(ctx, "info", "Starting VOD AI MediaKit subtitle removal")
    await ctx.report_progress(progress=20, total=100)
    try:
        async with runtime.provider_limiters.acquire("vod-mediakit", owner):
            submission = await service.submit(request)
    except ProviderError as exc:
        await context_log(
            ctx, "error", f"VOD AI MediaKit subtitle removal submission failed: {exc.message}"
        )
        return provider_error_result(exc)
    finally:
        await service.close()

    await runtime.ownership_store.record("vod-mediakit", submission.task_id, owner)
    await ctx.report_progress(progress=100, total=100)
    return VodRemoveSubtitlesOutput(
        status="accepted",
        request_id=submission.request_id,
        provider_log_id=submission.provider_log_id,
        task_id=submission.task_id,
        recommended_poll_after_ms=15000,
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
