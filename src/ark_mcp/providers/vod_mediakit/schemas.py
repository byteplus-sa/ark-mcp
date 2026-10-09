"""Internal request/response DTOs for the VOD AI MediaKit endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AliasChoices,
    AnyUrl,
    BaseModel,
    ConfigDict,
    Field,
    UrlConstraints,
    field_validator,
    model_validator,
)

HttpsUrl = Annotated[AnyUrl, UrlConstraints(allowed_schemes=["https"])]


EnhancementTier = Literal["fast", "standard", "professional"]
EnhancementResolution = Literal[
    "240p", "360p", "480p", "540p", "720p", "1080p", "2k", "4k", "6k", "8k"
]

DEFAULT_ENHANCEMENT_RESOLUTION: EnhancementResolution = "4k"
FAST_ENHANCEMENT_RESOLUTIONS: frozenset[str] = frozenset(
    {"240p", "360p", "480p", "540p", "720p", "1080p", "2k", "4k"}
)
FAST_RESOLUTION_LIMIT_MAX = 2160
FULL_RESOLUTION_LIMIT_MAX = 4320


def validate_enhancement_profile(
    *,
    tool_version: str,
    resolution: str | None,
    resolution_limit: int | None,
    enhance_style: str | None,
) -> None:
    """Enforce the documented cross-field rules for the enhancement endpoints."""
    if resolution is not None and resolution_limit is not None:
        raise ValueError("resolution and resolution_limit are mutually exclusive; set only one")
    if tool_version != "fast":
        return
    if resolution is not None and resolution not in FAST_ENHANCEMENT_RESOLUTIONS:
        raise ValueError("fast enhancement supports resolutions from 240p up to 4k only")
    if resolution_limit is not None and resolution_limit > FAST_RESOLUTION_LIMIT_MAX:
        raise ValueError(
            f"fast enhancement supports resolution_limit up to {FAST_RESOLUTION_LIMIT_MAX}"
        )
    if enhance_style is not None:
        raise ValueError("enhance_style is not supported by the fast enhancement tier")


class VodMediaKitEnhancementRequest(BaseModel):
    """Request profile for ``POST /enhance-video`` and ``POST /enhance-video-fast``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    video_url: HttpsUrl
    scene: Literal["common", "ugc", "short_series", "aigc", "old_film"] = "common"
    tool_version: EnhancementTier = "professional"
    enhance_style: Literal["hd", "natural"] | None = None
    resolution: EnhancementResolution | None = None
    resolution_limit: int | None = Field(default=None, ge=128, le=FULL_RESOLUTION_LIMIT_MAX)
    bitrate_level: Literal["low", "medium", "high"] = "high"
    bitrate: int | None = Field(default=None, ge=10, le=150000)
    fps: float | None = Field(default=24, ge=15, le=120)
    project: str = Field(
        default="default",
        min_length=1,
        max_length=128,
        serialization_alias="Project",
    )

    @model_validator(mode="after")
    def validate_profile(self) -> VodMediaKitEnhancementRequest:
        """Apply cross-field rules and default the target to 4K when none is given."""
        validate_enhancement_profile(
            tool_version=self.tool_version,
            resolution=self.resolution,
            resolution_limit=self.resolution_limit,
            enhance_style=self.enhance_style,
        )
        if self.resolution is None and self.resolution_limit is None:
            self.resolution = DEFAULT_ENHANCEMENT_RESOLUTION
        return self

    def to_payload(self) -> dict[str, Any]:
        """Serialize the wire body, omitting fields the selected endpoint does not document."""
        excluded = {"scene", "enhance_style", "project", "tool_version"} if self.is_fast else set()
        return self.model_dump(mode="json", by_alias=True, exclude_none=True, exclude=excluded)

    @property
    def is_fast(self) -> bool:
        """Whether this request targets the dedicated fast-tier endpoint."""
        return self.tool_version == "fast"


class VodMediaKitProviderErrorDetail(BaseModel):
    """Error detail returned by MediaKit."""

    model_config = ConfigDict(extra="ignore")

    code: str | None = None
    type: str | None = None
    message: str | None = None


