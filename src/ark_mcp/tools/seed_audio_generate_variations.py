"""``seed_audio_generate_variations`` tool — parallel audio generation.

Generates N independent audio variations in parallel. Seed Audio does not
support seeds, so variations rely on the stochastic nature of the model.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import uuid4

from fastmcp import Context
from pydantic import BaseModel, Field, model_validator

from ark_mcp.artifacts.store import ArtifactPersistenceError
from ark_mcp.config.env import get_settings
from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.media import AudioReference, ReferenceImageInput
from ark_mcp.domain.models import VariationError, VariationResult, VariationSummary
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.providers.seed_speech.seed_audio import SeedAudioService
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
    resolve_prompts,
    run_variation_batch,
    variation_batch_deadline,
)
from ark_mcp.tools._persistence import (
    persist_base64,
    persistence_variation_error,
    provider_url_ref,
)
from ark_mcp.tools._task_execution import context_log
from ark_mcp.tools.seed_audio_generate import AudioOutputOptions, AudioWatermarkOptions


class SeedAudioVariationsInput(BaseModel):
    """Input for parallel Seed Audio generation."""

    text_prompt: str | None = Field(
        None,
        min_length=1,
        max_length=3000,
        description="Base prompt. Required if variation_prompts is None.",
    )
    variations: int = Field(1, ge=1, le=5, description="Number of variations.")
    variation_prompts: list[str] | None = Field(None, description="Explicit prompts per variation.")
    audio_references: list[AudioReference] = Field(
        default_factory=list,
        max_length=3,
        description="Reference audio for voice cloning or scene control (max 3). Mutually exclusive with image_reference.",
    )
    image_reference: ReferenceImageInput | None = Field(
        None,
        description="Reference image for visual-guided audio generation. Mutually exclusive with audio_references.",
    )
    output: AudioOutputOptions | None = Field(
        None, description="Optional output format, sample rate, and rate controls."
    )
    watermark: AudioWatermarkOptions | None = Field(
        None, description="Optional AIGC watermark settings."
    )
    persist: bool = Field(
        True, description="Whether to persist generated audio as durable MCP resources."
    )
    output_dir: str | None = output_dir_field("each variation audio")
    overwrite: bool = overwrite_field()

    @model_validator(mode="after")
    def validate_prompt_required(self) -> SeedAudioVariationsInput:
        if self.text_prompt is None and not self.variation_prompts:
            raise ValueError("Either text_prompt or variation_prompts must be provided.")
        return self

    @model_validator(mode="after")
    def validate_no_media_mixing(self) -> SeedAudioVariationsInput:
        if self.audio_references and self.image_reference:
            raise ValueError("Audio and image references are mutually exclusive.")
        return self

    @model_validator(mode="after")
    def validate_prompts_length(self) -> SeedAudioVariationsInput:
        if self.variation_prompts and len(self.variation_prompts) != self.variations:
            raise ValueError(f"variation_prompts must have exactly {self.variations} entries")
        return self


class SeedAudioVariationsOutput(BaseModel):
    """Output for parallel Seed Audio generation."""

    provider: Literal["byteplus-seed-speech"] = Field(
        "byteplus-seed-speech", description="Provider that generated the audio."
    )
    model: Literal["seed-audio-1.0"] = Field(
        "seed-audio-1.0", description="Model used for generation."
    )
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
async def seed_audio_generate_variations(
    input: SeedAudioVariationsInput, ctx: Context
) -> SeedAudioVariationsOutput:
    """Generate multiple audio variations in parallel through Seed Speech.

    Seed Audio does not support seeds, so variations rely on the stochastic
    nature of the model. Partial failures are captured per variation. Requires
    MCP task-augmented execution for the parallel generations and persistence.
    """
    await context_log(ctx, "info", f"Starting {input.variations} parallel Seed Audio generations")
    await ctx.report_progress(progress=10, total=100)

    log_cost_estimate(product="audio", variations=input.variations, duration_seconds=15.0)

    settings = get_settings()
    if not settings.has_seed_audio:
        raise ValueError("BYTEPLUS_SEED_SPEECH_API_KEY is not configured.")

    prompts = resolve_prompts(input.text_prompt, input.variation_prompts, input.variations)

    runtime = get_runtime(ctx)
    store = runtime.artifact_store
    owner = get_principal(ctx)

    audio_refs_data: list[dict[str, Any]] | None = await resolve_asset_references(
        ctx,
        [ref.model_dump() for ref in input.audio_references] if input.audio_references else None,
        product="Seed Audio",
        expected_type="Audio",
    )
    image_refs = await resolve_asset_references(
        ctx,
        [input.image_reference.model_dump()] if input.image_reference else None,
        product="Seed Audio",
        expected_type="Image",
    )
    image_ref_data: dict[str, Any] | None = image_refs[0] if image_refs else None

    audio_config_dict: dict[str, Any] | None = (
        input.output.model_dump(exclude_none=True) if input.output else None
    )
    watermark_dict: dict[str, Any] | None = (
        input.watermark.model_dump(exclude_none=True) if input.watermark else None
    )

    service = SeedAudioService()

    async def _generate_single(idx: int, progress: VariationProgress) -> VariationResult:
        try:
            client_request_id = str(uuid4())
            references = SeedAudioService.build_references(
                audio_refs=audio_refs_data,
                image_ref=image_ref_data,
            )

            request = SeedAudioService.build_request(
                text_prompt=prompts[idx],
                references=references if references else None,
                audio_config=audio_config_dict,
                watermark=watermark_dict,
            )

            async with billed_provider_slot(
                ctx,
                provider="seed-speech",
                product="audio",
                estimated_cost_usd=estimate_cost(
                    product="audio", variations=1, duration_seconds=15.0
                ),
            ):
                progress.phase = "generating"
                response, log_id = await call_with_retry(
                    lambda: service.generate(request, request_id=client_request_id)
                )

            artifact: ArtifactRef | None = None
            source_expiry = (datetime.now(UTC) + timedelta(hours=2)).isoformat()

            if input.persist and response.audio:
                progress.phase = "persisting"
                artifact = await persist_base64(
                    store,
                    data=response.audio,
                    media_type=MediaType.AUDIO,
                    mime_type="audio/wav",
                    source_expires_at=source_expiry,
                    auth=owner,
                    provider_url=response.url,
                )
            elif response.url:
                artifact = provider_url_ref(
                    url=response.url,
                    media_type=MediaType.AUDIO,
                    mime_type="audio/wav",
                    source_expires_at=source_expiry,
                )

            return VariationResult(
                index=idx,
                artifact=artifact,
                request_id=client_request_id,
                provider_log_id=log_id,
            )
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
        except ArtifactPersistenceError as exc:
            return VariationResult(index=idx, error=persistence_variation_error(exc))
        except Exception as exc:
            return VariationResult(
                index=idx,
                error=VariationError(code="UNEXPECTED_ERROR", message=str(exc)),
            )

    try:
        summary = await run_variation_batch(
            count=input.variations,
            factory=_generate_single,
            batch_deadline=variation_batch_deadline(input.variations, settings, persists=True),
            max_concurrent=DEFAULT_MAX_CONCURRENT,
        )
    finally:
        await service.close()

    await ctx.report_progress(progress=100, total=100)
    log_info(
        "seed_audio_variations_complete",
        total=summary.total,
        succeeded=summary.succeeded,
        failed=summary.failed,
    )

    return SeedAudioVariationsOutput(summary=summary)
