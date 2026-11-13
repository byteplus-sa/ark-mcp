"""MCP tool for the BytePlus VOD AI MediaKit transcode submission."""

from __future__ import annotations

from typing import Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, model_validator

from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.media import HttpsUrl
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.providers.vod_mediakit.schemas import VodMediaKitTranscodeRequest
from ark_mcp.providers.vod_mediakit.transcode import VodMediaKitTranscodeService
from ark_mcp.runtime import get_principal, get_runtime
from ark_mcp.security.url_policy import UrlValidationError, validate_url
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._task_execution import context_log
from ark_mcp.tools._vod_shared import VOD_OPERATION_DESCRIPTION, VodOperation


class VodTranscodeVideoOptions(BaseModel):
    """Output video options for transcoding."""

    codec: Literal["h264", "h265"] = Field(
        default="h264", description="Output video codec: h264 or h265."
    )
    scale_type: Literal[0, 1, 2] = Field(
        default=2,
        description="0 = keep source size, 1 = long/short-side limit, 2 = width/height limit.",
    )
    scale_mode: Literal[0, 1, 2] = Field(
        default=2,
        description="For scale_type 1 or 2: 0 = shrink only, 1 = stretch to target, 2 = letterbox with black bars.",
    )
    scale_width: int | None = Field(
        default=None,
        ge=0,
        le=4320,
        description="Target width in pixels; scale_type=2 only. If only one of width/height is set, the other scales proportionally. Defaults to 720.",
    )
    scale_height: int | None = Field(
        default=None,
        ge=0,
        le=4320,
        description="Target height in pixels; scale_type=2 only. Defaults to 720.",
    )
    scale_short: int | None = Field(
        default=None,
        ge=0,
        le=4320,
        description="Target short side in pixels; only when scale_type=1.",
    )
    scale_long: int | None = Field(
        default=None,
        ge=0,
        le=4320,
        description="Target long side in pixels; only when scale_type=1.",
    )
    bitrate_mode: Literal["crf", "abr", "cbr"] = Field(
        default="crf",
        description="crf (quality), abr (average bitrate), or cbr (constant bitrate).",
    )
    bitrate_crf: int = Field(
        default=25,
        ge=0,
        le=51,
        description="CRF level (0 is lossless); bitrate_mode=crf only.",
    )
    bitrate_kbps: int = Field(
        default=2000,
        ge=10,
        le=50000,
        description="Bitrate in kbps: max limit for crf, average target for abr, constant for cbr.",
    )
    fps_mode: Literal["vfr", "cfr"] = Field(
        default="vfr",
        description="vfr (fps is a max) or cfr (forced constant); only used when fps is set.",
    )
    fps: int | None = Field(
        default=None,
        ge=1,
        le=240,
        description="Target frame rate; unset keeps the source rate.",
    )
    is_hdr_to_sdr: bool = Field(
        default=True, description="Convert HDR input to SDR; false keeps HDR."
    )

    @model_validator(mode="after")
    def _validate_scale_fields(self) -> VodTranscodeVideoOptions:
        if self.scale_type == 2:
            if self.scale_width is None and self.scale_height is None:
                self.scale_width = 720
                self.scale_height = 720
        elif self.scale_type == 1 and self.scale_short is None and self.scale_long is None:
            raise ValueError("scale_type=1 requires scale_short and/or scale_long")
        if self.scale_type != 2 and (self.scale_width is not None or self.scale_height is not None):
            raise ValueError("scale_width/scale_height require scale_type=2")
        if self.scale_type != 1 and (self.scale_short is not None or self.scale_long is not None):
            raise ValueError("scale_short/scale_long require scale_type=1")
        return self


class VodTranscodeVideoInput(BaseModel):
    """Input for the BytePlus VOD AI MediaKit video transcoding task."""

    video_url: HttpsUrl = Field(
        description="Public HTTPS source-video URL; private and link-local hosts are rejected."
    )
    container_format: Literal["MP4", "FLV", "MPEGTS"] = Field(
        default="MP4", description="Output container format."
    )
    video: VodTranscodeVideoOptions = Field(
        default_factory=VodTranscodeVideoOptions,
        description="Output video options. Defaults letterbox to 720x720 (h264, CRF 25).",
    )


class VodTranscodeVideoOutput(BaseModel):
    """Accepted asynchronous transcode submission."""

    provider: Literal["byteplus-vod-mediakit"] = Field(
        default="byteplus-vod-mediakit", description="Provider surface."
    )
    status: Literal["accepted"] = Field(description="Always accepted; the task is asynchronous.")
    request_id: str | None = Field(default=None, description="Provider request ID, when returned.")
    provider_log_id: str | None = Field(
        default=None, description="Provider x-tt-logid, when returned."
    )
    task_id: str = Field(description="Task ID to poll with vod_get_task (operation 'transcode').")
    operation: VodOperation = Field(default="transcode", description=VOD_OPERATION_DESCRIPTION)
    recommended_poll_after_ms: int = Field(
        description="Suggested delay before the first vod_get_task poll, in milliseconds."
    )


async def vod_transcode_video(
    input: VodTranscodeVideoInput, ctx: Context
) -> VodTranscodeVideoOutput | ToolResult:
    """Transcode a public video (codec, container, scaling, bitrate, fps, HDR) with MediaKit.

    Default options letterbox to 720x720. Returns a task_id; poll it with
    vod_get_task (operation 'transcode'). Not retried automatically. Requires
    background execution (ark_job_submit).
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
        log_warning("vod_transcode_video_invalid_source_url", error=str(exc))
        return ToolResult(
            content=[{"type": "text", "text": UrlValidationError.safe_message}],
            is_error=True,
        )
    request = VodMediaKitTranscodeRequest.model_validate(
        {
            "video_url": validated_source.url,
            "container_format": input.container_format,
            "video": input.video.model_dump(mode="json", exclude_none=True),
        }
    )
    owner = get_principal(ctx)
    service = VodMediaKitTranscodeService()
    await context_log(ctx, "info", "Starting VOD AI MediaKit video transcoding")
    await ctx.report_progress(progress=20, total=100)
    try:
        async with runtime.provider_limiters.acquire("vod-mediakit", owner):
            submission = await service.submit(request)
    except ProviderError as exc:
        await context_log(
            ctx, "error", f"VOD AI MediaKit transcode submission failed: {exc.message}"
        )
        return provider_error_result(exc)
    finally:
        await service.close()

    await runtime.ownership_store.record("vod-mediakit", submission.task_id, owner)
    await ctx.report_progress(progress=100, total=100)
    return VodTranscodeVideoOutput(
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
