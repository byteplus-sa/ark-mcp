"""Shared Seedream components: edit coordinates, request building, persistence, variations."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, model_validator

from ark_mcp.artifacts.image_inspection import inspect_image_bytes
from ark_mcp.artifacts.store import ArtifactPersistenceError
from ark_mcp.config.env import get_settings
from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.models import (
    SeedreamGenerationMetadata,
    SeedreamImageInspection,
    SeedreamImageMetadata,
    VariationError,
    VariationResult,
    VariationSummary,
)
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.seedream import SeedreamService
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import billed_provider_slot, get_principal, get_runtime
from ark_mcp.security.media_policy import decode_base64_safely, get_media_limits
from ark_mcp.tools._cost import DEFAULT_MAX_CONCURRENT, estimate_cost, log_cost_estimate
from ark_mcp.tools._parallel import VariationProgress, run_variation_batch, variation_batch_deadline
from ark_mcp.tools._persistence import (
    persist_base64,
    persist_from_url,
    persistence_issue,
    persistence_variation_error,
    provider_url_ref,
)

if TYPE_CHECKING:
    from fastmcp import Context

    from ark_mcp.config.model_capabilities import ImageCapabilities
    from ark_mcp.providers.modelark.schemas import (
        SeedreamProviderRequest,
        SeedreamProviderResponse,
    )

MAX_PROMPT_CHARACTERS = 4000
SOURCE_URL_LIFETIME = timedelta(hours=24)


class EditCoordinate(BaseModel):
    """Normalized point coordinate (0-999) for interactive editing."""

    x: int = Field(..., ge=0, le=999, description="Horizontal position, 0=left, 999=right.")
    y: int = Field(..., ge=0, le=999, description="Vertical position, 0=top, 999=bottom.")


class EditBbox(BaseModel):
    """Normalized bounding box (0-999) for region-based editing."""

    x1: int = Field(..., ge=0, le=999, description="Left edge.")
    y1: int = Field(..., ge=0, le=999, description="Top edge.")
    x2: int = Field(..., ge=0, le=999, description="Right edge.")
    y2: int = Field(..., ge=0, le=999, description="Bottom edge.")

    @model_validator(mode="after")
    def validate_ordering(self) -> EditBbox:
        if self.x1 > self.x2:
            raise ValueError(f"x1 ({self.x1}) must not exceed x2 ({self.x2})")
        if self.y1 > self.y2:
            raise ValueError(f"y1 ({self.y1}) must not exceed y2 ({self.y2})")
        return self


def edit_prompt(instruction: str, point: EditCoordinate | None, bbox: EditBbox | None) -> str:
    """Prepend coordinate markup for point- or box-based edits to an instruction."""
    parts: list[str] = []
    if bbox is not None:
        parts.append(f"Image 1<bbox>{bbox.x1} {bbox.y1} {bbox.x2} {bbox.y2}</bbox>")
    if point is not None:
        parts.append(f"Image 1<point>{point.x} {point.y}</point>")
    markup = " ".join(parts)
    return f"{markup} {instruction}" if markup else instruction


def build_seedream_request(
    request: Any,
    caps: ImageCapabilities,
    images: list[dict[str, Any]] | None,
    *,
    prompt: str,
    seed: int | None,
) -> SeedreamProviderRequest:
    """Build the provider request body for one Seedream call."""
    return SeedreamService.build_request(
        model=caps.model_id,
        prompt=prompt,
        images=images,
        size=request.size,
        seed=seed,
        max_images=request.max_images,
        output_format=caps.resolve_output_format(request.output_format, request.background),
        response_format=request.response_format,
        watermark=request.watermark,
        prompt_optimization=caps.resolve_prompt_optimization(request.prompt_optimization),
        background=request.background,
        safety_identifier=request.safety_identifier,
    )


def _source_expiry() -> str:
    return (datetime.now(UTC) + SOURCE_URL_LIFETIME).isoformat()


def _mime_type(item_format: str | None, requested: str | None) -> str:
    return f"image/{item_format or requested or 'jpeg'}"


def _normalized_image_format(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.split(";", 1)[0].strip().lower().removeprefix("image/")
    return "jpeg" if normalized == "jpg" else normalized


def generation_metadata(
    response: SeedreamProviderResponse,
    request: SeedreamProviderRequest,
    request_id: str | None,
    *,
    variation_index: int = 0,
) -> SeedreamGenerationMetadata:
    """Record submitted provenance without copying prompt or private media."""
    metadata = SeedreamService.extract_generation_metadata(
        response, variation_index=variation_index
    )
    metadata.request_id = request_id
    metadata.seed = request.seed
    metadata.prompt_sha256 = hashlib.sha256(request.prompt.encode("utf-8")).hexdigest()
    options: dict[str, str | int | bool] = {}
    for name in ("size", "output_format", "response_format", "watermark", "background"):
        value = getattr(request, name)
        if value is not None:
            options[name] = value
    if request.optimize_prompt_options:
        options["prompt_optimization"] = request.optimize_prompt_options["mode"]
    if request.sequential_image_generation_options:
        options["max_images"] = request.sequential_image_generation_options["max_images"]
    options["reference_count"] = (
        len(request.image) if isinstance(request.image, list) else int(bool(request.image))
    )
    metadata.request_options = options
    return metadata


async def attach_image_inspection(
    ctx: Context,
    artifact: ArtifactRef,
    image: SeedreamImageMetadata,
    request: SeedreamProviderRequest,
) -> None:
    """Inspect accessible artifact bytes without fetching expiring provider URLs."""
    image.artifact_id = artifact.id
    if not artifact.fallback_data and not artifact.uri.startswith("seed-media://artifacts/"):
        return
    try:
        async with asyncio.timeout(10):
            if artifact.fallback_data:
                raw = decode_base64_safely(
                    artifact.fallback_data, get_media_limits().image_max_bytes, label="image"
                )
            else:
                stored = await get_runtime(ctx).artifact_store.get(
                    artifact.id, auth=get_principal(ctx)
                )
                raw = stored.data
            inspection = await asyncio.to_thread(inspect_image_bytes, raw)
    except (OSError, ValueError, RuntimeError, ProviderError):
        image.inspection = SeedreamImageInspection(
            status="unavailable", issues=["inspection_unavailable"]
        )
        return
    image.inspection = inspection
    if inspection.status != "verified":
        return
    for label, claimed_format in (
        ("artifact", artifact.mime_type),
        ("requested", request.output_format),
        ("provider", image.output_format),
    ):
        if claimed_format and _normalized_image_format(claimed_format) != inspection.output_format:
            inspection.issues.append(f"{label}_format_mismatch")
    actual_size = f"{inspection.width}x{inspection.height}"
    for label, claimed_size in (("requested", request.size), ("provider", image.size)):
        if claimed_size and "x" in claimed_size and claimed_size != actual_size:
            inspection.issues.append(f"{label}_size_mismatch")
    for label, claimed_background in (
        ("requested", request.background),
        ("provider", image.background),
    ):
        if claimed_background in ("opaque", "transparent") and (
            (claimed_background == "transparent") != inspection.has_transparency
        ):
            inspection.issues.append(f"{label}_background_mismatch")
    if inspection.issues:
        inspection.status = "mismatch"


async def store_response_images(
    ctx: Context,
    response: SeedreamProviderResponse,
    *,
    persist: bool,
    output_format: str | None,
) -> list[ArtifactRef]:
    """Persist each returned image, or return expiring provider-URL references."""
    store = get_runtime(ctx).artifact_store
    owner = get_principal(ctx)
    source_expiry = _source_expiry()
    artifacts: list[ArtifactRef] = []
    for item in response.data:
        mime_type = _mime_type(item.output_format, output_format)
        if persist and item.b64_json:
            artifacts.append(
                await persist_base64(
                    store,
                    data=item.b64_json,
                    media_type=MediaType.IMAGE,
                    mime_type=mime_type,
                    source_expires_at=source_expiry,
                    auth=owner,
                    provider_url=item.url,
                )
            )
        elif persist and item.url:
            artifacts.append(
                await persist_from_url(
                    store,
                    url=item.url,
                    media_type=MediaType.IMAGE,
                    mime_type=mime_type,
                    source_expires_at=source_expiry,
                    auth=owner,
                )
            )
        elif item.url:
            artifacts.append(
                provider_url_ref(
                    url=item.url,
                    media_type=MediaType.IMAGE,
                    mime_type=mime_type,
                    source_expires_at=source_expiry,
                )
            )
    return artifacts


async def run_seedream_variations(
    request: Any,
    ctx: Context,
    caps: ImageCapabilities,
    images: list[dict[str, Any]] | None,
    prompts: list[str],
    seeds: list[int | None],
) -> tuple[VariationSummary, list[SeedreamGenerationMetadata]]:
    """Generate one image per prompt in parallel, capturing failures per variation."""
    settings = get_settings()
    log_cost_estimate(product="image", variations=len(prompts), model_id=caps.model_id)
    store = get_runtime(ctx).artifact_store
    owner = get_principal(ctx)
    service = SeedreamService()
    generations: dict[int, SeedreamGenerationMetadata] = {}

    async def generate_one(index: int, progress: VariationProgress) -> VariationResult:
        try:
            provider_request = build_seedream_request(
                request, caps, images, prompt=prompts[index], seed=seeds[index]
            )
            async with billed_provider_slot(
                ctx,
                provider="modelark",
                product="image",
                estimated_cost_usd=estimate_cost(
                    product="image", variations=1, model_id=caps.model_id
                ),
            ):
                progress.phase = "generating"
                response, request_id = await call_with_retry(
                    lambda: service.generate(provider_request)
                )
            generations[index] = generation_metadata(
                response, provider_request, request_id, variation_index=index
            )
            item_errors = service.extract_item_errors(response)
            if not response.data or not (response.data[0].url or response.data[0].b64_json):
                error = item_errors[0]
                return VariationResult(
                    index=index,
                    seed=seeds[index],
                    request_id=request_id,
                    error=VariationError(
                        code=error.code or "NO_OUTPUT",
                        message=error.message,
                        request_id=request_id,
                        phase="generating",
                    ),
                )
            artifact = await _persist_variation(
                response,
                request,
                store,
                owner,
                progress,
                index,
                seeds[index],
                request_id,
                output_format=provider_request.output_format,
            )
            if artifact is None:
                return VariationResult(
                    index=index,
                    seed=seeds[index],
                    request_id=request_id,
                    error=VariationError(
                        code="NO_DELIVERABLE_OUTPUT",
                        message="Provider output cannot be delivered with these persistence settings.",
                        request_id=request_id,
                        phase="persisting",
                    ),
                )
            progress.phase = "persisting"
            progress.partial = VariationResult(
                index=index, seed=seeds[index], artifact=artifact, request_id=request_id
            )
            generations[index].images[0].artifact_id = artifact.id
            if artifact.fallback_data or artifact.uri.startswith("seed-media://artifacts/"):
                generations[index].images[0].inspection = SeedreamImageInspection(
                    status="unavailable", issues=["inspection_unavailable"]
                )
            await attach_image_inspection(
                ctx, artifact, generations[index].images[0], provider_request
            )
            return progress.partial
        except ProviderError as exc:
            return VariationResult(
                index=index,
                seed=seeds[index],
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
                index=index, seed=seeds[index], error=persistence_variation_error(exc)
            )
        except Exception as exc:
            return VariationResult(
                index=index,
                seed=seeds[index],
                error=VariationError(code="UNEXPECTED_ERROR", message=str(exc)),
            )

    try:
        summary = await run_variation_batch(
            count=len(prompts),
            factory=generate_one,
            batch_deadline=variation_batch_deadline(len(prompts), settings),
            max_concurrent=DEFAULT_MAX_CONCURRENT,
        )
    finally:
        await service.close()
    log_info(
        "seedream_variations_complete",
        total=summary.total,
        succeeded=summary.succeeded,
        failed=summary.failed,
    )
    for result in summary.variations:
        metadata = generations.get(result.index)
        if metadata is not None and metadata.images and result.artifact is not None:
            metadata.images[0].artifact_id = result.artifact.id
    return summary, [generations[index] for index in sorted(generations)]


async def _persist_variation(
    response: SeedreamProviderResponse,
    request: Any,
    store: Any,
    owner: Any,
    progress: VariationProgress,
    index: int,
    seed: int | None,
    request_id: str | None,
    *,
    output_format: str | None,
) -> ArtifactRef | None:
    if not response.data:
        return None
    item = response.data[0]
    mime_type = _mime_type(item.output_format, output_format)
    source_expiry = _source_expiry()
    if item.url:
        progress.partial = VariationResult(
            index=index,
            seed=seed,
            artifact=provider_url_ref(
                url=item.url,
                media_type=MediaType.IMAGE,
                mime_type=mime_type,
                source_expires_at=source_expiry,
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
    if request.persist:
        progress.phase = "persisting"
        if item.b64_json:
            return await persist_base64(
                store,
                data=item.b64_json,
                media_type=MediaType.IMAGE,
                mime_type=mime_type,
                source_expires_at=source_expiry,
                auth=owner,
                provider_url=item.url,
            )
        if item.url:
            return await persist_from_url(
                store,
                url=item.url,
                media_type=MediaType.IMAGE,
                mime_type=mime_type,
                source_expires_at=source_expiry,
                auth=owner,
            )
        return None
    if item.url:
        return provider_url_ref(
            url=item.url,
            media_type=MediaType.IMAGE,
            mime_type=mime_type,
            source_expires_at=source_expiry,
        )
    return None
