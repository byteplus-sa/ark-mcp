"""Shared Seedance tool components.

Input types (``SeedanceImageInput``, ``SeedanceVideoInput``,
``SeedanceAudioInput``), task-type constraints, request building, billed
submission, parallel variations, and Draft task loading for
``seedance_create_task``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, ClassVar, Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, model_validator

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import VideoCapabilities
from ark_mcp.domain.artifacts import MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.media import (
    ASSET_REFERENCE_URL_DESCRIPTION,
    MediaSource,
    validate_reference_url,
)
from ark_mcp.domain.models import VariationError, VariationResult, VariationSummary
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.generation_tasks import GenerationTaskService
from ark_mcp.providers.modelark.schemas import (
    GenerationTaskResponse,
    SeedanceCreateProviderRequest,
)
from ark_mcp.providers.modelark.seedance import SeedanceService
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import billed_provider_slot, get_principal, get_runtime
from ark_mcp.tools._asset_shared import collect_asset_ids
from ark_mcp.tools._cost import DEFAULT_MAX_CONCURRENT, estimate_cost, log_cost_estimate
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._parallel import (
    VariationProgress,
    run_variation_batch,
    variation_batch_deadline,
)
from ark_mcp.tools._task_execution import context_log

_IMAGE_INPUT_EXAMPLE = (
    'Expected each image reference to be a URL string ("https://..." or '
    '"asset://<asset_id>"), {"url": "https://..."}, or {"kind": "url", '
    '"url": "https://...", "role": "reference_image"}.'
)
_AUDIO_INPUT_EXAMPLE = (
    'Expected each audio reference to be a URL string ("https://..." or '
    '"asset://<asset_id>"), {"url": "https://..."}, or {"kind": "url", "url": "https://..."}.'
)


class SeedanceImageInput(MediaSource):
    """Image input with an optional role for Seedance."""

    MEDIA_CATEGORY: ClassVar[MediaType] = MediaType.IMAGE
    ALLOW_ASSET_URI: ClassVar[bool] = True

    url: str | None = Field(default=None, description=ASSET_REFERENCE_URL_DESCRIPTION)
    role: Literal["first_frame", "last_frame", "reference_image"] | None = Field(
        None,
        description="Role of this image: first_frame, last_frame, or reference_image. If omitted, provider default applies.",
    )

    @model_validator(mode="before")
    @classmethod
    def coerce_image_reference(cls, data: object) -> object:
        """Accept a plain URL string or ``{"url": ...}`` shorthand.

        A bare string is coerced to ``kind="url"`` with the default
        ``reference_image`` role. A dict with a URL but no explicit ``kind``
        is normalized the same way. Unrecognized dicts raise a tailored error
        that shows the expected shape without echoing the caller's input.
        """
        if isinstance(data, str):
            return {"kind": "url", "url": data, "role": "reference_image"}
        if isinstance(data, dict) and "kind" not in data and "url" in data:
            return {"kind": "url", **data, "role": data.get("role", "reference_image")}
        if isinstance(data, dict) and "kind" not in data:
            raise ValueError(_IMAGE_INPUT_EXAMPLE)
        return data


class SeedanceVideoInput(BaseModel):
    """Video reference input for Seedance."""

    kind: Literal["url"] = Field(
        "url",
        description="Media source kind. Always 'url' for video references.",
    )
    url: str = Field(
        ...,
        description=(
            "HTTPS URL of the reference video, or 'asset://<asset_id>' for an Active private "
            "Video asset."
        ),
    )
    role: Literal["reference_video"] = Field(
        "reference_video",
        description="Role of this input. Always 'reference_video'.",
    )

    @model_validator(mode="before")
    @classmethod
    def coerce_video_reference(cls, data: object) -> object:
        """Accept a plain URL string for a video reference."""
        if isinstance(data, str):
            return {"url": data}
        return data

    @model_validator(mode="after")
    def validate_video_url(self) -> SeedanceVideoInput:
        validate_reference_url(
            self.url, allow_asset=True, log_event="seedance_video_invalid_source_url"
        )
        return self


class SeedanceAudioInput(MediaSource):
    """Audio reference input for Seedance."""

    MEDIA_CATEGORY: ClassVar[MediaType] = MediaType.AUDIO
    ALLOW_ASSET_URI: ClassVar[bool] = True

    url: str | None = Field(default=None, description=ASSET_REFERENCE_URL_DESCRIPTION)
    role: Literal["reference_audio"] = Field(
        "reference_audio",
        description="Role of this input. Always 'reference_audio'.",
    )

    @model_validator(mode="before")
    @classmethod
    def coerce_audio_reference(cls, data: object) -> object:
        """Accept a plain URL string or ``{"url": ...}`` shorthand."""
        if isinstance(data, str):
            return {"kind": "url", "url": data, "role": "reference_audio"}
        if isinstance(data, dict) and "kind" not in data and "url" in data:
            return {"kind": "url", **data}
        if isinstance(data, dict) and "kind" not in data:
            raise ValueError(_AUDIO_INPUT_EXAMPLE)
        return data


OmniReferenceTaskType = Literal["auto", "reference", "edit", "extend"]

OMNI_REFERENCE_TASK_TYPE_DESCRIPTION = (
    "Seedance 2.5 task type hint: 'auto' (provider default, inferred from the prompt and "
    "media), 'reference' (new video from references), 'edit' (edit a reference video), or "
    "'extend' (extend a reference video forward or backward). 'edit' needs at least one "
    "reference video, ratio omitted or 'adaptive', and duration omitted or -1. 'extend' needs "
    "at least one reference video and ratio omitted or 'adaptive'. Write the prompt to match "
    "the task type, otherwise the provider fails the task with TaskTypeMismatch."
)

_ADAPTIVE_RATIO = "adaptive"
_FRAME_ROLES = frozenset({"first_frame", "last_frame"})


def task_type_constraint_violations(
    *,
    task_type: OmniReferenceTaskType | None,
    ratio: str | None,
    duration: int | None,
    has_videos: bool,
    image_roles: list[str | None],
) -> list[str]:
    """Return every documented Seedance 2.5 task-type constraint the request breaks.

    The provider rejects these combinations with
    ``InvalidParameter.TaskTypeConstraint`` after billing a submission slot, so
    they are rejected locally with the fix spelled out instead.
    """
    violations: list[str] = []
    ratio_is_fixed = ratio is not None and ratio != _ADAPTIVE_RATIO
    if task_type in ("edit", "extend"):
        if not has_videos:
            violations.append(
                f"omni_reference_task_type '{task_type}' requires at least one reference video."
            )
        if ratio_is_fixed:
            violations.append(
                f"omni_reference_task_type '{task_type}' requires ratio omitted or "
                f"'{_ADAPTIVE_RATIO}'; got '{ratio}'."
            )
    if task_type == "edit" and duration not in (None, -1):
        violations.append(
            "omni_reference_task_type 'edit' requires duration omitted or -1 (the output "
            f"follows the source video); got {duration}."
        )
    has_frame_image = any(role in _FRAME_ROLES for role in image_roles)
    if ratio_is_fixed and task_type not in ("edit", "extend") and has_frame_image:
        violations.append(
            "First-frame and first/last-frame requests require ratio omitted or "
            f"'{_ADAPTIVE_RATIO}' (the output follows the first frame); got '{ratio}'."
        )
    return violations


DRAFT_RESOLUTION: Literal["480p"] = "480p"
DRAFT_REUSED_FIELDS = (
    "prompt",
    "images",
    "videos",
    "audios",
    "duration",
    "ratio",
    "generate_audio",
    "omni_reference_task_type",
)
DRAFT_TASK_MAX_AGE = timedelta(days=7)
RECOMMENDED_POLL_AFTER_MS = 5000


@dataclass(frozen=True, slots=True)
class MediaPayloads:
    """Provider-ready media references for one create request."""

    images: list[dict[str, Any]] | None
    videos: list[dict[str, Any]] | None
    audios: list[dict[str, Any]] | None

    @property
    def asset_ids(self) -> list[str]:
        return collect_asset_ids(self.images, self.videos, self.audios)


def media_payloads(request: Any) -> MediaPayloads:
    """Dump a create request's media references into provider-ready dicts."""
    return MediaPayloads(
        images=[image.model_dump() for image in request.images] if request.images else None,
        videos=[video.model_dump() for video in request.videos] if request.videos else None,
        audios=[audio.model_dump() for audio in request.audios] if request.audios else None,
    )


