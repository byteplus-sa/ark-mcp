"""MCP tool for the BytePlus VOD AI MediaKit voice and background audio separation."""

from __future__ import annotations

from typing import Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, model_validator

from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.media import HttpsUrl
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.providers.vod_mediakit.schemas import VodMediaKitSeparateVoiceRequest
from ark_mcp.providers.vod_mediakit.separate_voice import (
    VodMediaKitSeparateVoiceService,
)
from ark_mcp.runtime import get_principal, get_runtime
from ark_mcp.security.url_policy import UrlValidationError, validate_url
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._task_execution import context_log
from ark_mcp.tools._vod_shared import VOD_OPERATION_DESCRIPTION, VodOperation


class VodSeparateAudioInput(BaseModel):
    """Public source URL and separation options for the MediaKit separate-voice task."""

    audio_url: HttpsUrl | None = Field(
        default=None,
        description=(
            "Public HTTPS audio URL (mp3, m4a, wav). Set exactly one of audio_url or video_url."
        ),
    )
    video_url: HttpsUrl | None = Field(
        default=None,
        description=(
            "Public HTTPS video URL (mp4, flv, ts, avi, mov, wmv, mkv). Set exactly one of "
            "audio_url or video_url."
        ),
    )
    scene: Literal["Audio", "Music", "Drama", "Narrate"] = Field(
        default="Audio",
        description=(
            "Audio and Music give voice + background tracks; Drama and Narrate give voice + "
            "music + sfx tracks."
        ),
    )
    output_format: Literal["aac", "mp3", "wav", "m4a", "flac"] = Field(
        default="aac",
        description="Output audio format.",
    )

    @model_validator(mode="after")
    def require_exactly_one_source(self) -> VodSeparateAudioInput:
        if (self.audio_url is None) == (self.video_url is None):
            raise ValueError("exactly one of audio_url or video_url is required")
        return self


class VodSeparateAudioOutput(BaseModel):
    """Accepted asynchronous voice and background audio separation submission."""

    provider: Literal["byteplus-vod-mediakit"] = Field(
        default="byteplus-vod-mediakit", description="Provider surface."
    )
    status: Literal["accepted"] = Field(description="Always accepted; the task is asynchronous.")
    request_id: str | None = Field(default=None, description="Provider request ID, when returned.")
    provider_log_id: str | None = Field(
        default=None, description="Provider x-tt-logid, when returned."
    )
    task_id: str = Field(
        description="Task ID to poll with vod_get_task (operation 'separate_audio')."
    )
    operation: VodOperation = Field(default="separate_audio", description=VOD_OPERATION_DESCRIPTION)
    recommended_poll_after_ms: int = Field(
        description="Suggested delay before the first vod_get_task poll, in milliseconds."
    )


async def vod_separate_audio(
    input: VodSeparateAudioInput, ctx: Context
) -> VodSeparateAudioOutput | ToolResult:
    """Split a public audio or video URL into voice and background tracks (plus music and sfx for Drama/Narrate scenes) with MediaKit.

    Returns a task_id; poll it with vod_get_task (operation 'separate_audio').
    Not retried automatically. Requires background execution (ark_job_submit).
    """
    runtime = get_runtime(ctx)
    settings = runtime.settings
    if not settings.has_vod_mediakit:
        raise ValueError(
            "BYTEPLUS_VOD_MEDIAKIT_API_KEY is not configured. Set it to enable this tool."
        )

    source = input.audio_url or input.video_url
    try:
        validated_source = validate_url(str(source))
    except UrlValidationError as exc:
        log_warning("vod_separate_audio_invalid_source_url", error=str(exc))
        return ToolResult(
            content=[{"type": "text", "text": UrlValidationError.safe_message}],
            is_error=True,
        )
    request = VodMediaKitSeparateVoiceRequest.model_validate(
        {
            "audio_url": validated_source.url if input.audio_url else None,
            "video_url": validated_source.url if input.video_url else None,
            "scene": input.scene,
            "output_format": input.output_format,
        }
    )
    owner = get_principal(ctx)
    service = VodMediaKitSeparateVoiceService()
    await context_log(
        ctx, "info", "Starting BytePlus VOD AI MediaKit voice and background audio separation"
    )
    await ctx.report_progress(progress=20, total=100)
    try:
        async with runtime.provider_limiters.acquire("vod-mediakit", owner):
            submission = await service.submit(request)
    except ProviderError as exc:
        await context_log(
            ctx, "error", f"VOD AI MediaKit separation submission failed: {exc.message}"
        )
        return provider_error_result(exc)
    finally:
        await service.close()

    await runtime.ownership_store.record("vod-mediakit", submission.task_id, owner)
    await ctx.report_progress(progress=100, total=100)
    return VodSeparateAudioOutput(
        status="accepted",
        request_id=submission.request_id,
        provider_log_id=submission.provider_log_id,
        task_id=submission.task_id,
        recommended_poll_after_ms=3000,
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
