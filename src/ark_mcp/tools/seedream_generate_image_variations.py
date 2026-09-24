"""``seedream_generate_image_variations`` tool — parallel image generation.

Generates N independent image variations in parallel using ``asyncio.gather``.
Each variation gets a distinct seed (when supported) and may use its own
prompt (via ``variation_prompts``). Partial failures are captured per
variation — one bad variation does not fail the batch.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastmcp import Context
from pydantic import BaseModel, Field, model_validator

from ark_mcp.artifacts.store import ArtifactPersistenceError
from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import get_capability_registry
from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.media import ReferenceImageInput
from ark_mcp.domain.models import VariationError, VariationResult, VariationSummary
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.seedream import SeedreamService
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import billed_provider_slot, get_principal, get_runtime
from ark_mcp.tools._asset_shared import resolve_asset_references
from ark_mcp.tools._cost import DEFAULT_MAX_CONCURRENT, estimate_cost, log_cost_estimate
from ark_mcp.tools._local_export import (
    local_export,
    output_dir_field,
    overwrite_field,
)
from ark_mcp.tools._local_export import (
    needs_persist as _needs_persist,
)
from ark_mcp.tools._parallel import (
    VariationProgress,
    generate_seeds,
    resolve_prompts,
    run_variation_batch,
    variation_batch_deadline,
)
from ark_mcp.tools._persistence import (
    persist_base64,
    persist_from_url,
    persistence_issue,
    persistence_variation_error,
    provider_url_ref,
)
from ark_mcp.tools._task_execution import context_log


class SeedreamVariationsInput(BaseModel):
    """Input for parallel Seedream image generation."""

    prompt: str | None = Field(
        None,
        min_length=1,
        max_length=4000,
        description="Base prompt for all variations (1-4,000 characters). Required if variation_prompts is None.",
    )
    variations: int = Field(1, ge=1, le=10, description="Number of variations.")
    variation_prompts: list[Annotated[str, Field(min_length=1, max_length=4000)]] | None = Field(
        None,
        description="Explicit prompts per variation (each 1-4,000 characters). If provided, overrides prompt and must have `variations` entries.",
    )
    base_seed: int | None = Field(
        None,
        ge=-1,
        le=2147483647,
        description="Base seed. None = provider-randomized. -1 = client-randomized. N = deterministic (N+i per variation).",
    )
    images: list[ReferenceImageInput] | None = Field(
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
    output_dir: str | None = output_dir_field("each variation image")
    overwrite: bool = overwrite_field()

    @model_validator(mode="after")
    def validate_prompt_required(self) -> SeedreamVariationsInput:
        if self.prompt is None and not self.variation_prompts:
            raise ValueError("Either prompt or variation_prompts must be provided.")
        return self

    @model_validator(mode="after")
    def validate_prompts_length(self) -> SeedreamVariationsInput:
        if self.variation_prompts and len(self.variation_prompts) != self.variations:
            raise ValueError(
                f"variation_prompts must have exactly {self.variations} entries, "
                f"got {len(self.variation_prompts)}"
            )
        return self


class SeedreamVariationsOutput(BaseModel):
    """Output for parallel Seedream image generation."""

    provider: Literal["byteplus-modelark"] = Field(
        "byteplus-modelark", description="Provider that generated the images."
    )
    model: str = Field(..., description="Model ID used for generation.")
    created_at: str = Field(..., description="ISO-8601 timestamp of generation.")
    summary: VariationSummary = Field(
        ..., description="Aggregate result with per-variation artifacts and errors."
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}


@local_export("summary", precondition=_needs_persist)
async def seedream_generate_image_variations(
    input: SeedreamVariationsInput, ctx: Context
) -> SeedreamVariationsOutput:
    """Generate multiple image variations in parallel through ModelArk Seedream.

    Each variation is an independent generation with its own seed (when
    supported). Partial failures are captured — one bad variation does not
    fail the batch. Requires MCP task-augmented execution so the batch runs in
    the background.
    """
    await context_log(ctx, "info", f"Starting {input.variations} parallel Seedream generations")
    await ctx.report_progress(progress=10, total=100)

    log_cost_estimate(product="image", variations=input.variations)

    settings = get_settings()
    if not settings.has_modelark:
        raise ValueError("BYTEPLUS_MODELARK_API_KEY is not configured.")

    registry = get_capability_registry()
    caps = registry.get_image_capabilities(input.model)

    if len(input.images or []) > caps.max_references:
        raise ValueError(
            f"Model '{caps.model_id}' supports at most {caps.max_references} "
            f"reference images, got {len(input.images or [])}."
        )
    registry.validate_output_format(input.model, input.output_format)
    registry.validate_image_size(input.model, input.size)

    seeds = generate_seeds(input.base_seed, input.variations)
    prompts = resolve_prompts(input.prompt, input.variation_prompts, input.variations)

    runtime = get_runtime(ctx)
    store = runtime.artifact_store
    owner = get_principal(ctx)

    images_data: list[dict[str, Any]] | None = await resolve_asset_references(
        ctx,
        [src.model_dump() for src in input.images] if input.images else None,
        product="Seedream",
        expected_type="Image",
    )

    service = SeedreamService()

    async def _generate_single(idx: int, progress: VariationProgress) -> VariationResult:
        try:
            request = SeedreamService.build_request(
                model=caps.model_id,
                prompt=prompts[idx],
                images=images_data,
                size=input.size,
                seed=seeds[idx],
                output_format=input.output_format,
                response_format=input.response_format,
                watermark=input.watermark,
                prompt_optimization=caps.resolve_prompt_optimization(input.prompt_optimization),
            )

            async with billed_provider_slot(
                ctx,
                provider="modelark",
                product="image",
                estimated_cost_usd=estimate_cost(product="image", variations=1),
            ):
                progress.phase = "generating"
                response, request_id = await call_with_retry(lambda: service.generate(request))

            artifact: ArtifactRef | None = None
            source_expiry = (datetime.now(UTC) + timedelta(hours=24)).isoformat()

            if response.data:
                item = response.data[0]
                mime = f"image/{item.output_format or input.output_format or 'jpeg'}"
                if item.url:
                    progress.partial = VariationResult(
                        index=idx,
                        seed=seeds[idx],
                        artifact=provider_url_ref(
                            url=item.url,
                            media_type=MediaType.IMAGE,
                            mime_type=mime,
                            source_expires_at=source_expiry,
                            # Only surfaced if the batch deadline cancels this
                            # variation mid-persist, so it must already say the
                            # output is unpersisted.
                            issue=persistence_issue(
                                ArtifactPersistenceError(
                                    "storage_failed",
                                    "Persistence did not finish before the batch deadline.",
                                    retryable=True,
                                ),
                                MediaType.IMAGE,
                                source_url_expires_at=source_expiry,
                            ),
                        ),
                        request_id=request_id,
                    )
                if input.persist:
                    progress.phase = "persisting"
                    if item.b64_json:
                        artifact = await persist_base64(
                            store,
                            data=item.b64_json,
                            media_type=MediaType.IMAGE,
                            mime_type=mime,
                            source_expires_at=source_expiry,
                            auth=owner,
                            provider_url=item.url,
                        )
                    elif item.url:
                        artifact = await persist_from_url(
                            store,
                            url=item.url,
                            media_type=MediaType.IMAGE,
                            mime_type=mime,
                            source_expires_at=source_expiry,
                            auth=owner,
                        )
                elif item.url:
                    artifact = provider_url_ref(
                        url=item.url,
                        media_type=MediaType.IMAGE,
                        mime_type=mime,
                        source_expires_at=source_expiry,
                    )

            return VariationResult(
                index=idx,
                seed=seeds[idx],
                artifact=artifact,
                request_id=request_id,
            )
        except ProviderError as exc:
            return VariationResult(
                index=idx,
                seed=seeds[idx],
                error=VariationError(
                    code=exc.code or "PROVIDER_ERROR",
                    message=exc.message,
                    request_id=exc.request_id,
                    retryable=exc.retryable,
                    ambiguous_completion=bool(exc.ambiguous_completion),
                ),
                request_id=exc.request_id,
            )
        except ArtifactPersistenceError as exc:
            return VariationResult(
                index=idx,
                seed=seeds[idx],
                error=persistence_variation_error(exc),
            )
        except Exception as exc:
            return VariationResult(
                index=idx,
                seed=seeds[idx],
                error=VariationError(code="UNEXPECTED_ERROR", message=str(exc)),
            )

    try:
        summary = await run_variation_batch(
            count=input.variations,
            factory=_generate_single,
            batch_deadline=variation_batch_deadline(input.variations, settings),
            max_concurrent=DEFAULT_MAX_CONCURRENT,
        )
    finally:
        await service.close()

    await ctx.report_progress(progress=100, total=100)
    log_info(
        "seedream_variations_complete",
        total=summary.total,
        succeeded=summary.succeeded,
        failed=summary.failed,
    )

    return SeedreamVariationsOutput(
        model=caps.model_id,
        created_at=datetime.now(UTC).isoformat(),
        summary=summary,
    )