def build_seedance_request(
    request: Any,
    caps: VideoCapabilities,
    media: MediaPayloads,
    *,
    prompt: str | None,
) -> SeedanceCreateProviderRequest:
    """Build the provider request body for one Seedance task."""
    if request.draft_task_id:
        content = SeedanceService.build_draft_content(request.draft_task_id)
    else:
        content = SeedanceService.build_content(
            prompt=prompt, images=media.images, videos=media.videos, audios=media.audios
        )
    return SeedanceService.build_request(
        model=caps.model_id,
        content=content,
        resolution=request.resolution,
        ratio=request.ratio,
        duration=request.duration,
        generate_audio=request.generate_audio,
        watermark=request.watermark,
        return_last_frame=request.return_last_frame,
        execution_expires_after=request.execution_expires_after,
        priority=request.priority,
        safety_identifier=request.safety_identifier,
        omni_reference_task_type=request.omni_reference_task_type,
        draft=request.draft,
        output_format=request.output_format,
    )


async def submit_seedance_request(
    ctx: Context,
    service: SeedanceService,
    caps: VideoCapabilities,
    request: SeedanceCreateProviderRequest,
) -> tuple[str, str | None]:
    """Submit one billed Seedance task and record its owner; raises ``ProviderError``."""
    async with billed_provider_slot(
        ctx,
        provider="modelark",
        product="video",
        estimated_cost_usd=estimate_cost(product="video", variations=1, video_family=caps.family),
    ):
        task_id, request_id = await call_with_retry(lambda: service.create_task(request))
    await get_runtime(ctx).ownership_store.record("modelark", task_id, get_principal(ctx))
    return task_id, request_id