class VodMediaKitProviderResult(BaseModel):
    """Provisional synchronous result object from MediaKit."""

    model_config = ConfigDict(extra="ignore")

    output_url: HttpsUrl = Field(validation_alias=AliasChoices("output_url", "video_url", "url"))
    request_id: str | None = None
    task_id: str | None = Field(default=None, validation_alias=AliasChoices("task_id", "id"))
    status: str | None = None
    mime_type: str | None = Field(
        default=None,
        validation_alias=AliasChoices("mime_type", "content_type"),
    )
    expires_at: str | None = Field(
        default=None,
        validation_alias=AliasChoices("expires_at", "expiration"),
    )
    output_size_bytes: int | None = Field(
        default=None,
        ge=0,
        validation_alias=AliasChoices("output_size_bytes", "size"),
    )
    error: VodMediaKitProviderErrorDetail | None = None

    @field_validator("expires_at")
    @classmethod
    def require_iso_8601_expiry(cls, value: str | None) -> str | None:
        """Accept expiry metadata only when it is an ISO-8601 timestamp."""
        if value is not None:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class VodMediaKitProviderResponse(BaseModel):
    """Supported provisional synchronous success envelope."""

    model_config = ConfigDict(extra="ignore")

    success: Literal[True]
    result: VodMediaKitProviderResult = Field(validation_alias=AliasChoices("data", "result"))
    request_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def require_exactly_one_result_container(cls, value: Any) -> Any:
        """Reject ambiguous envelopes containing both result aliases."""
        if isinstance(value, dict) and "data" in value and "result" in value:
            raise ValueError("response must contain only one of 'data' or 'result'")
        return value


class VodMediaKitAcceptedResponse(BaseModel):
    """Directly observed asynchronous acceptance envelope."""

    model_config = ConfigDict(extra="ignore")

    success: Literal[True]
    task_id: str = Field(min_length=1)
    request_id: str | None = None


class EnhancementSubmission(BaseModel):
    """Normalized accepted or completed enhancement result for the tool layer."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted", "succeeded"]
    request_id: str | None = None
    provider_log_id: str | None = None
    task_id: str | None = None
    output_url: HttpsUrl | None = None
    mime_type: str | None = None
    expires_at: str | None = None
    output_size_bytes: int | None = Field(default=None, ge=0)
    provider_status: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None

    @model_validator(mode="after")
    def validate_status_payload(self) -> EnhancementSubmission:
        """Require a task for acceptance and an output URL for completion."""
        if self.status == "accepted":
            if not self.task_id:
                raise ValueError("accepted enhancement requires task_id")
            if self.output_url is not None:
                raise ValueError("accepted enhancement must not include output_url")
        elif self.output_url is None:
            raise ValueError("succeeded enhancement requires output_url")
        return self

    @field_validator("provider_status", "failure_code", "failure_message")
    @classmethod
    def reject_blank_optional_strings(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value


class VodMediaKitEnhancementTaskResult(BaseModel):
    """Live-confirmed result object for a completed enhancement task."""

    model_config = ConfigDict(extra="ignore")

    video_url: HttpsUrl
    duration: float | None = Field(default=None, ge=0)
    fps: float | None = Field(default=None, gt=0)
    resolution: str | None = None
    tool_version: str | None = None


class VodMediaKitEnhancementTaskResponse(BaseModel):
    """Response from ``GET /tasks/{task_id}`` for standard, professional, or fast enhancement."""

    model_config = ConfigDict(extra="ignore")

    success: Literal[True]
    task_id: str = Field(min_length=1)
    task_type: Literal["enhance-video", "enhance-video-fast"]
    status: str = Field(min_length=1)
    result: VodMediaKitEnhancementTaskResult | None = None
    error: VodMediaKitProviderErrorDetail | None = None
    request_id: str | None = None
    queue_id: str | None = None
    expires_at: str | int | None = None
    created_at: str | int | None = None
    finished_at: str | int | None = None


class EnhancementTask(BaseModel):
    """Normalized enhancement task state for the tool layer."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    status: Literal["processing", "succeeded", "failed"]
    provider_status: str | None = None
    request_id: str | None = None
    output_url: HttpsUrl | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    fps: float | None = Field(default=None, gt=0)
    resolution: str | None = None
    tool_version: str | None = None
    created_at: str | None = None
    finished_at: str | None = None
    source_expires_at: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> EnhancementTask:
        """Require output metadata only for successful tasks and errors only for failures."""
        if self.status == "succeeded":
            if self.output_url is None:
                raise ValueError("succeeded enhancement task requires output_url")
            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError("succeeded enhancement task must not carry a failure")
        elif self.status == "processing":
            if self.output_url is not None:
                raise ValueError("processing enhancement task must not carry output_url")
            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError("processing enhancement task must not carry a failure")
        elif self.output_url is not None:
            raise ValueError("failed enhancement task must not carry output_url")
        return self


