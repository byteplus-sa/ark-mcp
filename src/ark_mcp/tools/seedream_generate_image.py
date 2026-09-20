"""``seedream_generate_image`` tool — image generation and editing through ModelArk.

The handler derives ``sequential_image_generation`` from ``max_images``,
forces ``stream: false`` for MVP, and validates model-specific features.
For example, Pro rejects batch/streaming fields, and 4.x rejects
``output_format`` until the API-reference/tutorial conflict is resolved.
"""

from __future__ import annotations

from datetime import UTC
from typing import Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import get_capability_registry
from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.media import MediaSource
from ark_mcp.domain.models import SeedreamItemError, SeedreamUsage
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.seedream import SeedreamService
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import billed_provider_slot, get_principal, get_runtime
from ark_mcp.tools._cost import log_cost_estimate
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._task_execution import context_log

# ---------------------------------------------------------------------------
# Input / Output models
# ---------------------------------------------------------------------------


class SeedreamGenerateInput(BaseModel):
    """Input model for ``seedream_generate_image``."""

    prompt: str = Field(
        ...,
        min_length=1,
        max_length=4000,
        description="Text prompt describing the image to generate (1-4,000 characters).",
    )
    images: list[MediaSource] | None = Field(
        None,
        description="Reference images for image-to-image or editing. Count limited by model capabilities.",
    )
    model: str | None = Field(
        None,
        description=(
            "Model ID. Available: 'dola-seedream-5-0-pro-260628' (Pro, default, no batch, 10 refs, PNG/JPEG). "
            "Lite and 4x model IDs are configured via SEEDREAM_MODEL_BINDINGS. Omit to use the default."
        ),
    )
    size: str | None = Field(
        None,
        description="Output image size (e.g. '1024x1024', '2048x1152'). Must be valid for the selected model.",
    )
    seed: int | None = Field(
        None, ge=-1, le=2147483647, description="Random seed. -1 = random, 0+ = fixed."
    )
    max_images: int | None = Field(
        None,
        ge=1,
        le=15,
        description="Number of images to generate in a single call (1-15). Only supported by Lite and 4.x models. Pro models are limited to 1.",
    )
    output_format: Literal["png", "jpeg"] | None = Field(
        None, description="Output image format. Not supported by 4.x models."
    )
    response_format: Literal["url", "b64_json"] | None = Field(
        None, description="Response format for generated images: URL or Base64 JSON."
    )
    watermark: bool | None = Field(
        None, description="Whether to apply an AIGC watermark to generated images."
    )
    prompt_optimization: Literal["standard", "fast"] | None = Field(
        None,
        description=(
            "Prompt optimization mode: standard (higher quality) or fast (lower latency). "
            "When omitted, Seedream 5.0 Pro uses the server-configured default "
            "(SEEDREAM_PROMPT_OPTIMIZATION_MODE, standard unless the operator changes "
            "it); other models use the provider default. Pass fast to prioritize lower latency."
        ),
    )
    persist: bool = Field(
        True, description="Whether to persist generated images as durable MCP resources."
    )


class SeedreamGenerateOutput(BaseModel):
    """Output model for ``seedream_generate_image``."""

    provider: Literal["byteplus-modelark"] = Field(
        "byteplus-modelark", description="Provider that generated the images."
    )
    model: str = Field(..., description="Model ID used for generation.")
    created_at: str = Field(..., description="ISO-8601 timestamp of generation.")
    artifacts: list[ArtifactRef] = Field(
        ..., description="Durable artifact references for generated images."
    )
    item_errors: list[SeedreamItemError] = Field(
        ..., description="Per-image errors for any failed items in a batch."
    )
    usage: SeedreamUsage = Field(..., description="Token usage for this generation.")


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------


