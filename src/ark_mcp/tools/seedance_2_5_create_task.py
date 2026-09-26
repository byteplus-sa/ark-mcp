"""``seedance_2_5_create_task`` tool — create an asynchronous Seedance 2.5 video task.

Seedance 2.5 supports up to 30-second video generation, 50 multimodal
references (30 images, 10 videos, 10 audio), 480p/720p/1080p resolution, and
timestamp-level prompt control for editing.
"""

from __future__ import annotations

from typing import Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, model_validator

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import (
    ModelFamily,
    VideoCapabilities,
    get_capability_registry,
)
from ark_mcp.observability.logger import info as log_info
from ark_mcp.tools._seedance_shared import (
    SeedanceAudioInput,
    SeedanceImageInput,
    SeedanceVideoInput,
    execute_seedance_create,
    strip_ratio_for_video_extension,
)
from ark_mcp.tools._task_execution import context_log


class Seedance25CreateTaskInput(BaseModel):
    """Input model for ``seedance_2_5_create_task``."""

    prompt: str | None = Field(
        None,
        min_length=1,
        max_length=32000,
        description="Text prompt describing the video to generate (1-32,000 characters). Optional when media inputs are provided.",
    )
    images: list[SeedanceImageInput] | None = Field(
        None,
        max_length=30,
        description=(
            "Reference images with optional roles (first_frame, last_frame, reference_image). "
            "Max 30 for Seedance 2.5. Each entry may be a plain URL string or "
            '{"url": "https://..."}; these are coerced to role=reference_image.'
        ),
    )
    videos: list[SeedanceVideoInput] | None = Field(
        None,
        max_length=10,
        description=(
            "Reference videos. Max 10 for Seedance 2.5. Each entry may be a plain URL string "
            'or {"url": "https://..."}.'
        ),
    )
    audios: list[SeedanceAudioInput] | None = Field(
        None,
        max_length=10,
        description=(
            "Reference audio for audio-driven generation. Max 10 for Seedance 2.5. Audio-only input "
            "is supported (unique to 2.5): a single BGM, voice, or sound-effect track can drive "
            "visual pacing, beat matching, and lip-sync. Each entry may be a plain URL string or "
            '{"url": "https://..."}.'
        ),
    )
    model: str | None = Field(
        None,
        description="Model ID. Defaults to 'dreamina-seedance-2-5-260628'. Omit to use the default.",
    )
    resolution: Literal["480p", "720p", "1080p"] | None = Field(
        None,
        description="Output video resolution. Seedance 2.5 supports 480p, 720p, and 1080p. 4k is not supported.",
    )
    ratio: str | None = Field(
        None,
        description=(
            "Output aspect ratio (e.g. '16:9', '9:16'). Must be supported by the selected model. "
            "Video extension (extend_video): ratio must be omitted — it auto-locks to the source "
            "video; any value is stripped to prevent InvalidParameter.TaskTypeConstraint. "
            "Video editing (edit_video): ratio is auto-derived from the input video. "
            "For first/last-frame tasks, the ratio locks to the first image."
        ),
    )
    duration: int | None = Field(
        None,
        ge=-1,
        le=30,
        description=(
            "Video duration in seconds (-1 for auto). Max 30 for Seedance 2.5. "
            "Ignored for video editing tasks — the duration is auto-derived from the "
            "input video (within ~0.3s)."
        ),
    )
    omni_reference_task_type: str | None = Field(
        None,
        description=(
            "Task type hint for the provider. The provider defaults to 'auto' which "
            "auto-detects from the prompt and media. Set explicitly to force a specific "
            "task type (e.g. 'edit_video', 'extend_video') when auto-detection is "
            "ambiguous. When set, ratio and duration may be auto-derived from input "
            "media depending on the task type. For extend_video, ratio is stripped "
            "(auto-locks to source) to prevent InvalidParameter.TaskTypeConstraint."
        ),
    )
    generate_audio: bool | None = Field(
        None, description="Whether to generate an audio track for the video."
    )
    watermark: bool | None = Field(
        False, description="Whether to apply an AIGC watermark to the video."
    )
    return_last_frame: bool | None = Field(
        None, description="Whether to return the last frame as a separate image output."
    )
    execution_expires_after: int | None = Field(
        None,
        ge=3600,
        le=259200,
        description="Maximum execution time in seconds before the task expires (3600-259200, i.e. 1 hour to 3 days).",
    )
    priority: int | None = Field(
        None,
        ge=0,
        le=9,
        description="Task priority (0-9). Higher values may incur additional cost.",
    )
    safety_identifier: str | None = Field(
        None,
        max_length=64,
        description="Optional identifier for content safety tracking (max 64 characters).",
    )

    @model_validator(mode="after")
    def validate_media_required(self) -> Seedance25CreateTaskInput:
        """Seedance 2.5 supports audio-only input; no media at all is rejected."""
        has_images = bool(self.images)
        has_videos = bool(self.videos)
        has_audios = bool(self.audios)
        has_prompt = bool(self.prompt) or bool(getattr(self, "variation_prompts", None))

        if not has_images and not has_videos and not has_audios and not has_prompt:
            raise ValueError("At least one of prompt, images, videos, or audios is required.")
        return self

    @model_validator(mode="after")
    def validate_reference_counts(self) -> Seedance25CreateTaskInput:
        """Enforce reference count limits per Seedance 2.5 specs."""
        if self.images and len(self.images) > 30:
            raise ValueError(f"Too many reference images: {len(self.images)}. Maximum is 30.")
        if self.videos and len(self.videos) > 10:
            raise ValueError(f"Too many reference videos: {len(self.videos)}. Maximum is 10.")
        if self.audios and len(self.audios) > 10:
            raise ValueError(f"Too many reference audios: {len(self.audios)}. Maximum is 10.")
        return self

    @model_validator(mode="after")
    def validate_ratio_for_extension(self) -> Seedance25CreateTaskInput:
        """Strip ratio for video extension tasks to prevent InvalidParameter.TaskTypeConstraint."""
        self.ratio = strip_ratio_for_video_extension(self.omni_reference_task_type, self.ratio)
        return self


