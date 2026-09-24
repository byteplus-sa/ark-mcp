"""Shared Seedance tool components.

Input types (``SeedanceImageInput``, ``SeedanceVideoInput``,
``SeedanceAudioInput``) and the ``execute_seedance_create`` helper used by
both Seedance 2.0 and 2.5 tool handlers.
"""

from __future__ import annotations

from typing import Any, ClassVar, Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, model_validator

from ark_mcp.config.model_capabilities import VideoCapabilities
from ark_mcp.domain.artifacts import MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.media import (
    ASSET_REFERENCE_URL_DESCRIPTION,
    MediaSource,
    validate_reference_url,
)
from ark_mcp.observability.logger import info as log_info
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import billed_provider_slot, get_principal, get_runtime
from ark_mcp.tools._asset_shared import collect_asset_ids, preflight_seedance_assets
from ark_mcp.tools._cost import log_cost_estimate
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._task_execution import context_log

_IMAGE_INPUT_EXAMPLE = (
    'Expected each image reference to be a URL string ("https://..." or '
    '"asset://<asset_id>"), {"url": "https://..."}, or {"kind": "url", '
    '"url": "https://...", "role": "reference_image"}.'
)
_AUDIO_INPUT_EXAMPLE = (
    'Expected each audio reference to be a URL string ("https://..." or '
    '"asset://<asset_id>"), {"url": "https://..."}, or {"kind": "url", "url": "https://..."}.'
)


class SeedanceImageInput(MediaSource):
    """Image input with an optional role for Seedance."""

    MEDIA_CATEGORY: ClassVar[MediaType] = MediaType.IMAGE
    ALLOW_ASSET_URI: ClassVar[bool] = True

    url: str | None = Field(default=None, description=ASSET_REFERENCE_URL_DESCRIPTION)
    role: Literal["first_frame", "last_frame", "reference_image"] | None = Field(
        None,
        description="Role of this image: first_frame, last_frame, or reference_image. If omitted, provider default applies.",
    )

    @model_validator(mode="before")
    @classmethod
    def coerce_image_reference(cls, data: object) -> object:
        """Accept a plain URL string or ``{"url": ...}`` shorthand.

        A bare string is coerced to ``kind="url"`` with the default
        ``reference_image`` role. A dict with a URL but no explicit ``kind``
        is normalized the same way. Unrecognized dicts raise a tailored error
        that shows the expected shape without echoing the caller's input.
        """
        if isinstance(data, str):
            return {"kind": "url", "url": data, "role": "reference_image"}
        if isinstance(data, dict) and "kind" not in data and "url" in data:
            return {"kind": "url", **data, "role": data.get("role", "reference_image")}
        if isinstance(data, dict) and "kind" not in data:
            raise ValueError(_IMAGE_INPUT_EXAMPLE)
        return data


class SeedanceVideoInput(BaseModel):
    """Video reference input for Seedance."""

    kind: Literal["url"] = Field(
        "url",
        description="Media source kind. Always 'url' for video references.",
    )
    url: str = Field(
        ...,
        description=(
            "HTTPS URL of the reference video, or 'asset://<asset_id>' for an Active private "
            "Video asset."
        ),
    )
    role: Literal["reference_video"] = Field(
        "reference_video",
        description="Role of this input. Always 'reference_video'.",
    )

    @model_validator(mode="before")
    @classmethod
    def coerce_video_reference(cls, data: object) -> object:
        """Accept a plain URL string for a video reference."""
        if isinstance(data, str):
            return {"url": data}
        return data

    @model_validator(mode="after")
    def validate_video_url(self) -> SeedanceVideoInput:
        validate_reference_url(
            self.url, allow_asset=True, log_event="seedance_video_invalid_source_url"
        )
        return self


