"""MCP tool for MediaKit subtitle burn-in submission."""

from __future__ import annotations

from typing import Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, model_validator

from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.media import HttpsUrl
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.providers.vod_mediakit.schemas import (
    VodMediaKitAddSubtitlesRequest,
    VodMediaKitSubtitleCue,
)
from ark_mcp.providers.vod_mediakit.subtitles import VodMediaKitSubtitleBurnInService
from ark_mcp.runtime import get_principal, get_runtime
from ark_mcp.security.url_policy import UrlValidationError, validate_url
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._task_execution import context_log
from ark_mcp.tools._vod_shared import VOD_OPERATION_DESCRIPTION, VodOperation

SubtitleFont = Literal[
    "inter",
    "montserrat",
    "oppo_sans",
    "roboto",
    "source_han_serif",
    "sy_black",
    "pm_zhengdao",
    "zhanku_kuaile",
]


class VodAddSubtitlesInput(BaseModel):
    """Input for a subtitle burn-in task."""

    video_url: HttpsUrl = Field(
        description="Public HTTPS video URL that MediaKit can fetch; private and link-local destinations are rejected."
    )
    subtitle_url: HttpsUrl | None = Field(
        default=None,
        description="Optional public HTTPS SRT, VTT, or ASS subtitle-file URL. At least one of subtitle_url or subtitles is required; subtitle_url takes priority when both are supplied.",
    )
    subtitles: list[VodMediaKitSubtitleCue] | None = Field(
        default=None,
        min_length=1,
        description="Optional non-empty inline subtitle cue list. At least one of subtitle_url or subtitles is required.",
    )
    subtitle_pos_preset: Literal["bottom_center", "top_center", "center", "lower_third"] = Field(
        default="bottom_center",
        description="Subtitle position preset: bottom_center, top_center, center, or lower_third.",
    )
    subtitle_font_size: int = Field(
        default=50,
        gt=0,
        description="Positive subtitle font size in pixels; MediaKit defaults to 50.",
    )
    subtitle_font_color: str = Field(
        default="#FFFFFFFF",
        pattern=r"^#[0-9A-Fa-f]{8}$",
        description="Subtitle color and opacity as #RRGGBBAA; defaults to opaque white.",
    )
    subtitle_font_type: SubtitleFont = Field(
        default="inter",
        description="MediaKit font identifier. Current documented values include inter, montserrat, oppo_sans, roboto, source_han_serif, sy_black, pm_zhengdao, and zhanku_kuaile.",
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
    project: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Optional legacy MediaKit project label serialized upstream as case-sensitive Project; omit for the API key's configured project.",
    )

    @model_validator(mode="after")
    def validate_content_and_callback(self) -> VodAddSubtitlesInput:
        if self.subtitle_url is None and not self.subtitles:
            raise ValueError("subtitle_url or subtitles is required")
        if self.callback_args is not None and len(self.callback_args.encode("utf-8")) > 512:
            raise ValueError("callback_args must not exceed 512 bytes")
        return self


class VodAddSubtitlesOutput(BaseModel):
    """Accepted asynchronous subtitle burn-in submission."""

    provider: Literal["byteplus-vod-mediakit"] = Field(
        default="byteplus-vod-mediakit",
        description="Provider surface that accepted the request.",
    )
    status: Literal["accepted"] = Field(
        description="Always accepted because subtitle burn-in runs asynchronously."
    )
    request_id: str | None = Field(
        default=None, description="Provider diagnostic request ID, when returned."
    )
    provider_log_id: str | None = Field(
        default=None, description="Provider x-tt-logid diagnostic identifier, when returned."
    )
    task_id: str = Field(
        description="Provider task ID to pass to vod_get_task (operation 'add_subtitles')."
    )
    operation: VodOperation = Field(default="add_subtitles", description=VOD_OPERATION_DESCRIPTION)
    recommended_poll_after_ms: int = Field(
        description="Suggested initial polling delay in milliseconds; a heuristic, not a provider guarantee."
    )


def _validate_public_url(value: HttpsUrl, event: str) -> str:
    try:
        return validate_url(str(value)).url
    except UrlValidationError as exc:
        log_warning(event, error=str(exc))
        raise


async def vod_add_subtitles(
    input: VodAddSubtitlesInput, ctx: Context
) -> VodAddSubtitlesOutput | ToolResult:
    """Burn subtitle cues or an SRT/VTT/ASS file into a video with MediaKit.

    Returns an asynchronous task ID; poll it with vod_get_task using the
    returned task_id and operation ('add_subtitles'). When both subtitle_url
    and subtitles are supplied, MediaKit uses subtitle_url. The request is not
    automatically retried after ambiguous failures; supply and reuse
    client_token when reconciling a timed-out submission. Long-running work
    runs through ark_job_submit (or native MCP task augmentation).
    """
    runtime = get_runtime(ctx)
    if not runtime.settings.has_vod_mediakit:
        raise ValueError(
            "BYTEPLUS_VOD_MEDIAKIT_API_KEY is not configured. Set it to enable this tool."
        )

    try:
        video_url = _validate_public_url(input.video_url, "vod_add_subtitles_invalid_video_url")
        subtitle_url = (
            _validate_public_url(input.subtitle_url, "vod_add_subtitles_invalid_subtitle_url")
            if input.subtitle_url is not None
            else None
        )
        callback_url = (
            _validate_public_url(input.callback_url, "vod_add_subtitles_invalid_callback_url")
            if input.callback_url is not None
            else None
        )
    except UrlValidationError:
        return ToolResult(
            content=[{"type": "text", "text": UrlValidationError.safe_message}],
            is_error=True,
        )

    request = VodMediaKitAddSubtitlesRequest.model_validate(
        {
            "video_url": video_url,
            "subtitle_url": subtitle_url,
            "subtitles": input.subtitles,
            "subtitle_pos_preset": input.subtitle_pos_preset,
            "subtitle_font_size": input.subtitle_font_size,
            "subtitle_font_color": input.subtitle_font_color,
            "subtitle_font_type": input.subtitle_font_type,
            "client_token": input.client_token,
            "callback_args": input.callback_args,
            "callback_url": callback_url,
            "queue_id": input.queue_id,
            "project": input.project,
        }
    )
    owner = get_principal(ctx)
    service = VodMediaKitSubtitleBurnInService()
    await context_log(ctx, "info", "Starting VOD AI MediaKit subtitle burn-in")
    await ctx.report_progress(progress=20, total=100)
    try:
        async with runtime.provider_limiters.acquire("vod-mediakit", owner):
            submission = await service.submit(request)
    except ProviderError as exc:
        await context_log(
            ctx, "error", f"VOD AI MediaKit subtitle burn-in submission failed: {exc.message}"
        )
        return provider_error_result(exc)
    finally:
        await service.close()

    await runtime.ownership_store.record("vod-mediakit", submission.task_id, owner)
    await ctx.report_progress(progress=100, total=100)
    return VodAddSubtitlesOutput(
        status="accepted",
        request_id=submission.request_id,
        provider_log_id=submission.provider_log_id,
        task_id=submission.task_id,
        recommended_poll_after_ms=5000,
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