class VodMediaKitProviderErrorResponse(BaseModel):
    """Verified MediaKit error response envelope."""

    model_config = ConfigDict(extra="ignore")

    success: bool | None = None
    error: VodMediaKitProviderErrorDetail | None = None
    request_id: str | None = None


class VodMediaKitTranscodeVideoOptions(BaseModel):
    """Verified ``video`` object for ``POST /transcode-video``.

    Field names, enum values, and ranges are confirmed from the official AI
    MediaKit API reference (2026-08-14).
    """

    model_config = ConfigDict(extra="forbid")

    codec: Literal["h264", "h265"] = Field(
        default="h264",
        description="Output video codec.",
    )
    scale_type: Literal[0, 1, 2] = Field(
        default=2,
        description="Scaling mode: 0 = follow source, 1 = long/short-side limit, 2 = width/height limit.",
    )
    scale_mode: Literal[0, 1, 2] = Field(
        default=2,
        description="Aspect handling when scaling: 0 = no upsampling, 1 = stretch, 2 = letterbox.",
    )
    scale_width: int | None = Field(
        default=None,
        ge=0,
        le=4320,
        description="Target width in pixels; only when scale_type=2.",
    )
    scale_height: int | None = Field(
        default=None,
        ge=0,
        le=4320,
        description="Target height in pixels; only when scale_type=2.",
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
        description="Bitrate control mode: crf, abr, or cbr.",
    )
    bitrate_crf: int = Field(
        default=25,
        ge=0,
        le=51,
        description="CRF quality level; only used when bitrate_mode=crf.",
    )
    bitrate_kbps: int = Field(
        default=2000,
        ge=10,
        le=50000,
        description="Bitrate in kbps.",
    )
    fps_mode: Literal["vfr", "cfr"] = Field(
        default="vfr",
        description="Frame-rate mode; only takes effect after fps is set.",
    )
    fps: int | None = Field(
        default=None,
        ge=1,
        le=240,
        description="Target frame rate; unset keeps the source rate.",
    )
    is_hdr_to_sdr: bool = Field(
        default=True,
        description="Convert HDR input to SDR.",
    )

    @model_validator(mode="after")
    def validate_scale_fields(self) -> VodMediaKitTranscodeVideoOptions:
        if self.scale_type == 2 and self.scale_width is None and self.scale_height is None:
            raise ValueError("scale_type=2 requires scale_width and/or scale_height")
        if self.scale_type == 1 and self.scale_short is None and self.scale_long is None:
            raise ValueError("scale_type=1 requires scale_short and/or scale_long")
        if self.scale_type != 2 and (self.scale_width is not None or self.scale_height is not None):
            raise ValueError("scale_width/scale_height require scale_type=2")
        if self.scale_type != 1 and (self.scale_short is not None or self.scale_long is not None):
            raise ValueError("scale_short/scale_long require scale_type=1")
        return self


class VodMediaKitTranscodeRequest(BaseModel):
    """Verified request body for ``POST /tools/transcode-video``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    video_url: HttpsUrl
    container_format: Literal["MP4", "FLV", "MPEGTS"] = "MP4"
    video: VodMediaKitTranscodeVideoOptions = Field(
        default_factory=VodMediaKitTranscodeVideoOptions
    )


class VodMediaKitTranscodeTaskResult(BaseModel):
    """Verified ``result`` object of a completed transcode task."""

    model_config = ConfigDict(extra="ignore")

    video_url: HttpsUrl = Field(validation_alias=AliasChoices("video_url", "output_url", "url"))
    duration: float | None = Field(default=None, ge=0)
    resolution: str | None = None
    video_codec: str | None = None


class VodMediaKitTranscodeTaskResponse(BaseModel):
    """Verified polling response from ``GET /tasks/{task_id}``."""

    model_config = ConfigDict(extra="ignore")

    success: Literal[True]
    task_id: str = Field(min_length=1)
    task_type: str | None = None
    status: str = Field(min_length=1)
    result: VodMediaKitTranscodeTaskResult | None = None
    error: VodMediaKitProviderErrorDetail | None = None
    request_id: str | None = None
    queue_id: str | None = None
    expires_at: str | int | None = Field(
        default=None,
        description="Provider output URL expiry as Unix-seconds or ISO-8601.",
    )
    created_at: str | int | None = None
    finished_at: str | int | None = None


class TranscodeSubmission(BaseModel):
    """Normalized accepted transcode submission for the tool layer."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted"]
    request_id: str | None = None
    provider_log_id: str | None = None
    task_id: str = Field(min_length=1)


