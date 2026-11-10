"""``seedream_generate_image`` tool — generate or edit images with ModelArk Seedream.

One tool covers text-to-image, reference-based editing, point/box edits,
provider batches (``max_images``) and parallel variations. Model-specific
limits are checked at runtime from the capability registry.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import ImageCapabilities, get_capability_registry
from ark_mcp.domain.artifacts import ArtifactRef
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.media import ReferenceImageInput
from ark_mcp.domain.models import (
    SeedreamGenerationMetadata,
    SeedreamItemError,
    SeedreamUsage,
    VariationSummary,
)
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.seedream import SeedreamService
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import billed_provider_slot
from ark_mcp.tools._asset_shared import resolve_asset_references
from ark_mcp.tools._capability_checks import check_image_request, raise_on_violations
from ark_mcp.tools._cost import image_cost_estimate, log_cost_estimate
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._local_export import local_export, output_path_field, overwrite_field
from ark_mcp.tools._local_export import needs_persist as _needs_persist
from ark_mcp.tools._parallel import generate_seeds
from ark_mcp.tools._redaction import redact_media_url
from ark_mcp.tools._seedream_shared import (
    MAX_PROMPT_CHARACTERS,
    EditBbox,
    EditCoordinate,
    attach_image_inspection,
    build_seedream_request,
    edit_prompt,
    generation_metadata,
    run_seedream_variations,
    store_response_images,
)
from ark_mcp.tools._task_execution import context_log

MAX_VARIATIONS = 10

__all__ = [
    "EditBbox",
    "EditCoordinate",
    "SeedreamGenerateInput",
    "SeedreamGenerateOutput",
    "SeedreamGeneratePreview",
    "seedream_generate_image",
]


class SeedreamGenerateInput(BaseModel):
    """Input model for ``seedream_generate_image``."""

    model_config = ConfigDict(extra="forbid")

    prompt: str | None = Field(
        None,
        min_length=1,
        max_length=MAX_PROMPT_CHARACTERS,
        description=(
            "Prompt or edit instruction (1-4,000 characters including point/bbox markup). "
            "Required unless variation_prompts is set."
        ),
    )
    images: list[ReferenceImageInput] | None = Field(
        None,
        description=(
            "Reference or source images (per-model max in ark_list_models(kind='image'))."
        ),
    )
    point: EditCoordinate | None = Field(
        None,
        description="Edit: normalized (0-999) point on the first image. Requires images.",
    )
    bbox: EditBbox | None = Field(
        None,
        description="Edit: normalized (0-999) box on the first image. Requires images.",
    )
    model: str | None = Field(
        None,
        description="Exact model ID from ark_list_models(kind='image'); the default if omitted.",
    )
    size: str | None = Field(
        None,
        description="5.5: 1K, 1.5K, 2K (default), or WIDTHxHEIGHT: 921600-4624220 pixels, ratio 1:16-16:1.",
    )
    seed: int | None = Field(
        None,
        ge=-1,
        le=2147483647,
        description=(
            "Random seed. With variations > 1: N gives variation i seed N + i; -1 picks a "
            "random seed per variation; omitted lets the provider randomize."
        ),
    )
    max_images: int | None = Field(
        None,
        ge=1,
        le=15,
        description=(
            "Images from one provider call (1-15), on models that support batches. Not "
            "combinable with variations > 1."
        ),
    )
    output_format: Literal["png", "jpeg"] | None = Field(
        None,
        description="Output format. Defaults to png on 5.5 and for transparent backgrounds; otherwise provider default.",
    )
    response_format: Literal["url", "b64_json"] | None = Field(
        None, description="Provider response format: URL or Base64 JSON (requires persist=true)."
    )
    watermark: bool | None = Field(None, description="Apply an AIGC watermark.")
    prompt_optimization: Literal["standard", "fast"] | None = Field(
        None,
        description=(
            "5.5: standard only (provider default); fast is rejected. "
            "5.0 Pro: standard or fast, default SEEDREAM_PROMPT_OPTIMIZATION_MODE."
        ),
    )
    background: Literal["auto", "opaque", "transparent"] | None = Field(
        None,
        description=(
            "5.5 defaults to auto (prompt decides; jpeg is opaque). transparent requires png "
            "output; 5.5 accepts text or references without alpha, 5.0 Pro needs one "
            "already-transparent PNG. auto is 5.5-only."
        ),
    )
    safety_identifier: str | None = Field(
        None,
        min_length=1,
        max_length=64,
        pattern=r"^[\x21-\x7E]+$",
        description="5.5/5.0 Pro: stable end-user ID, 1-64 non-space printable ASCII characters. Use a hash, not personal data.",
    )
    variations: int = Field(
        1,
        ge=1,
        le=MAX_VARIATIONS,
        description="Parallel generations (1-10); failures are reported per variation.",
    )
    variation_prompts: (
        list[Annotated[str, Field(min_length=1, max_length=MAX_PROMPT_CHARACTERS)]] | None
    ) = Field(
        None,
        description="One prompt per variation, overriding prompt; exactly `variations` entries.",
    )
    persist: bool = Field(True, description="Persist images as durable MCP resources.")
    dry_run: bool = Field(
        False,
        description="Validate and return the request bodies and a cost estimate; nothing is generated or billed.",
    )
    output_path: str | None = output_path_field(
        "the generated images", directory_needed="when several images are produced"
    )
    overwrite: bool = overwrite_field()

    @model_validator(mode="after")
    def validate_request_shape(self) -> SeedreamGenerateInput:
        """Check prompt, edit and variation combinations that hold for every model."""
        if self.response_format == "b64_json" and not self.persist:
            raise ValueError("response_format='b64_json' requires persist=true; use url otherwise.")
        if self.variation_prompts is not None and len(self.variation_prompts) != self.variations:
            raise ValueError(
                f"variation_prompts must have exactly {self.variations} entries; got "
                f"{len(self.variation_prompts)}."
            )
        if not self.prompt and not self.variation_prompts:
            raise ValueError("Either prompt or variation_prompts is required.")
        if (self.point or self.bbox) and not self.images:
            raise ValueError("point and bbox edit the first image; provide images.")
        if self.variations > 1 and (self.max_images or 1) > 1:
            raise ValueError("Use either max_images (one provider batch) or variations, not both.")
        for prompt in self.prompts():
            length = len(prompt)
            if length > MAX_PROMPT_CHARACTERS:
                raise ValueError(
                    f"Prompt with coordinate markup is {length} characters; must not exceed "
                    f"{MAX_PROMPT_CHARACTERS}."
                )
        return self

    def prompts(self) -> list[str]:
        """Return the final prompt for each variation, with any edit markup."""
        base = (
            list(self.variation_prompts)
            if self.variation_prompts
            else [self.prompt or ""] * self.variations
        )
        return [edit_prompt(prompt, self.point, self.bbox) for prompt in base]


class SeedreamGeneratePreview(BaseModel):
    """What a dry run would have submitted."""

    requests: list[dict[str, JsonValue]] = Field(
        ...,
        description=(
            "Provider request bodies that would be sent, one per variation. Reference image "
            "URLs are reduced to scheme and host, inline Base64 to its size, and safety_identifier redacted."
        ),
    )
    estimated_cost_usd: float = Field(
        ...,
        description="Estimated USD using cached account pricing or built-in fallback; not billing.",
    )
    price_source: Literal["catalog", "builtin"] = Field(
        "builtin", description="catalog: cached account image rate; builtin: fallback estimate."
    )


class SeedreamGenerateOutput(BaseModel):
    """Output model for ``seedream_generate_image``."""

    provider: Literal["byteplus-modelark"] = Field(
        "byteplus-modelark", description="Provider that generated the images."
    )
    model: str = Field(..., description="Model ID used for generation.")
    created_at: str = Field(..., description="ISO-8601 timestamp of generation.")
    dry_run: bool = Field(False, description="True when nothing was generated; see preview.")
    artifacts: list[ArtifactRef] = Field(
        default_factory=list,
        description="Images from a single call (variations=1). Empty for variations and dry runs.",
    )
    item_errors: list[SeedreamItemError] = Field(
        default_factory=list,
        description="Single-call image errors, including NO_OUTPUT and NO_DELIVERABLE_OUTPUT.",
    )
    usage: SeedreamUsage | None = Field(
        None, description="Provider image/token usage; null for variations and dry runs."
    )
    summary: VariationSummary | None = Field(
        None,
        description="Per-variation results (seed, artifact or error) when variations > 1.",
    )
    generations: list[SeedreamGenerationMetadata] = Field(
        default_factory=list,
        description="Completed-call provenance, provider reports and byte checks; empty for dry runs.",
    )
    preview: SeedreamGeneratePreview | None = Field(
        None, description="Request bodies and cost estimate; set only for a dry run."
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}


@local_export(
    "artifacts",
    "summary",
    require_dir=lambda i: i.variations > 1 or (i.max_images or 1) > 1,
    precondition=_needs_persist,
)
async def seedream_generate_image(
    input: SeedreamGenerateInput, ctx: Context
) -> SeedreamGenerateOutput | ToolResult:
    """Generate or edit images with Seedream (text-to-image, image-to-image, point/box edits).

    Pass images to edit or use as references; point or bbox targets a spot on the first
    image. variations (up to 10) run in parallel; max_images is one provider batch on models
    that support it. background='transparent' generates transparent PNGs from text or
    references on 5.5; 5.0 Pro requires an already-transparent PNG input.
    5.5 supports auto background and standard optimization only. Layer decomposition
    and rich-text OCR are not supported; unknown input fields are rejected.
    Model limits: ark_list_models(kind='image'); requests outside them
    are rejected with every problem listed. dry_run=true returns request bodies and a cost
    estimate. Run through ark_job_submit.
    """
    settings = get_settings()
    if not settings.has_modelark:
        raise ValueError(
            "BYTEPLUS_MODELARK_API_KEY is not configured. Set it in .env to enable Seedream tools."
        )
    await context_log(ctx, "info", "Starting Seedream image generation")
    await ctx.report_progress(progress=10, total=100)

    caps = get_capability_registry().resolve_image_model(input.model)
    raise_on_violations(
        check_image_request(input, caps),
        model_id=caps.model_id,
        family=caps.family.value,
        kind="image",
    )
    images = await resolve_asset_references(
        ctx,
        [source.model_dump() for source in input.images] if input.images else None,
        product="Seedream",
        expected_type="Image",
    )
    prompts = input.prompts()
    seeds = generate_seeds(input.seed, input.variations) if input.variations > 1 else [input.seed]

    if input.dry_run:
        return _preview(input, caps, images, prompts, seeds)
    if input.variations > 1:
        summary, generations = await run_seedream_variations(
            input, ctx, caps, images, prompts, seeds
        )
        await ctx.report_progress(progress=100, total=100)
        return SeedreamGenerateOutput(
            model=caps.model_id,
            created_at=datetime.now(UTC).isoformat(),
            summary=summary,
            generations=generations,
        )
    return await _generate_single(input, ctx, caps, images, prompts[0])


async def _generate_single(
    input: SeedreamGenerateInput,
    ctx: Context,
    caps: ImageCapabilities,
    images: list[dict[str, object]] | None,
    prompt: str,
) -> SeedreamGenerateOutput | ToolResult:
    await ctx.report_progress(progress=30, total=100)
    request = build_seedream_request(input, caps, images, prompt=prompt, seed=input.seed)
    estimated_cost = log_cost_estimate(
        product="image", variations=input.max_images or 1, model_id=caps.model_id
    )
    service = SeedreamService()
    try:
        async with billed_provider_slot(
            ctx, provider="modelark", product="image", estimated_cost_usd=estimated_cost
        ):
            response, request_id = await call_with_retry(lambda: service.generate(request))
    except ProviderError as exc:
        await context_log(ctx, "error", f"Seedream generation failed: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    await ctx.report_progress(progress=80, total=100)
    artifacts = await store_response_images(
        ctx, response, persist=input.persist, output_format=request.output_format
    )
    metadata = generation_metadata(response, request, request_id)
    output_indices = [
        index
        for index, item in enumerate(response.data)
        if item.url or (input.persist and item.b64_json)
    ]
    for index, artifact in zip(output_indices, artifacts, strict=True):
        await attach_image_inspection(ctx, artifact, metadata.images[index], request)
    item_errors = SeedreamService.extract_item_errors(response)
    for index, item in enumerate(response.data):
        if item.b64_json and not item.url and not input.persist:
            item_errors.append(
                SeedreamItemError(
                    index=index,
                    code="NO_DELIVERABLE_OUTPUT",
                    message="Provider returned Base64 without persistence; output cannot be delivered.",
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
        item_errors=item_errors,
        usage=SeedreamService.extract_usage(response),
        generations=[metadata],
    )


def _preview(
    input: SeedreamGenerateInput,
    caps: ImageCapabilities,
    images: list[dict[str, object]] | None,
    prompts: list[str],
    seeds: list[int | None],
) -> SeedreamGenerateOutput:
    requests = [
        _redact(
            build_seedream_request(input, caps, images, prompt=prompt, seed=seed).model_dump(
                exclude_none=True
            )
        )
        for prompt, seed in zip(prompts, seeds, strict=True)
    ]
    cost, source = image_cost_estimate(caps.model_id, len(prompts) * (input.max_images or 1))
    return SeedreamGenerateOutput(
        model=caps.model_id,
        created_at=datetime.now(UTC).isoformat(),
        dry_run=True,
        preview=SeedreamGeneratePreview(
            requests=requests, estimated_cost_usd=cost, price_source=source
        ),
    )


def _redact(body: dict[str, JsonValue]) -> dict[str, JsonValue]:
    if "safety_identifier" in body:
        body["safety_identifier"] = "[REDACTED]"
    image = body.get("image")
    if isinstance(image, str):
        body["image"] = redact_media_url(image)
    elif isinstance(image, list):
        body["image"] = [
            redact_media_url(item) if isinstance(item, str) else item for item in image
        ]
    return body
