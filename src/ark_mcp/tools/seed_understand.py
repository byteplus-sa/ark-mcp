"""``seed_understand`` tool — multimodal understanding through Seed 2.1 via ModelArk.

The handler validates media inputs, builds an OpenAI-compatible Chat Completions
request with image/video content parts, and returns only the model's final
answer. Deep thinking is always enabled (depth set by ``reasoning_effort``);
the reasoning trace is discarded and never returned, logged, or saved.
Optional ``response_format`` enforces JSON or a JSON Schema at generation
time, and ``save_to`` writes the answer straight to a local file. The server
registers the handler as a required MCP background task and forces
``stream: false`` for the provider. The completion loop is shared with
``seed_audio_understand`` in ``_understanding_shared``.
"""

from __future__ import annotations

from typing import ClassVar

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import Field, model_validator

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import get_capability_registry
from ark_mcp.domain.artifacts import MediaType
from ark_mcp.domain.media import MediaSource
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.tools._task_execution import context_log
from ark_mcp.tools._understanding_shared import (
    SeedUnderstandOutput,
    UnderstandingJsonSchema,
    UnderstandingOptions,
    UnderstandingResponseFormat,
    run_understanding,
)

__all__ = [
    "TOOL_ANNOTATIONS",
    "SeedUnderstandInput",
    "SeedUnderstandOutput",
    "UnderstandingImageInput",
    "UnderstandingJsonSchema",
    "UnderstandingResponseFormat",
    "UnderstandingVideoInput",
    "seed_understand",
]


class UnderstandingImageInput(MediaSource):
    """Image input for multimodal understanding."""

    MEDIA_CATEGORY: ClassVar[MediaType] = MediaType.IMAGE


class UnderstandingVideoInput(MediaSource):
    """Video input for multimodal understanding. URL only — Base64 is not supported."""

    MEDIA_CATEGORY: ClassVar[MediaType] = MediaType.VIDEO

    @model_validator(mode="before")
    @classmethod
    def reject_video_base64(cls, data: object) -> object:
        if isinstance(data, dict) and data.get("kind") == "base64":
            raise ValueError(
                "Video Base64 is not supported by the chat endpoint; "
                "upload via media_upload and pass a URL."
            )
        return data


class SeedUnderstandInput(UnderstandingOptions):
    """Input model for ``seed_understand``."""

    images: list[UnderstandingImageInput] | None = Field(
        None,
        max_length=32,
        description="Images to understand (URL or Base64). For local files, upload via media_upload first.",
    )
    videos: list[UnderstandingVideoInput] | None = Field(
        None,
        max_length=32,
        description="Videos to understand. Must be HTTPS URLs — video Base64 is not supported.",
    )
    model: str | None = Field(
        None,
        description="Override the configured Seed 2.1 model ID. Must be present in the capability registry.",
    )
    thinking: bool | None = Field(
        None,
        description=(
            "Deprecated and ignored. Deep thinking is always enabled; use reasoning_effort to "
            "control its depth. The reasoning trace is never returned. Will be removed."
        ),
    )


async def seed_understand(
    input: SeedUnderstandInput, ctx: Context
) -> SeedUnderstandOutput | ToolResult:
    """Understand images and videos, or reason about a task, through the Seed 2.1 multimodal model.

    Accepts a natural-language prompt plus optional images and videos, and
    returns only the model's final answer. The model always uses deep thinking
    (depth set by reasoning_effort, default 'medium'), but the reasoning trace is
    never returned. Use response_format to force JSON or a JSON Schema at
    generation time (parsed result in choices[].parsed), and save_to to write the
    answer straight to a local file. For local media files, upload them first with
    media_upload to obtain an HTTPS URL; video Base64 is not supported by the chat
    endpoint. This tool requires task-augmented execution so long video analysis
    does not consume a foreground MCP request deadline.
    """
    await context_log(ctx, "info", "Starting Seed 2.1 multimodal understanding")
    await ctx.report_progress(progress=10, total=100)

    settings = get_settings()
    if not settings.has_understanding:
        raise ValueError(
            "BYTEPLUS_MODELARK_API_KEY is not configured. Set it in .env to enable understanding tools."
        )

    registry = get_capability_registry()
    caps = registry.get_understanding_capabilities(input.model)

    image_count = len(input.images or [])
    video_count = len(input.videos or [])
    total_media = image_count + video_count
    if total_media > caps.max_media_parts:
        raise ValueError(
            f"Model '{caps.model_id}' supports at most {caps.max_media_parts} "
            f"media parts, got {total_media}."
        )

    if input.thinking is False:
        log_warning("thinking_flag_ignored", model=caps.model_id)
        await context_log(
            ctx, "warning", "thinking=false is deprecated and ignored; deep thinking is always on."
        )

    if caps.supports_thinking and input.reasoning_effort not in caps.reasoning_efforts:
        raise ValueError(
            f"Model '{caps.model_id}' supports reasoning_effort values "
            f"{caps.reasoning_efforts}, got '{input.reasoning_effort}'."
        )

    if input.response_format and input.response_format.type not in caps.response_formats:
        raise ValueError(
            f"Model '{caps.model_id}' does not support response_format "
            f"'{input.response_format.type}'; supported: {caps.response_formats}."
        )

    return await run_understanding(
        input,
        ctx,
        settings=settings,
        model_id=caps.model_id,
        supports_thinking=caps.supports_thinking,
        output_cls=SeedUnderstandOutput,
        image_parts=[src.model_dump() for src in input.images] if input.images else None,
        video_parts=[src.model_dump() for src in input.videos] if input.videos else None,
    )


# save_to writes local files, so the tool is not read-only.
TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
