"""``seedance_create_task`` tool — create an asynchronous Seedance video task.

The capability registry validates whether the selected model supports a
field or resolution. Pydantic cross-field validators enforce Seedance 2.0
duration bounds, reference roles, media counts, and the rule that audio
cannot be the sole media input.
"""

from __future__ import annotations

from typing import Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, model_validator

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import ModelFamily, get_capability_registry
from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import billed_provider_slot, get_principal, get_runtime
from ark_mcp.tools._asset_shared import collect_asset_ids, preflight_seedance_assets
from ark_mcp.tools._cost import log_cost_estimate
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._seedance_shared import (
    SeedanceAudioInput,
    SeedanceImageInput,
    SeedanceVideoInput,
    strip_ratio_for_video_extension,
)
from ark_mcp.tools._task_execution import context_log


class SeedanceCreateTaskInput(BaseModel):
    """Input model for ``seedance_create_task``."""

    prompt: str | None = Field(
        None,
        min_length=1,
        max_length=32000,
        description="Text prompt describing the video to generate (1-32,000 characters). Optional when media inputs are provided.",
    )
    images: list[SeedanceImageInput] | None = Field(
        None,
        description=(
            "Reference images with optional roles (first_frame, last_frame, reference_image). "
            'Max 9. Each entry may be a plain URL string or {"url": "https://..."}; '
            "these are coerced to role=reference_image."
        ),
    )
    videos: list[SeedanceVideoInput] | None = Field(
        None,
        description=(
            'Reference videos. Max 3. Each entry may be a plain URL string or {"url": "https://..."}.'
        ),
    )
    audios: list[SeedanceAudioInput] | None = Field(
        None,
        description=(
            "Reference audio for audio-driven generation. Max 3. Cannot be the sole media input. "
            'Each entry may be a plain URL string or {"url": "https://..."}.'
        ),
    )
    model: str | None = Field(
        None,
        description=(
            "Model ID. Available: 'dreamina-seedance-2-0-260128' (Standard, default, 480p-4K, 9 imgs/3 vids/3 audios). "
            "Fast and Mini model IDs are configured via SEEDANCE_MODEL_BINDINGS. "
            "For Seedance 2.5 (30s duration, 30 imgs/10 vids/10 audios), use seedance_2_5_create_task instead. "
            "Omit to use the default."
        ),
    )
    resolution: Literal["480p", "720p", "1080p", "4k"] | None = Field(
        None, description="Output video resolution. Must be supported by the selected model."
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
        le=15,
        description=(
            "Video duration in seconds (-1 for auto). Max 15. Must be within the selected "
            "model's supported range. Ignored for video editing tasks — the duration is "
            "auto-derived from the input video (within ~0.3s)."
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
        description="Task priority (0-9). Higher values may incur additional cost. Range depends on the selected model.",
    )
    safety_identifier: str | None = Field(
        None,
        max_length=64,
        description="Optional identifier for content safety tracking (max 64 characters).",
    )

    @model_validator(mode="after")
    def validate_media_required(self) -> SeedanceCreateTaskInput:
        """Audio cannot be the sole media input; text-only is allowed."""
        has_images = bool(self.images)
        has_videos = bool(self.videos)
        has_audios = bool(self.audios)
        has_prompt = bool(self.prompt) or bool(getattr(self, "variation_prompts", None))

        if not has_images and not has_videos:
            if has_audios:
                raise ValueError(
                    "Audio references cannot be the sole media input. "
                    "At least one image or video is required."
                )
            if not has_prompt:
                raise ValueError("At least one of prompt, images, or videos is required.")
        return self

    @model_validator(mode="after")
    def validate_reference_counts(self) -> SeedanceCreateTaskInput:
        """Enforce reference count limits per Seedance 2.0 docs."""
        if self.images and len(self.images) > 9:
            raise ValueError(f"Too many reference images: {len(self.images)}. Maximum is 9.")
        if self.videos and len(self.videos) > 3:
            raise ValueError(f"Too many reference videos: {len(self.videos)}. Maximum is 3.")
        if self.audios and len(self.audios) > 3:
            raise ValueError(f"Too many reference audios: {len(self.audios)}. Maximum is 3.")
        return self

    @model_validator(mode="after")
    def validate_ratio_for_extension(self) -> SeedanceCreateTaskInput:
        """Strip ratio for video extension tasks to prevent InvalidParameter.TaskTypeConstraint."""
        self.ratio = strip_ratio_for_video_extension(self.omni_reference_task_type, self.ratio)
        return self


class SeedanceCreateTaskOutput(BaseModel):
    """Output model for ``seedance_create_task``."""

    task_id: str = Field(..., description="Provider task ID for polling and management.")
    status: Literal["queued"] = Field(
        "queued", description="Initial task status. Poll with seedance_get_task for updates."
    )
    recommended_poll_after_ms: int = Field(
        ..., description="Suggested delay in milliseconds before first poll."
    )


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------


async def seedance_create_task(
    input: SeedanceCreateTaskInput, ctx: Context
) -> SeedanceCreateTaskOutput | ToolResult:
    """Create an asynchronous Seedance video generation task.

    Accepts text, image, video, and audio references as content input.
    The task runs asynchronously on the provider — use
    ``seedance_get_task`` to poll for completion. Returns the task ID
    and a recommended polling interval. Requires MCP task-augmented execution
    for the provider submission itself.
    """
    await context_log(ctx, "info", "Creating Seedance video generation task")
    await ctx.report_progress(progress=10, total=100)

    settings = get_settings()
    if not settings.has_modelark:
        raise ValueError(
            "BYTEPLUS_MODELARK_API_KEY is not configured. Set it in .env to enable Seedance tools."
        )

    registry = get_capability_registry()
    caps = registry.get_video_capabilities(input.model)

    if caps.family is ModelFamily.SEEDANCE_2_5:
        raise ValueError(
            f"Model '{input.model}' is a Seedance 2.5 model. "
            f"Use seedance_2_5_create_task for Seedance 2.5 models."
        )

    # Validate model-specific capabilities.
    registry.validate_resolution(input.model, input.resolution)
    registry.validate_duration(input.model, input.duration)

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

    await ctx.report_progress(progress=30, total=100)

    # Build the provider content array.
    images_data = None
    videos_data = None
    audios_data = None

    if input.images:
        images_data = [img.model_dump() for img in input.images]
    if input.videos:
        videos_data = [vid.model_dump() for vid in input.videos]
    if input.audios:
        audios_data = [aud.model_dump() for aud in input.audios]

    # Stop before billing when a referenced asset is still Processing or Failed.
    await preflight_seedance_assets(ctx, collect_asset_ids(images_data, videos_data, audios_data))

    content = SeedanceService.build_content(
        prompt=input.prompt,
        images=images_data,
        videos=videos_data,
        audios=audios_data,
    )

    request = SeedanceService.build_request(
        model=caps.model_id,
        content=content,
        resolution=input.resolution,
        ratio=input.ratio,
        duration=input.duration,
        generate_audio=input.generate_audio,
        watermark=input.watermark,
        return_last_frame=input.return_last_frame,
        execution_expires_after=input.execution_expires_after,
        priority=input.priority,
        safety_identifier=input.safety_identifier,
        omni_reference_task_type=input.omni_reference_task_type,
    )

    await ctx.report_progress(progress=50, total=100)

    estimated_cost = log_cost_estimate(product="video", variations=1, model_id=caps.model_id)

    service = SeedanceService()
    try:
        async with billed_provider_slot(
            ctx,
            provider="modelark",
            product="video",
            estimated_cost_usd=estimated_cost,
        ):
            task_id, request_id = await call_with_retry(lambda: service.create_task(request))
    except ProviderError as exc:
        await context_log(ctx, "error", f"Seedance task creation failed: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    await get_runtime(ctx).ownership_store.record("modelark", task_id, get_principal(ctx))

    await ctx.report_progress(progress=100, total=100)
    log_info(
        "seedance_task_created",
        task_id=task_id,
        model=caps.model_id,
        request_id=request_id,
    )

    # Recommend polling after ~5 seconds for the first check.
    return SeedanceCreateTaskOutput(
        task_id=task_id,
        status="queued",
        recommended_poll_after_ms=5000,
    )


# Tool annotation constants — camelCase per MCP specification.
TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