class SeedanceAudioInput(MediaSource):
    """Audio reference input for Seedance."""

    MEDIA_CATEGORY: ClassVar[MediaType] = MediaType.AUDIO
    ALLOW_ASSET_URI: ClassVar[bool] = True

    url: str | None = Field(default=None, description=ASSET_REFERENCE_URL_DESCRIPTION)
    role: Literal["reference_audio"] = Field(
        "reference_audio",
        description="Role of this input. Always 'reference_audio'.",
    )

    @model_validator(mode="before")
    @classmethod
    def coerce_audio_reference(cls, data: object) -> object:
        """Accept a plain URL string or ``{"url": ...}`` shorthand."""
        if isinstance(data, str):
            return {"kind": "url", "url": data, "role": "reference_audio"}
        if isinstance(data, dict) and "kind" not in data and "url" in data:
            return {"kind": "url", **data}
        if isinstance(data, dict) and "kind" not in data:
            raise ValueError(_AUDIO_INPUT_EXAMPLE)
        return data


_EXTENSION_TASK_TYPE = "extend_video"


def strip_ratio_for_video_extension(
    omni_reference_task_type: str | None, ratio: str | None
) -> str | None:
    """Strip ``ratio`` for video extension tasks.

    Video extension tasks auto-lock ratio to the source video. The provider
    rejects any explicit ratio with ``InvalidParameter.TaskTypeConstraint``,
    so we strip it to prevent the failure.
    """
    if omni_reference_task_type == _EXTENSION_TASK_TYPE and ratio is not None:
        log_warning(
            "seedance_ratio_stripped_for_extension",
            task_type=_EXTENSION_TASK_TYPE,
            reason=(
                "ratio auto-locks to source for extend_video; "
                "setting it fails with InvalidParameter.TaskTypeConstraint"
            ),
        )
        return None
    return ratio


async def execute_seedance_create(
    input_model: Any,
    ctx: Context,
    caps: VideoCapabilities,
) -> tuple[str, str | None] | ToolResult:
    """Execute a Seedance task creation using the resolved capabilities.

    Shared by both 2.0 and 2.5 create handlers. Handles content building,
    request building, billing, retry, ownership recording, and logging.

    Args:
        input_model: The validated input model (SeedanceCreateTaskInput or
            Seedance25CreateTaskInput). Must have prompt, images, videos,
            audios, resolution, ratio, duration, omni_reference_task_type,
            generate_audio, watermark, return_last_frame,
            execution_expires_after, priority, safety_identifier fields.
        ctx: MCP Context.
        caps: Resolved VideoCapabilities for the target model.

    Returns:
        ``(task_id, request_id)`` on success, or a ``ToolResult`` on
        provider error.
    """
    await ctx.report_progress(progress=30, total=100)

    images_data = None
    videos_data = None
    audios_data = None

    if input_model.images:
        images_data = [img.model_dump() for img in input_model.images]
    if input_model.videos:
        videos_data = [vid.model_dump() for vid in input_model.videos]
    if input_model.audios:
        audios_data = [aud.model_dump() for aud in input_model.audios]

    # asset:// references pass through natively; stop early (before billing)
    # when a referenced asset we can see is still Processing or has Failed.
    await preflight_seedance_assets(ctx, collect_asset_ids(images_data, videos_data, audios_data))

    content = SeedanceService.build_content(
        prompt=input_model.prompt,
        images=images_data,
        videos=videos_data,
        audios=audios_data,
    )

    request = SeedanceService.build_request(
        model=caps.model_id,
        content=content,
        resolution=input_model.resolution,
        ratio=input_model.ratio,
        duration=input_model.duration,
        generate_audio=input_model.generate_audio,
        watermark=input_model.watermark,
        return_last_frame=input_model.return_last_frame,
        execution_expires_after=input_model.execution_expires_after,
        priority=input_model.priority,
        safety_identifier=input_model.safety_identifier,
        omni_reference_task_type=input_model.omni_reference_task_type,
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

    return task_id, request_id
