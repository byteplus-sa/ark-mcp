"""``seedance_2_5_create_task_variations`` tool — parallel Seedance 2.5 video task creation.

Creates N independent Seedance 2.5 video generation tasks in parallel.
Each variation creates a separate task; the caller polls each task ID
via ``seedance_get_task``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context
from pydantic import BaseModel, Field, model_validator

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import ModelFamily
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.models import VariationError, VariationResult, VariationSummary
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import billed_provider_slot, get_principal, get_runtime
from ark_mcp.tools._asset_shared import collect_asset_ids, preflight_seedance_assets
from ark_mcp.tools._cost import DEFAULT_MAX_CONCURRENT, estimate_cost, log_cost_estimate
from ark_mcp.tools._parallel import (
    VariationProgress,
    resolve_prompts,
    run_variation_batch,
    variation_batch_deadline,
)
from ark_mcp.tools._task_execution import context_log
from ark_mcp.tools.seedance_2_5_create_task import (
    Seedance25CreateTaskInput,
    resolve_seedance_2_5_capabilities,
)


class Seedance25VariationsInput(Seedance25CreateTaskInput):
    """Input for parallel Seedance 2.5 video task creation.

    Inherits all fields and validators from Seedance25CreateTaskInput
    (prompt, images, videos, audios, model, resolution, duration, etc.)
    and adds variations-specific fields.
    """

    prompt: str | None = Field(
        None,
        min_length=1,
        max_length=32000,
        description="Base prompt for all variations (1-32,000 characters). Required if variation_prompts is None.",
    )
    variations: int = Field(1, ge=1, le=5, description="Number of variations.")
    variation_prompts: list[Annotated[str, Field(min_length=1, max_length=32000)]] | None = Field(
        None,
        description="Explicit prompts per variation (each 1-32,000 characters). If provided, overrides prompt and must have `variations` entries.",
    )

    @model_validator(mode="after")
    def validate_prompt_required(self) -> Seedance25VariationsInput:
        if self.prompt is None and not self.variation_prompts:
            raise ValueError("Either prompt or variation_prompts must be provided.")
        return self

    @model_validator(mode="after")
    def validate_prompts_length(self) -> Seedance25VariationsInput:
        if self.variation_prompts and len(self.variation_prompts) != self.variations:
            raise ValueError(f"variation_prompts must have exactly {self.variations} entries")
        return self


class Seedance25VariationsOutput(BaseModel):
    """Output for parallel Seedance 2.5 video task creation."""

    summary: VariationSummary = Field(
        ..., description="Aggregate result with per-variation task IDs and errors."
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


async def seedance_2_5_create_task_variations(
    input: Seedance25VariationsInput, ctx: Context
) -> Seedance25VariationsOutput:
    """Create multiple Seedance 2.5 video tasks in parallel.

    Each variation creates a separate task. The caller polls each task ID
    via ``seedance_get_task``. Partial failures are captured per variation.
    Requires MCP task-augmented execution for the provider submissions.
    """
    return await run_seedance_2_5_variations(input, ctx, ModelFamily.SEEDANCE_2_5)


async def run_seedance_2_5_variations(
    input: Seedance25VariationsInput, ctx: Context, family: ModelFamily
) -> Seedance25VariationsOutput:
    """Run a parallel variation batch for a Seedance 2.5-generation family."""
    label = "Seedance 2.5 Premium" if family is ModelFamily.SEEDANCE_2_5_PREMIUM else "Seedance 2.5"
    await context_log(ctx, "info", f"Starting {input.variations} parallel {label} task creations")
    await ctx.report_progress(progress=10, total=100)

    settings = get_settings()
    if not settings.has_modelark:
        raise ValueError("BYTEPLUS_MODELARK_API_KEY is not configured.")

    caps = resolve_seedance_2_5_capabilities(input, family)

    log_cost_estimate(product="video", variations=input.variations, model_id=caps.model_id)

    prompts = resolve_prompts(input.prompt, input.variation_prompts, input.variations)

    images_data: list[dict[str, Any]] | None = (
        [img.model_dump() for img in input.images] if input.images else None
    )
    videos_data: list[dict[str, Any]] | None = (
        [vid.model_dump() for vid in input.videos] if input.videos else None
    )
    audios_data: list[dict[str, Any]] | None = (
        [aud.model_dump() for aud in input.audios] if input.audios else None
    )

    # Stop before billing when a referenced asset is still Processing or Failed.
    await preflight_seedance_assets(ctx, collect_asset_ids(images_data, videos_data, audios_data))

    service = SeedanceService()

    async def _create_single(idx: int, progress: VariationProgress) -> VariationResult:
        try:
            content = SeedanceService.build_content(
                prompt=prompts[idx],
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

            async with billed_provider_slot(
                ctx,
                provider="modelark",
                product="video",
                estimated_cost_usd=estimate_cost(
                    product="video", variations=1, model_id=caps.model_id
                ),
            ):
                progress.phase = "generating"
                task_id, request_id = await call_with_retry(lambda: service.create_task(request))
            await get_runtime(ctx).ownership_store.record("modelark", task_id, get_principal(ctx))

            return VariationResult(index=idx, task_id=task_id, request_id=request_id)
        except ProviderError as exc:
            return VariationResult(
                index=idx,
                error=VariationError(
                    code=exc.code or "PROVIDER_ERROR",
                    message=exc.message,
                    request_id=exc.request_id,
                    retryable=exc.retryable,
                    ambiguous_completion=bool(exc.ambiguous_completion),
                ),
                request_id=exc.request_id,
            )
        except Exception as exc:
            return VariationResult(
                index=idx,
                error=VariationError(code="UNEXPECTED_ERROR", message=str(exc)),
            )

    try:
        summary = await run_variation_batch(
            count=input.variations,
            factory=_create_single,
            batch_deadline=variation_batch_deadline(input.variations, settings, persists=False),
            max_concurrent=DEFAULT_MAX_CONCURRENT,
        )
    finally:
        await service.close()

    await ctx.report_progress(progress=100, total=100)
    log_info(
        "seedance_2_5_variations_complete",
        family=str(family),
        total=summary.total,
        succeeded=summary.succeeded,
        failed=summary.failed,
    )

    return Seedance25VariationsOutput(
        summary=summary,
        recommended_poll_after_ms=5000,
    )
