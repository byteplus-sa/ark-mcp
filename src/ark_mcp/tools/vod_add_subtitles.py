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
        description="Public HTTPS video URL; private and link-local hosts are rejected."
    )
    subtitle_url: HttpsUrl | None = Field(
        default=None,
        description="Public HTTPS SRT, VTT, or ASS file URL. One of subtitle_url or subtitles is required; wins if both are set.",
    )
    subtitles: list[VodMediaKitSubtitleCue] | None = Field(
        default=None,
        min_length=1,
        description="Inline subtitle cues. One of subtitle_url or subtitles is required.",
    )
    subtitle_pos_preset: Literal["bottom_center", "top_center", "center", "lower_third"] = Field(
        default="bottom_center",
        description="Subtitle position preset.",
    )
    subtitle_font_size: int = Field(
        default=50,
        gt=0,
        description="Subtitle font size in pixels.",
    )
    subtitle_font_color: str = Field(
        default="#FFFFFFFF",
        pattern=r"^#[0-9A-Fa-f]{8}$",
        description="Subtitle color and opacity as #RRGGBBAA (default opaque white).",
    )
    subtitle_font_type: SubtitleFont = Field(
        default="inter",
        description="MediaKit font identifier.",
    )
    client_token: str | None = Field(
        default=None,
        pattern=r"^[\x20-\x7E]{1,64}$",
        description="Idempotency token (1-64 printable ASCII chars); reuse it to reconcile a timed-out submission.",
    )
    callback_args: str | None = Field(
        default=None,
        description="Callback payload echoed by MediaKit; max 512 UTF-8 bytes.",
    )
    callback_url: HttpsUrl | None = Field(
        default=None,
        description="Public HTTPS task callback URL; overrides the console callback.",
    )
    queue_id: str | None = Field(
        default=None,
        min_length=1,
        description="MediaKit queue ID; omit for the project's default queue.",
    )
    project: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        description="Legacy MediaKit project label (case-sensitive); omit for the API key's project.",
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
        description="Provider surface.",
    )
    status: Literal["accepted"] = Field(description="Always accepted; the task is asynchronous.")
    request_id: str | None = Field(default=None, description="Provider request ID, when returned.")
    provider_log_id: str | None = Field(
        default=None, description="Provider x-tt-logid, when returned."
    )
    task_id: str = Field(
        description="Task ID to poll with vod_get_task (operation 'add_subtitles')."
    )
    operation: VodOperation = Field(default="add_subtitles", description=VOD_OPERATION_DESCRIPTION)
    recommended_poll_after_ms: int = Field(
        description="Suggested delay before the first vod_get_task poll, in milliseconds."
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

    Returns a task_id; poll it with vod_get_task (operation 'add_subtitles').
    Not retried automatically; reuse client_token to reconcile a timeout.
    Requires background execution (ark_job_submit).
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
