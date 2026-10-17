"""Shared Seedream components: edit coordinates, request building, persistence, variations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, model_validator

from ark_mcp.artifacts.store import ArtifactPersistenceError
from ark_mcp.config.env import get_settings
from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.models import VariationError, VariationResult, VariationSummary
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.seedream import SeedreamService
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import billed_provider_slot, get_principal, get_runtime
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
        output_format=request.output_format,
        response_format=request.response_format,
        watermark=request.watermark,
        prompt_optimization=caps.resolve_prompt_optimization(request.prompt_optimization),
        background=request.background,
    )


def _source_expiry() -> str:
    return (datetime.now(UTC) + SOURCE_URL_LIFETIME).isoformat()


def _mime_type(item_format: str | None, requested: str | None) -> str:
    return f"image/{item_format or requested or 'jpeg'}"


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
) -> VariationSummary:
    """Generate one image per prompt in parallel, capturing failures per variation."""
    settings = get_settings()
    log_cost_estimate(product="image", variations=len(prompts))
    store = get_runtime(ctx).artifact_store
    owner = get_principal(ctx)
    service = SeedreamService()

    async def generate_one(index: int, progress: VariationProgress) -> VariationResult:
        try:
            provider_request = build_seedream_request(
                request, caps, images, prompt=prompts[index], seed=seeds[index]
            )
            async with billed_provider_slot(
                ctx,
                provider="modelark",
                product="image",
                estimated_cost_usd=estimate_cost(product="image", variations=1),
            ):
                progress.phase = "generating"
                response, request_id = await call_with_retry(
                    lambda: service.generate(provider_request)
                )
            artifact = await _persist_variation(
                response, request, store, owner, progress, index, seeds[index], request_id
            )
            return VariationResult(
                index=index, seed=seeds[index], artifact=artifact, request_id=request_id
            )
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
    return summary


async def _persist_variation(
    response: SeedreamProviderResponse,
    request: Any,
    store: Any,
    owner: Any,
    progress: VariationProgress,
    index: int,
    seed: int | None,
    request_id: str | None,
) -> ArtifactRef | None:
    if not response.data:
        return None
    item = response.data[0]
    mime_type = _mime_type(item.output_format, request.output_format)
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