class Seedance25CreateTaskOutput(BaseModel):
    """Output model for ``seedance_2_5_create_task``."""

    task_id: str = Field(..., description="Provider task ID for polling and management.")
    status: Literal["queued"] = Field(
        "queued", description="Initial task status. Poll with seedance_get_task for updates."
    )
    recommended_poll_after_ms: int = Field(
        ..., description="Suggested delay in milliseconds before first poll."
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}


_FAMILY_TOOLS: dict[ModelFamily, tuple[str, str, str]] = {
    ModelFamily.SEEDANCE_2_5: (
        "Seedance 2.5",
        "seedance_2_5_create_task",
        '{"model_id": "dreamina-seedance-2-5-260628", "family": "seedance_2_5"}',
    ),
    ModelFamily.SEEDANCE_2_5_PREMIUM: (
        "Seedance 2.5 Premium",
        "seedance_2_5_premium_create_task",
        '{"model_id": "dreamina-seedance-2-5-premium-260915", "family": "seedance_2_5_premium"}',
    ),
}


def resolve_seedance_2_5_capabilities(
    input: Seedance25CreateTaskInput, family: ModelFamily
) -> VideoCapabilities:
    """Resolve and validate the model for a Seedance 2.5-generation tool.

    ``family`` selects the tool's model family (regular 2.5 or Premium). An
    explicit ``input.model`` must belong to that family; otherwise the first
    configured binding of the family is used. Resolution, duration, priority,
    and execution expiry are validated against the resolved capabilities.
    """
    label, _tool_name, binding_example = _FAMILY_TOOLS[family]
    registry = get_capability_registry()

    if input.model:
        caps = registry.get_video_capabilities(input.model)
        if caps.family is not family:
            other = _FAMILY_TOOLS.get(caps.family)
            hint = (
                f"Use {other[1]} for {other[0]} models."
                if other
                else "Use seedance_create_task for Seedance 2.0 models."
            )
            raise ValueError(f"Model '{input.model}' is not a {label} model. {hint}")
    else:
        family_ids = [
            mid
            for mid in registry.list_video_models()
            if registry.get_video_capabilities(mid).family is family
        ]
        if not family_ids:
            raise ValueError(
                f"No {label} model is configured. Set SEEDANCE_MODEL_BINDINGS "
                f"to include a {binding_example} binding."
            )
        caps = registry.get_video_capabilities(family_ids[0])

    registry.validate_resolution(caps.model_id, input.resolution)
    registry.validate_duration(caps.model_id, input.duration)

    if input.priority is not None:
        lo, hi = caps.priority_range
        if input.priority < lo or input.priority > hi:
            raise ValueError(
                f"Priority {input.priority} is outside the supported range "
                f"[{lo}, {hi}] for model '{caps.model_id}'."
            )

    if input.execution_expires_after is not None:
        lo, hi = caps.execution_expires_after_range
        if input.execution_expires_after < lo or input.execution_expires_after > hi:
            raise ValueError(
                f"execution_expires_after {input.execution_expires_after} is "
                f"outside the supported range [{lo}, {hi}] for model "
                f"'{caps.model_id}'."
            )
    return caps


async def seedance_2_5_create_task(
    input: Seedance25CreateTaskInput, ctx: Context
) -> Seedance25CreateTaskOutput | ToolResult:
    """Create an asynchronous Seedance 2.5 video generation task.

    Accepts text, image, video, and audio references as content input.
    Supports up to 30-second video generation, 50 multimodal references
    (30 images, 10 videos, 10 audio), and 480p/720p/1080p resolution.
    The task runs asynchronously on the provider — use
    ``seedance_get_task`` to poll for completion. Returns the task ID
    and a recommended polling interval. Requires MCP task-augmented execution
    for the provider submission itself.
    """
    return await run_seedance_2_5_create(input, ctx, ModelFamily.SEEDANCE_2_5)


async def run_seedance_2_5_create(
    input: Seedance25CreateTaskInput, ctx: Context, family: ModelFamily
) -> Seedance25CreateTaskOutput | ToolResult:
    """Create one provider task for a Seedance 2.5-generation family."""
    label = _FAMILY_TOOLS[family][0]
    await context_log(ctx, "info", f"Creating {label} video generation task")
    await ctx.report_progress(progress=10, total=100)

    settings = get_settings()
    if not settings.has_modelark:
        raise ValueError(
            "BYTEPLUS_MODELARK_API_KEY is not configured. Set it in .env to enable Seedance tools."
        )

    caps = resolve_seedance_2_5_capabilities(input, family)

    result = await execute_seedance_create(input, ctx, caps)
    if isinstance(result, ToolResult):
        return result
    task_id, _ = result
    log_info(
        "seedance_2_5_create_task_complete",
        task_id=task_id,
        family=str(family),
        model=caps.model_id,
    )
    return Seedance25CreateTaskOutput(
        task_id=task_id,
        status="queued",
        recommended_poll_after_ms=5000,
    )
