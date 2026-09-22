"""``seed_understand`` tool — multimodal understanding through Seed 2.1 via ModelArk.

The handler validates media inputs, builds an OpenAI-compatible Chat Completions
request with image/video content parts, and returns the model's text answer
with optional chain-of-thought reasoning. The server registers the handler as
a required MCP background task and forces ``stream: false`` for the provider.
"""

from __future__ import annotations

from typing import ClassVar, Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, model_validator

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import get_capability_registry
from ark_mcp.domain.artifacts import MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.media import MediaSource
from ark_mcp.domain.models import UnderstandingChoice, UnderstandingUsage
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.understanding import SeedUnderstandingService
from ark_mcp.providers.retry import RetryPolicy, call_with_retry
from ark_mcp.runtime import billed_provider_slot
from ark_mcp.tools._cost import log_cost_estimate
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._task_execution import context_log

# Chat timeouts are safe to retry but are not auto-retried: a second full-length
# thinking run would double latency and token cost. 429/5xx are still retried.
_UNDERSTANDING_RETRY_POLICY = RetryPolicy(retry_timeouts=False)


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


class SeedUnderstandInput(BaseModel):
    """Input model for ``seed_understand``."""

    prompt: str = Field(
        ...,
        min_length=1,
        max_length=32000,
        description="The question or task for the model to reason about.",
    )
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
    system: str | None = Field(
        None,
        max_length=32000,
        description="Optional system instruction to guide the model's behavior.",
    )
    model: str | None = Field(
        None,
        description="Override the configured Seed 2.1 model ID. Must be present in the capability registry.",
    )
    thinking: bool = Field(
        False,
        description="Enable deep-thinking (chain-of-thought) reasoning. When true, the response includes reasoning_content.",
    )
    reasoning_effort: Literal["low", "medium", "high"] | None = Field(
        None,
        description="Reasoning effort level. Only applies when thinking=true.",
    )
    temperature: float | None = Field(
        None,
        ge=0.0,
        le=2.0,
        description="Sampling temperature (0.0-2.0). Lower is more deterministic.",
    )
    max_tokens: int | None = Field(
        None,
        ge=1,
        le=32768,
        description="Maximum output tokens (1-32768).",
    )
    top_p: float | None = Field(
        None,
        ge=0.0,
        le=1.0,
        description="Nucleus sampling probability (0.0-1.0).",
    )
    repetition_penalty: float | None = Field(
        None,
        ge=0.0,
        le=2.0,
        description="Repetition penalty (0.0-2.0). Ark-only parameter.",
    )


class SeedUnderstandOutput(BaseModel):
    """Output model for ``seed_understand``."""

    provider: Literal["byteplus-modelark"] = Field(
        "byteplus-modelark", description="Provider that generated the response."
    )
    model: str = Field(..., description="Model ID used for the completion.")
    completion_id: str | None = Field(
        None,
        description="Provider completion ID (e.g. 'chatcmpl-...') for tracing.",
    )
    choices: list[UnderstandingChoice] = Field(
        ..., description="Model completion choices (one for non-streaming)."
    )
    usage: UnderstandingUsage = Field(..., description="Token usage for this completion.")
    request_id: str | None = Field(None, description="Provider request ID for support tracing.")


async def seed_understand(
    input: SeedUnderstandInput, ctx: Context
) -> SeedUnderstandOutput | ToolResult:
    """Understand images and videos, or reason about a task, through the Seed 2.1 multimodal model.

    Accepts a natural-language prompt plus optional images and videos, and
    returns the model's text answer. Supports deep-thinking reasoning. Use
    this for video understanding, image understanding, and as a multimodal
    reasoning sub-agent. For local media files, upload them first with
    media_upload to obtain an HTTPS URL; video Base64 is not supported by the
    chat endpoint. This tool requires task-augmented execution so long video
    analysis does not consume a foreground MCP request deadline.
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

    if input.thinking and not caps.supports_thinking:
        raise ValueError(f"Model '{caps.model_id}' does not support thinking/reasoning mode.")

    if input.reasoning_effort and input.reasoning_effort not in caps.reasoning_efforts:
        raise ValueError(
            f"Model '{caps.model_id}' supports reasoning_effort values "
            f"{caps.reasoning_efforts}, got '{input.reasoning_effort}'."
        )

    await ctx.report_progress(progress=30, total=100)

    request = SeedUnderstandingService.build_request(
        model=caps.model_id,
        prompt=input.prompt,
        image_parts=[src.model_dump() for src in input.images] if input.images else None,
        video_parts=[src.model_dump() for src in input.videos] if input.videos else None,
        system=input.system,
        thinking=input.thinking,
        reasoning_effort=input.reasoning_effort,
        temperature=input.temperature,
        max_tokens=input.max_tokens,
        top_p=input.top_p,
        repetition_penalty=input.repetition_penalty,
    )

    await ctx.report_progress(progress=50, total=100)

    estimated_cost = log_cost_estimate(
        product="understanding",
        variations=1,
        max_tokens=input.max_tokens,
    )

    service = SeedUnderstandingService()
    try:
        async with billed_provider_slot(
            ctx,
            provider="modelark",
            product="understanding",
            estimated_cost_usd=estimated_cost,
        ):
            response, request_id = await call_with_retry(
                lambda: service.generate(request),
                policy=_UNDERSTANDING_RETRY_POLICY,
            )
    except ProviderError as exc:
        await context_log(ctx, "error", f"Understanding failed: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    await ctx.report_progress(progress=80, total=100)

    usage = SeedUnderstandingService.extract_usage(response)
    completion_id = SeedUnderstandingService.extract_completion_id(response)

    choices: list[UnderstandingChoice] = []
    for choice in response.choices:
        content = choice.message.content or ""
        choices.append(
            UnderstandingChoice(
                content=content,
                reasoning_content=choice.message.reasoning_content,
                finish_reason=choice.finish_reason or "stop",
            )
        )

    await ctx.report_progress(progress=100, total=100)
    log_info(
        "understanding_complete",
        model=caps.model_id,
        choices=len(choices),
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        request_id=request_id,
    )

    return SeedUnderstandOutput(
        model=caps.model_id,
        completion_id=completion_id,
        choices=choices,
        usage=UnderstandingUsage(
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
        ),
        request_id=request_id,
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