class TranscodeTask(BaseModel):
    """Normalized transcode task state for the tool layer."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    status: Literal["processing", "succeeded", "failed"]
    provider_status: str | None = None
    request_id: str | None = None
    output_url: HttpsUrl | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    resolution: str | None = None
    video_codec: str | None = None
    created_at: str | None = None
    finished_at: str | None = None
    source_expires_at: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> TranscodeTask:
        if self.status == "succeeded":
            if self.output_url is None:
                raise ValueError("succeeded transcode task requires output_url")
            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError("succeeded transcode task must not carry a failure")
        elif self.status == "processing":
            if self.output_url is not None:
                raise ValueError("processing transcode task must not carry output_url")
        elif self.failure_code is None and self.failure_message is None:
            raise ValueError("failed transcode task requires failure detail")
        return self

    @field_validator("failure_code", "failure_message")
    @classmethod
    def reject_blank_optional_strings(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value


class VodMediaKitSeparateVoiceRequest(BaseModel):
    """Verified request body for ``POST /tools/separate-voice``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    audio_url: HttpsUrl | None = None
    video_url: HttpsUrl | None = None
    scene: Literal["Audio", "Music", "Drama", "Narrate"] = "Audio"
    output_format: Literal["aac", "mp3", "wav", "m4a", "flac"] = "aac"

    @model_validator(mode="after")
    def require_exactly_one_source(self) -> VodMediaKitSeparateVoiceRequest:
        if (self.audio_url is None) == (self.video_url is None):
            raise ValueError("exactly one of audio_url or video_url is required")
        return self


class VodMediaKitSeparateVoiceTaskResult(BaseModel):
    """Verified ``result`` object of a completed separate-voice task."""

    model_config = ConfigDict(extra="ignore")

    voice_audio_url: HttpsUrl | None = None
    background_audio_url: HttpsUrl | None = None
    music_audio_url: HttpsUrl | None = None
    sfx_audio_url: HttpsUrl | None = None
    duration: float | None = Field(default=None, ge=0)


class VodMediaKitSeparateVoiceTaskResponse(BaseModel):
    """Verified polling response from ``GET /tasks/{task_id}``."""

    model_config = ConfigDict(extra="ignore")

    success: Literal[True]
    task_id: str = Field(min_length=1)
    task_type: str | None = None
    status: str = Field(min_length=1)
    result: VodMediaKitSeparateVoiceTaskResult | None = None
    error: VodMediaKitProviderErrorDetail | None = None
    request_id: str | None = None
    queue_id: str | None = None
    expires_at: str | int | None = Field(
        default=None,
        description="Provider output URL expiry as Unix-seconds or ISO-8601.",
    )
    created_at: str | int | None = None
    finished_at: str | int | None = None


