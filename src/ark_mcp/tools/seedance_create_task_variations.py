"""``seedance_create_task_variations`` tool — parallel video task creation.

Creates N independent Seedance video generation tasks in parallel. Seedance
2.0 does not support seeds. Each variation creates a separate task; the
caller polls each task ID via ``seedance_get_task``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import Context
from pydantic import BaseModel, Field, model_validator

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import ModelFamily, get_capability_registry
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.models import VariationError, VariationResult, VariationSummary
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import billed_provider_slot, get_principal, get_runtime
from ark_mcp.tools._cost import DEFAULT_MAX_CONCURRENT, estimate_cost, log_cost_estimate
from ark_mcp.tools._parallel import (
    VariationProgress,
    resolve_prompts,
    run_variation_batch,
    variation_batch_deadline,
)
from ark_mcp.tools._task_execution import context_log
from ark_mcp.tools.seedance_create_task import SeedanceCreateTaskInput


class SeedanceVariationsInput(SeedanceCreateTaskInput):
    """Input for parallel Seedance video task creation.

    Inherits all fields and validators from SeedanceCreateTaskInput
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
    def validate_prompt_required(self) -> SeedanceVariationsInput:
        if self.prompt is None and not self.variation_prompts:
            raise ValueError("Either prompt or variation_prompts must be provided.")
        return self

    @model_validator(mode="after")
    def validate_prompts_length(self) -> SeedanceVariationsInput:
        if self.variation_prompts and len(self.variation_prompts) != self.variations:
            raise ValueError(f"variation_prompts must have exactly {self.variations} entries")
        return self


class SeedanceVariationsOutput(BaseModel):
    """Output for parallel Seedance video task creation."""

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


async def seedance_create_task_variations(
    input: SeedanceVariationsInput, ctx: Context
) -> SeedanceVariationsOutput:
    """Create multiple Seedance video tasks in parallel.

    Each variation creates a separate task. The caller polls each task ID
    via ``seedance_get_task``. Partial failures are captured per variation.
    Requires MCP task-augmented execution for the provider submissions.
    """
    await context_log(ctx, "info", f"Starting {input.variations} parallel Seedance task creations")
    await ctx.report_progress(progress=10, total=100)

    settings = get_settings()
    if not settings.has_modelark:
        raise ValueError("BYTEPLUS_MODELARK_API_KEY is not configured.")

    registry = get_capability_registry()
    caps = registry.get_video_capabilities(input.model)

    if caps.family is ModelFamily.SEEDANCE_2_5:
        raise ValueError(
            f"Model '{input.model}' is a Seedance 2.5 model. "
            f"Use seedance_2_5_create_task for Seedance 2.5 models."
        )

    log_cost_estimate(
        product="video",
        variations=input.variations,
        model_id=caps.model_id,
    )

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
                f"outside the supported range [{lo}, {hi}]."
            )

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
                    product="video",
                    variations=1,
                    model_id=caps.model_id,
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
        "seedance_variations_complete",
        total=summary.total,
        succeeded=summary.succeeded,
        failed=summary.failed,
    )

    return SeedanceVariationsOutput(
        summary=summary,
        recommended_poll_after_ms=5000,
    )