async def seedream_generate_image(
    input: SeedreamGenerateInput, ctx: Context
) -> SeedreamGenerateOutput | ToolResult:
    """Generate or edit an image through ModelArk Seedream.

    Supports text-to-image and reference-based editing. The ``max_images``
    parameter enables batch generation for models that support it (Lite,
    4.x). Pro models are limited to single-image generation. Requires MCP
    task-augmented execution so generation and persistence run in the background.
    """
    await context_log(ctx, "info", "Starting Seedream image generation")
    await ctx.report_progress(progress=10, total=100)

    settings = get_settings()
    if not settings.has_modelark:
        raise ValueError(
            "BYTEPLUS_MODELARK_API_KEY is not configured. Set it in .env to enable Seedream tools."
        )

    registry = get_capability_registry()
    caps = registry.get_image_capabilities(input.model)

    # Validate model-specific features.
    if input.max_images and input.max_images > 1 and not caps.supports_batch:
        raise ValueError(
            f"Model '{caps.model_id}' does not support batch generation. "
            f"Set max_images to 1 or omit it."
        )

    if len(input.images or []) > caps.max_references:
        raise ValueError(
            f"Model '{caps.model_id}' supports at most {caps.max_references} "
            f"reference images, got {len(input.images or [])}."
        )

    registry.validate_output_format(input.model, input.output_format)
    registry.validate_image_size(input.model, input.size)

    await ctx.report_progress(progress=30, total=100)

    # Build the provider request.
    images_data = None
    if input.images:
        images_data = [src.model_dump() for src in input.images]

    request = SeedreamService.build_request(
        model=caps.model_id,
        prompt=input.prompt,
        images=images_data,
        size=input.size,
        seed=input.seed,
        max_images=input.max_images,
        output_format=input.output_format,
        response_format=input.response_format,
        watermark=input.watermark,
        prompt_optimization=caps.resolve_prompt_optimization(input.prompt_optimization),
    )

    await ctx.report_progress(progress=50, total=100)

    estimated_cost = log_cost_estimate(product="image", variations=input.max_images or 1)

    service = SeedreamService()
    try:
        async with billed_provider_slot(
            ctx,
            provider="modelark",
            product="image",
            estimated_cost_usd=estimated_cost,
        ):
            response, request_id = await call_with_retry(lambda: service.generate(request))
    except ProviderError as exc:
        await context_log(ctx, "error", f"Seedream generation failed: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    await ctx.report_progress(progress=80, total=100)

    # Persist artifacts.
    artifacts: list[ArtifactRef] = []
    if input.persist:
        store = get_runtime(ctx).artifact_store
        owner = get_principal(ctx)
        from datetime import datetime, timedelta

        source_expiry = (datetime.now(UTC) + timedelta(hours=24)).isoformat()

        for item in response.data:
            mime_type = f"image/{item.output_format or input.output_format or 'jpeg'}"
            if item.b64_json:
                ref = await store.put_base64(
                    data=item.b64_json,
                    media_type=MediaType.IMAGE,
                    mime_type=mime_type,
                    source_expires_at=source_expiry,
                    auth=owner,
                )
                artifacts.append(ref)
            elif item.url:
                ref = await store.copy_from_trusted_url(
                    url=item.url,
                    media_type=MediaType.IMAGE,
                    mime_type=mime_type,
                    source_expires_at=source_expiry,
                    auth=owner,
                )
                artifacts.append(ref)
    else:
        # Return unpersisted references to provider URLs (will expire).
        from datetime import datetime, timedelta

        source_expiry = (datetime.now(UTC) + timedelta(hours=24)).isoformat()
        for item in response.data:
            if item.url:
                artifacts.append(
                    ArtifactRef(
                        id="provider-url",
                        uri=item.url,
                        media_type=MediaType.IMAGE,
                        mime_type=f"image/{item.output_format or input.output_format or 'jpeg'}",
                        created_at=datetime.now(UTC).isoformat(),
                        source_expires_at=source_expiry,
                    )
                )

    await ctx.report_progress(progress=100, total=100)
    log_info(
        "seedream_complete",
        model=caps.model_id,
        artifacts_count=len(artifacts),
        request_id=request_id,
    )

    return SeedreamGenerateOutput(
        model=caps.model_id,
        created_at=SeedreamService.get_created_at(response),
        artifacts=artifacts,
        item_errors=SeedreamService.extract_item_errors(response),
        usage=SeedreamService.extract_usage(response),
    )


# Tool annotation constants — camelCase per MCP specification.
TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