class SeparateVoiceSubmission(BaseModel):
    """Normalized accepted separate-voice submission for the tool layer."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted"]
    request_id: str | None = None
    provider_log_id: str | None = None
    task_id: str = Field(min_length=1)


class SeparateVoiceTask(BaseModel):
    """Normalized separate-voice task state for the tool layer."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    status: Literal["processing", "succeeded", "failed"]
    provider_status: str | None = None
    request_id: str | None = None
    voice_url: HttpsUrl | None = None
    background_url: HttpsUrl | None = None
    music_url: HttpsUrl | None = None
    sfx_url: HttpsUrl | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    created_at: str | None = None
    finished_at: str | None = None
    source_expires_at: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> SeparateVoiceTask:
        if self.status == "succeeded":
            if (
                self.voice_url is None
                and self.background_url is None
                and self.music_url is None
                and self.sfx_url is None
            ):
                raise ValueError("succeeded separate-voice task requires at least one track URL")
            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError("succeeded separate-voice task must not carry a failure")
        elif self.status == "processing":
            if (
                self.voice_url is not None
                or self.background_url is not None
                or self.music_url is not None
                or self.sfx_url is not None
            ):
                raise ValueError("processing separate-voice task must not carry track URLs")
        elif self.failure_code is None and self.failure_message is None:
            raise ValueError("failed separate-voice task requires failure detail")
        return self

    @field_validator("failure_code", "failure_message")
    @classmethod
    def reject_blank_optional_strings(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value


class VodMediaKitSubtitleCue(BaseModel):
    """One inline subtitle cue for the MediaKit burn-in endpoint."""

    model_config = ConfigDict(extra="forbid")

    subtitle_text: str = Field(min_length=1, description="Subtitle text shown for this cue.")
    start_time: float = Field(
        ge=0,
        allow_inf_nan=False,
        description="Cue start time in seconds from the beginning of the video.",
    )
    end_time: float = Field(
        gt=0,
        allow_inf_nan=False,
        description="Cue end time in seconds from the beginning of the video.",
    )

    @field_validator("subtitle_text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("subtitle_text must not be blank")
        return value

    @model_validator(mode="after")
    def validate_time_range(self) -> VodMediaKitSubtitleCue:
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time")
        return self


class VodMediaKitAddSubtitlesRequest(BaseModel):
    """Request body for ``POST /tools/add-subtitle-to-video``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    video_url: HttpsUrl
    subtitle_url: HttpsUrl | None = None
    subtitles: list[VodMediaKitSubtitleCue] | None = Field(default=None, min_length=1)
    subtitle_pos_preset: Literal["bottom_center", "top_center", "center", "lower_third"] = (
        "bottom_center"
    )
    subtitle_font_size: int = Field(default=50, gt=0)
    subtitle_font_color: str = Field(default="#FFFFFFFF", pattern=r"^#[0-9A-Fa-f]{8}$")
    subtitle_font_type: Literal[
        "inter",
        "montserrat",
        "oppo_sans",
        "roboto",
        "source_han_serif",
        "sy_black",
        "pm_zhengdao",
        "zhanku_kuaile",
    ] = "inter"
    client_token: str | None = Field(default=None, pattern=r"^[\x20-\x7E]{1,64}$")
    callback_args: str | None = None
    callback_url: HttpsUrl | None = None
    queue_id: str | None = Field(default=None, min_length=1)
    project: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        serialization_alias="Project",
    )

    @model_validator(mode="after")
    def require_subtitle_content(self) -> VodMediaKitAddSubtitlesRequest:
        if self.subtitle_url is None and not self.subtitles:
            raise ValueError("subtitle_url or subtitles is required")
        return self

    @field_validator("callback_args")
    @classmethod
    def validate_callback_args_size(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8")) > 512:
            raise ValueError("callback_args must not exceed 512 bytes")
        return value


class VodMediaKitEraseLocation(BaseModel):
    """Normalized rectangular erasure area within a video frame."""

    model_config = ConfigDict(extra="forbid")

    top_left_x: float = Field(
        ge=0, le=1, allow_inf_nan=False, description="Left edge as a frame-width ratio."
    )
    top_left_y: float = Field(
        ge=0, le=1, allow_inf_nan=False, description="Top edge as a frame-height ratio."
    )
    bottom_right_x: float = Field(
        ge=0,
        le=1,
        allow_inf_nan=False,
        description="Right edge as a frame-width ratio; greater than top_left_x.",
    )
    bottom_right_y: float = Field(
        ge=0,
        le=1,
        allow_inf_nan=False,
        description="Bottom edge as a frame-height ratio; greater than top_left_y.",
    )

    @model_validator(mode="after")
    def validate_corners(self) -> VodMediaKitEraseLocation:
        if self.bottom_right_x <= self.top_left_x:
            raise ValueError("bottom_right_x must be greater than top_left_x")
        if self.bottom_right_y <= self.top_left_y:
            raise ValueError("bottom_right_y must be greater than top_left_y")
        return self


class VodMediaKitTimeSegment(BaseModel):
    """Time interval selected or skipped during precision erasure."""

    model_config = ConfigDict(extra="forbid")

    start_time: float = Field(
        ge=0, allow_inf_nan=False, description="Segment start time in seconds."
    )
    end_time: float = Field(
        gt=0,
        allow_inf_nan=False,
        description="Segment end time in seconds; greater than start_time.",
    )

    @model_validator(mode="after")
    def validate_time_range(self) -> VodMediaKitTimeSegment:
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be greater than start_time")
        return self


class VodMediaKitTimeSegmentFilter(BaseModel):
    """Controls whether listed erasure time segments are selected or skipped."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["selected", "skip"] = Field(
        description="selected erases only listed segments; skip erases outside them."
    )
    segments: list[VodMediaKitTimeSegment] = Field(
        min_length=1, description="Non-empty list of time segments."
    )


class VodMediaKitSubtitleFilter(BaseModel):
    """Optional OCR thresholds used by precision subtitle erasure."""

    model_config = ConfigDict(extra="forbid")

    min_text_height_ratio: float | None = Field(
        default=None,
        ge=0,
        le=1,
        allow_inf_nan=False,
        description="Minimum OCR text-height ratio treated as a subtitle.",
    )
    max_text_height_ratio: float | None = Field(
        default=None,
        ge=0,
        le=1,
        allow_inf_nan=False,
        description="Maximum OCR text-height ratio treated as a subtitle.",
    )
    center_offset_ratio: float | None = Field(
        default=None,
        ge=0,
        le=1,
        allow_inf_nan=False,
        description="Maximum horizontal center offset ratio treated as a subtitle.",
    )

    @model_validator(mode="after")
    def validate_height_range(self) -> VodMediaKitSubtitleFilter:
        if (
            self.min_text_height_ratio is not None
            and self.max_text_height_ratio is not None
            and self.min_text_height_ratio > self.max_text_height_ratio
        ):
            raise ValueError("min_text_height_ratio must not exceed max_text_height_ratio")
        return self


class VodMediaKitRemoveSubtitlesRequest(BaseModel):
    """Request body for ``POST /tools/erase-video-subtitle-pro``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    video_url: HttpsUrl
    mode: Literal["Subtitle", "Text"] = "Subtitle"
    output_encode_mode: Literal["Quality", "Size"] = "Quality"
    erase_ratio_location: list[VodMediaKitEraseLocation] | None = Field(
        default=None, min_length=1, max_length=20
    )
    time_segment_filter: VodMediaKitTimeSegmentFilter | None = None
    subtitle_filter: VodMediaKitSubtitleFilter | None = None
    client_token: str | None = Field(default=None, pattern=r"^[\x20-\x7E]{1,64}$")
    callback_args: str | None = None
    callback_url: HttpsUrl | None = None
    queue_id: str | None = Field(default=None, min_length=1)
    model_version: Literal["v4", "v5"] | None = None
    project: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
        serialization_alias="Project",
    )

    @field_validator("callback_args")
    @classmethod
    def validate_callback_args_size(cls, value: str | None) -> str | None:
        if value is not None and len(value.encode("utf-8")) > 512:
            raise ValueError("callback_args must not exceed 512 bytes")
        return value


class VodMediaKitSubtitleTaskResult(BaseModel):
    """Completed video result from a subtitle MediaKit task."""

    model_config = ConfigDict(extra="ignore")

    video_url: HttpsUrl = Field(validation_alias=AliasChoices("video_url", "output_url", "url"))
    duration: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    resolution: str | None = None


class VodMediaKitSubtitleTaskResponse(BaseModel):
    """Polling response from ``GET /tasks/{task_id}`` for subtitle operations."""

    model_config = ConfigDict(extra="ignore")

    success: Literal[True]
    task_id: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    status: str = Field(min_length=1)
    result: VodMediaKitSubtitleTaskResult | None = None
    error: VodMediaKitProviderErrorDetail | None = None
    request_id: str | None = None
    queue_id: str | None = None
    expires_at: str | int | None = None
    created_at: str | int | None = None
    finished_at: str | int | None = None


class SubtitleSubmission(BaseModel):
    """Normalized accepted subtitle operation for the tool layer."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["accepted"]
    request_id: str | None = None
    provider_log_id: str | None = None
    task_id: str = Field(min_length=1)


class SubtitleTask(BaseModel):
    """Normalized subtitle burn-in or erasure task state."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    status: Literal["processing", "succeeded", "failed"]
    provider_status: str | None = None
    request_id: str | None = None
    output_url: HttpsUrl | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    resolution: str | None = None
    created_at: str | None = None
    finished_at: str | None = None
    source_expires_at: str | None = None
    failure_code: str | None = None
    failure_message: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> SubtitleTask:
        if self.status == "succeeded":
            if self.output_url is None:
                raise ValueError("succeeded subtitle task requires output_url")
            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError("succeeded subtitle task must not carry a failure")
        elif self.status == "processing":
            if self.output_url is not None:
                raise ValueError("processing subtitle task must not carry output_url")
            if self.failure_code is not None or self.failure_message is not None:
                raise ValueError("processing subtitle task must not carry a failure")
        elif self.failure_code is None and self.failure_message is None:
            raise ValueError("failed subtitle task requires failure detail")
        return self

    @field_validator("failure_code", "failure_message")
    @classmethod
    def reject_blank_optional_strings(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            return None
        return value
