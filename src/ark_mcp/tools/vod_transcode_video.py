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
    """Transcode ``video`` object; fields and enums verified from official docs."""

    codec: Literal["h264", "h265"] = Field(
        default="h264", description="Output video codec: h264 or h265."
    )
    scale_type: Literal[0, 1, 2] = Field(
        default=2,
        description="Scaling mode: 0 = follow source (no scaling), 1 = long/short-side limit, 2 = width/height limit.",
    )
    scale_mode: Literal[0, 1, 2] = Field(
        default=2,
        description="Aspect-ratio handling when scale_type is 1 or 2: 0 = no upsampling (shrink only), 1 = stretch to target, 2 = letterbox with black-bar padding.",
    )
    scale_width: int | None = Field(
        default=None,
        ge=0,
        le=4320,
        description="Target output width in pixels; only when scale_type=2. If only width or height is given, the other scales proportionally. Defaults to the verified 720 profile.",
    )
    scale_height: int | None = Field(
        default=None,
        ge=0,
        le=4320,
        description="Target output height in pixels; only when scale_type=2. Defaults to the verified 720 profile.",
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
        description="Bitrate control: crf (quality), abr (average bitrate), cbr (constant bitrate).",
    )
    bitrate_crf: int = Field(
        default=25,
        ge=0,
        le=51,
        description="CRF quality level [0,51]; 0 is lossless; only used when bitrate_mode=crf.",
    )
    bitrate_kbps: int = Field(
        default=2000,
        ge=10,
        le=50000,
        description="Bitrate in kbps [10,50000]; crf = max limit, abr = average target, cbr = constant target.",
    )
    fps_mode: Literal["vfr", "cfr"] = Field(
        default="vfr",
        description="Frame-rate mode; only takes effect after fps is set: vfr (max limit), cfr (forced constant).",
    )
    fps: int | None = Field(
        default=None,
        ge=1,
        le=240,
        description="Target frame rate [1,240]; if unset the source frame rate is kept.",
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
        description="Public HTTPS source-video URL that BytePlus can fetch. Private and link-local destinations are rejected."
    )
    container_format: Literal["MP4", "FLV", "MPEGTS"] = Field(
        default="MP4", description="Output container format: MP4 (default), FLV, or MPEGTS."
    )
    video: VodTranscodeVideoOptions = Field(
        default_factory=VodTranscodeVideoOptions,
        description="Transcoding options for the output video (codec, scaling, bitrate, frame rate, HDR). Defaults reproduce the verified portrait-to-720x720 letterbox profile.",
    )


class VodTranscodeVideoOutput(BaseModel):
    """Accepted asynchronous transcode submission."""

    provider: Literal["byteplus-vod-mediakit"] = Field(
        default="byteplus-vod-mediakit", description="Provider surface that processed the request."
    )
    status: Literal["accepted"] = Field(
        description="Always 'accepted': the transcode task is asynchronous."
    )
    request_id: str | None = Field(
        default=None, description="Provider diagnostic request ID, when returned."
    )
    provider_log_id: str | None = Field(
        default=None, description="Provider x-tt-logid diagnostic identifier, when returned."
    )
    task_id: str = Field(
        description="Provider task ID to pass to vod_get_task (operation 'transcode') for polling."
    )
    operation: VodOperation = Field(default="transcode", description=VOD_OPERATION_DESCRIPTION)
    recommended_poll_after_ms: int = Field(
        description="Server-side suggested poll delay; a heuristic, not a provider guarantee."
    )


async def vod_transcode_video(
    input: VodTranscodeVideoInput, ctx: Context
) -> VodTranscodeVideoOutput | ToolResult:
    """Submit an asynchronous BytePlus VOD AI MediaKit video transcoding task.

    Accepts a public HTTPS source URL and transcoding options (codec, container
    format, scaling, bitrate, frame rate, HDR). The default options reproduce
    the verified portrait-to-720x720 letterbox profile. Returns an accepted
    task ID; poll it with vod_get_task using the returned task_id and operation
    ('transcode'). The mutation is never retried automatically because
    completion can be ambiguous after a timeout. Long-running work runs through
    ark_job_submit (or native MCP task augmentation).
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
