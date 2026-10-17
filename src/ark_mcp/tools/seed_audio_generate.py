"""``seed_audio_generate`` tool — full-scene audio generation through Seed Speech.

Input validation uses a Pydantic model validator to reject image+audio
mixing, more than three references, invalid MIME types, and out-of-range
controls. The adapter maps discriminated unions to the provider's ``speaker``,
``audio_url``, ``audio_data``, ``image_url``, or ``image_data`` fields.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, model_validator

from ark_mcp.artifacts.store import ArtifactPersistenceError
from ark_mcp.config.env import get_settings
from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.media import AudioReference, ReferenceImageInput
from ark_mcp.domain.models import Subtitle, VariationError, VariationResult, VariationSummary
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.providers.seed_speech.seed_audio import SeedAudioService
from ark_mcp.runtime import billed_provider_slot, get_principal, get_runtime
from ark_mcp.tools._asset_shared import resolve_asset_references
from ark_mcp.tools._cost import DEFAULT_MAX_CONCURRENT, estimate_cost, log_cost_estimate
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._local_export import (
    local_export,
    output_path_field,
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
from ark_mcp.tools._persistence import persist_base64, persistence_variation_error, provider_url_ref
from ark_mcp.tools._task_execution import context_log

# ---------------------------------------------------------------------------
# Input / Output models
# ---------------------------------------------------------------------------


MAX_VARIATIONS = 5


class AudioOutputOptions(BaseModel):
    """Optional output controls for Seed Audio generation."""

    format: Literal["wav", "mp3", "pcm", "ogg"] | None = Field(
        None, description="Output audio format. Defaults to wav if not specified."
    )
    sample_rate: Literal[8000, 16000, 24000, 32000, 44100, 48000] | None = Field(
        None, description="Output sample rate in Hz. Defaults to provider setting if not specified."
    )
    speech_rate: int | None = Field(
        None, ge=-50, le=100, description="Speech speed adjustment. -50 (slowest) to 100 (fastest)."
    )
    loudness_rate: int | None = Field(
        None,
        ge=-50,
        le=100,
        description="Volume/loudness adjustment. -50 (quietest) to 100 (loudest).",
    )
    pitch_rate: int | None = Field(
        None, ge=-12, le=12, description="Pitch adjustment in semitones. -12 to +12."
    )
    subtitle: bool | None = Field(
        None, description="Whether to return timestamped subtitles in the response."
    )
    subtitle_type: Literal["utterance", "word"] | None = Field(
        None, description="Subtitle granularity: utterance-level or word-level timestamps."
    )


class AudioWatermarkOptions(BaseModel):
    """AIGC watermark controls for Seed Audio."""

    enable: bool | None = Field(None, description="Enable or disable AIGC audio watermarking.")
    metadata: bool | None = Field(
        None, description="Whether to embed AIGC metadata in the audio file."
    )


class SeedAudioGenerateInput(BaseModel):
    """Input model for ``seed_audio_generate``."""

    text_prompt: str | None = Field(
        None,
        min_length=1,
        max_length=3000,
        description=(
            "Text describing the audio to generate (up to 3,000 characters). Required unless "
            "variation_prompts is set."
        ),
    )
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
    variations: int = Field(
        1,
        ge=1,
        le=MAX_VARIATIONS,
        description=(
            "Independent generations to run in parallel (1-5). Seed Audio has no seed, so "
            "variations differ by the model's sampling; partial failures are reported per variation."
        ),
    )
    variation_prompts: list[Annotated[str, Field(min_length=1, max_length=3000)]] | None = Field(
        None,
        description="One prompt per variation, overriding text_prompt. Must have exactly `variations` entries.",
    )
    persist: bool = Field(
        True, description="Whether to persist the generated audio as a durable MCP resource."
    )
    output_path: str | None = output_path_field(
        "the generated audio; use a directory ending in '/' when variations > 1"
    )
    overwrite: bool = overwrite_field()

    @model_validator(mode="after")
    def validate_prompts(self) -> SeedAudioGenerateInput:
        """Require a prompt and one variation prompt per variation."""
        if self.text_prompt is None and not self.variation_prompts:
            raise ValueError("Either text_prompt or variation_prompts must be provided.")
        if self.variation_prompts is not None and len(self.variation_prompts) != self.variations:
            raise ValueError(f"variation_prompts must have exactly {self.variations} entries.")
        return self

    @model_validator(mode="after")
    def validate_no_media_mixing(self) -> SeedAudioGenerateInput:
        """Reject mixing audio and image references."""
        if self.audio_references and self.image_reference:
            raise ValueError(
                "Audio references and image reference are mutually exclusive. "
                "Use one or the other, not both."
            )
        return self


class SeedAudioGenerateOutput(BaseModel):
    """Output model for ``seed_audio_generate``."""

    provider: Literal["byteplus-seed-speech"] = Field(
        "byteplus-seed-speech", description="Provider that generated the audio."
    )
    model: Literal["seed-audio-1.0"] = Field(
        "seed-audio-1.0", description="Model used for generation."
    )
    duration_seconds: float | None = Field(
        None, description="Actual audio duration in seconds; null when variations > 1."
    )
    billing_duration_seconds: float | None = Field(
        None,
        description=(
            "Billing-relevant audio duration in seconds (may differ from actual duration); null "
            "when variations > 1."
        ),
    )
    artifact: ArtifactRef | None = Field(
        None,
        description="Durable artifact reference for the generated audio; null when variations > 1.",
    )
    subtitle: Subtitle | None = Field(
        None, description="Timestamped subtitle data, if requested and available."
    )
    request_id: str | None = Field(None, description="Client request ID used for this generation.")
    provider_log_id: str | None = Field(
        None, description="Provider-side log ID for troubleshooting."
    )
    source_url: str | None = Field(
        None, description="Provider HTTP URL (2-hour expiry) for direct download."
    )
    summary: VariationSummary | None = Field(
        None,
        description="Per-variation artifacts and errors when variations > 1; null otherwise.",
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(r"Max\s+duration\s*:\s*(\d+(?:\.\d+)?)\s*seconds?", re.IGNORECASE)
_MAX_MODEL_DURATION = 120.0


def _parse_duration_hint(text_prompt: str, *, default: float = 15.0) -> float:
    """Extract a duration hint from the prompt's ``Max duration: N seconds`` line.

    Falls back to ``default`` (15 s) if no hint is found. Capped at
    ``_MAX_MODEL_DURATION`` (120 s) to prevent runaway estimates.
    """
    match = _DURATION_RE.search(text_prompt)
    if not match:
        return default
    value = float(match.group(1))
    if value <= 0:
        return default
    return min(value, _MAX_MODEL_DURATION)


# ---------------------------------------------------------------------------
# Tool handler
# ---------------------------------------------------------------------------


@local_export(
    "artifact", "summary", require_dir=lambda i: i.variations > 1, precondition=_needs_persist
)
async def seed_audio_generate(
    input: SeedAudioGenerateInput, ctx: Context
) -> SeedAudioGenerateOutput | ToolResult:
    """Generate full-scene audio through Seed Speech.

    Accepts a text prompt (up to 3,000 characters) and optional audio or
    image references for voice cloning and scene control. Returns a durable
    artifact reference that survives the 2-hour provider URL expiry. Set
    variations (up to 5) for independent parallel generations, returned in
    summary. Run this tool through ark_job_submit (or native task
    augmentation); generation and persistence can exceed foreground client
    deadlines.
    """
    await context_log(ctx, "info", "Starting Seed Audio generation")
    await ctx.report_progress(progress=10, total=100)

    settings = get_settings()
    if not settings.has_seed_audio:
        raise ValueError(
            "BYTEPLUS_SEED_SPEECH_API_KEY is not configured. "
            "Set it in .env to enable Seed Audio tools."
        )

    # Build provider references from domain input; asset:// references follow
    # BYTEPLUS_MODELARK_ASSET_REFERENCE_MODE (rejected by default).
    audio_refs_data = await resolve_asset_references(
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
    image_ref_data = image_refs[0] if image_refs else None

    if input.variations > 1:
        summary = await _generate_variations(input, ctx, audio_refs_data, image_ref_data)
        await ctx.report_progress(progress=100, total=100)
        return SeedAudioGenerateOutput(summary=summary)

    text_prompt = input.text_prompt or (input.variation_prompts or [""])[0]
    service = SeedAudioService()
    await ctx.report_progress(progress=30, total=100)

    references = SeedAudioService.build_references(
        audio_refs=audio_refs_data,
        image_ref=image_ref_data,
    )

    audio_config_dict = None
    if input.output:
        audio_config_dict = input.output.model_dump(exclude_none=True)

    watermark_dict = None
    if input.watermark:
        watermark_dict = input.watermark.model_dump(exclude_none=True)

    request = SeedAudioService.build_request(
        text_prompt=text_prompt,
        references=references if references else None,
        audio_config=audio_config_dict,
        watermark=watermark_dict,
    )

    await ctx.report_progress(progress=50, total=100)

    duration_hint = _parse_duration_hint(text_prompt, default=15.0)
    estimated_cost = log_cost_estimate(
        product="audio", variations=1, duration_seconds=duration_hint
    )
    client_request_id = str(uuid4())

    try:
        async with billed_provider_slot(
            ctx,
            provider="seed-speech",
            product="audio",
            estimated_cost_usd=estimated_cost,
        ):
            response, log_id = await call_with_retry(
                lambda: service.generate(request, request_id=client_request_id)
            )
    except ProviderError as exc:
        await context_log(ctx, "error", f"Seed Audio generation failed: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    await ctx.report_progress(progress=80, total=100)

    # Persist the audio output.
    artifact: ArtifactRef
    source_url: str | None = None
    provider_expiry: str | None = (
        (datetime.now(UTC) + timedelta(hours=2)).isoformat() if response.url else None
    )

    if input.persist and response.audio:
        store = get_runtime(ctx).artifact_store
        artifact = await persist_base64(
            store,
            provider_url=response.url,
            data=response.audio,
            media_type=MediaType.AUDIO,
            mime_type="audio/wav",
            source_expires_at=provider_expiry,
            auth=get_principal(ctx),
        )
        source_url = response.url
    elif response.url:
        expiry = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
        artifact = ArtifactRef(
            id="provider-url",
            uri=response.url,
            media_type=MediaType.AUDIO,
            mime_type="audio/wav",
            created_at=datetime.now(UTC).isoformat(),
            source_expires_at=expiry,
        )
        source_url = response.url
    else:
        raise ValueError("Provider returned neither Base64 audio nor URL.")

    await ctx.report_progress(progress=100, total=100)
    log_info(
        "seed_audio_complete",
        duration=response.duration,
        billing_duration=response.original_duration,
        artifact_id=artifact.id,
    )

    return SeedAudioGenerateOutput(
        duration_seconds=response.duration or 0.0,
        billing_duration_seconds=response.original_duration or 0.0,
        artifact=artifact,
        subtitle=SeedAudioService.extract_subtitle(response),
        request_id=client_request_id,
        provider_log_id=log_id,
        source_url=source_url,
    )


async def _generate_variations(
    input: SeedAudioGenerateInput,
    ctx: Context,
    audio_refs_data: list[dict[str, Any]] | None,
    image_ref_data: dict[str, Any] | None,
) -> VariationSummary:
    log_cost_estimate(product="audio", variations=input.variations, duration_seconds=15.0)
    prompts = resolve_prompts(input.text_prompt, input.variation_prompts, input.variations)
    store = get_runtime(ctx).artifact_store
    owner = get_principal(ctx)
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
            batch_deadline=variation_batch_deadline(
                input.variations, get_settings(), persists=True
            ),
            max_concurrent=DEFAULT_MAX_CONCURRENT,
        )
    finally:
        await service.close()

    log_info(
        "seed_audio_variations_complete",
        total=summary.total,
        succeeded=summary.succeeded,
        failed=summary.failed,
    )

    return summary


# Tool annotation constants — camelCase per MCP specification.
TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