async def execute_seedance_create(
    request: Any,
    ctx: Context,
    caps: VideoCapabilities,
    media: MediaPayloads,
) -> tuple[str, str | None] | ToolResult:
    """Create one Seedance task; returns ``(task_id, request_id)`` or a provider error result."""
    await ctx.report_progress(progress=50, total=100)
    provider_request = build_seedance_request(request, caps, media, prompt=request.prompt)
    log_cost_estimate(product="video", variations=1, video_family=caps.family)
    service = SeedanceService()
    try:
        task_id, request_id = await submit_seedance_request(ctx, service, caps, provider_request)
    except ProviderError as exc:
        await context_log(ctx, "error", f"Seedance task creation failed: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()
    await ctx.report_progress(progress=100, total=100)
    log_info("seedance_task_created", task_id=task_id, model=caps.model_id, request_id=request_id)
    return task_id, request_id


async def run_seedance_variations(
    request: Any,
    ctx: Context,
    caps: VideoCapabilities,
    media: MediaPayloads,
    prompts: list[str | None],
) -> VariationSummary:
    """Submit one Seedance task per prompt in parallel, capturing failures per variation."""
    settings = get_settings()
    log_cost_estimate(product="video", variations=len(prompts), video_family=caps.family)
    service = SeedanceService()

    async def create_one(index: int, progress: VariationProgress) -> VariationResult:
        try:
            provider_request = build_seedance_request(request, caps, media, prompt=prompts[index])
            progress.phase = "generating"
            task_id, request_id = await submit_seedance_request(
                ctx, service, caps, provider_request
            )
            return VariationResult(index=index, task_id=task_id, request_id=request_id)
        except ProviderError as exc:
            return VariationResult(
                index=index,
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
                index=index, error=VariationError(code="UNEXPECTED_ERROR", message=str(exc))
            )

    try:
        summary = await run_variation_batch(
            count=len(prompts),
            factory=create_one,
            batch_deadline=variation_batch_deadline(len(prompts), settings, persists=False),
            max_concurrent=DEFAULT_MAX_CONCURRENT,
        )
    finally:
        await service.close()
    log_info(
        "seedance_variations_complete",
        model=caps.model_id,
        total=summary.total,
        succeeded=summary.succeeded,
        failed=summary.failed,
    )
    return summary


def parse_created_at(raw: int | float | str | None) -> datetime | None:
    """Parse a provider ``created_at`` (epoch seconds or ISO-8601) to an aware datetime.

    Returns ``None`` when the value is missing or unparsable so the local age
    check is skipped and the provider has the final say.
    """
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, tz=UTC)
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


async def load_draft_task(draft_task_id: str, ctx: Context) -> GenerationTaskResponse | ToolResult:
    """Fetch a Draft task and check it can be rendered as a final video.

    Enforces task ownership, then rejects tasks that have not succeeded, were
    not created in Draft mode, or are past the provider's 7-day Draft window,
    so the caller gets an actionable error instead of a provider rejection.
    """
    await get_runtime(ctx).ownership_store.require_owner(
        "modelark", draft_task_id, get_principal(ctx)
    )
    service = GenerationTaskService()
    try:
        task, _ = await call_with_retry(lambda: service.get_task(draft_task_id))
    except ProviderError as exc:
        await context_log(ctx, "error", f"Failed to retrieve Draft task: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    if task.draft is False:
        raise ValueError(
            f"Task '{draft_task_id}' was not created in Draft mode. Create a Draft "
            "with draft=true first."
        )
    if task.status != "succeeded":
        raise ValueError(
            f"Draft task '{draft_task_id}' is {task.status or 'not ready'}; only a "
            "succeeded Draft task can be rendered as a final video. Poll it with "
            "gen_task_get until it succeeds."
        )
    created_at = parse_created_at(task.created_at)
    if created_at is not None and datetime.now(UTC) - created_at > DRAFT_TASK_MAX_AGE:
        raise ValueError(
            f"Draft task '{draft_task_id}' was created more than 7 days ago and can "
            "no longer be rendered as a final video. Create a new Draft task."
        )
    return task
