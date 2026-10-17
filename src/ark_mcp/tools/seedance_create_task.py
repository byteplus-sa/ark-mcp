"""``seedance_create_task`` tool — create Seedance video tasks for any configured model.

One tool covers every Seedance family (2.0, 2.0 Fast/Mini, 2.5 and the
whitelist-only 2.5 Premium). The input schema advertises the widest limits;
the model's own limits are checked at runtime from the capability registry
and every violation is reported at once.
"""

from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, JsonValue, model_validator

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import VideoCapabilities, get_capability_registry
from ark_mcp.domain.models import VariationError, VariationSummary
from ark_mcp.observability.logger import info as log_info
from ark_mcp.tools._asset_shared import preflight_seedance_assets
from ark_mcp.tools._capability_checks import check_video_request, raise_on_violations
from ark_mcp.tools._cost import estimate_cost
from ark_mcp.tools._seedance_shared import (
    DRAFT_RESOLUTION,
    DRAFT_REUSED_FIELDS,
    OMNI_REFERENCE_TASK_TYPE_DESCRIPTION,
    RECOMMENDED_POLL_AFTER_MS,
    MediaPayloads,
    OmniReferenceTaskType,
    SeedanceAudioInput,
    SeedanceImageInput,
    SeedanceVideoInput,
    build_seedance_request,
    execute_seedance_create,
    load_draft_task,
    media_payloads,
    run_seedance_variations,
    task_type_constraint_violations,
)
from ark_mcp.tools._task_execution import context_log

SeedanceRatio = Literal["16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive"]
MAX_VARIATIONS = 5

__all__ = [
    "SeedanceAudioInput",
    "SeedanceCreatePreview",
    "SeedanceCreateTaskInput",
    "SeedanceCreateTaskOutput",
    "SeedanceImageInput",
    "SeedanceTaskRef",
    "SeedanceVideoInput",
    "seedance_create_task",
]


class SeedanceCreateTaskInput(BaseModel):
    """Input model for ``seedance_create_task``."""

    prompt: str | None = Field(
        None,
        min_length=1,
        max_length=32000,
        description=(
            "Text prompt for the video (1-32,000 characters; ModelArk recommends at most about "
            "1,000 English words). Optional when media inputs or variation_prompts are given."
        ),
    )
    images: list[SeedanceImageInput] | None = Field(
        None,
        max_length=30,
        description=(
            "Reference images with optional roles (first_frame, last_frame, reference_image). "
            "Up to 30; the model's own maximum is checked at runtime (see ark_list_models). "
            'Each entry may be a URL string, "asset://<asset_id>", or {"url": "https://..."}; '
            "these default to role=reference_image."
        ),
    )
    videos: list[SeedanceVideoInput] | None = Field(
        None,
        max_length=10,
        description=(
            "Reference videos (up to 10; per-model maximum checked at runtime). Each entry may "
            'be a URL string, "asset://<asset_id>", or {"url": "https://..."}.'
        ),
    )
    audios: list[SeedanceAudioInput] | None = Field(
        None,
        max_length=10,
        description=(
            "Reference audio (up to 10; per-model maximum checked at runtime). Seedance 2.5 "
            "models accept audio as the only media input; Seedance 2.0 models need an image "
            'or video as well. Each entry may be a URL string, "asset://<asset_id>", or '
            '{"url": "https://..."}.'
        ),
    )
    model: str | None = Field(
        None,
        description=(
            "Exact configured Seedance model ID from ark_list_models(kind='video'). Omit to use "
            "the configured default. With draft_task_id, omit it to reuse the Draft task's model."
        ),
    )
    resolution: Literal["480p", "720p", "1080p", "4k"] | None = Field(
        None,
        description=(
            "Output resolution; the model's supported set is checked at runtime. draft=true "
            "allows only 480p (the default); draft_task_id allows only the model's Draft final "
            "resolution (the default)."
        ),
    )
    ratio: SeedanceRatio | None = Field(
        None,
        description=(
            "Output aspect ratio; 'adaptive' is the provider default. Edit, extend, first-frame "
            "and first/last-frame requests on Seedance 2.5 require it omitted or 'adaptive'."
        ),
    )
    duration: int | None = Field(
        None,
        ge=-1,
        le=30,
        description=(
            "Duration in seconds: -1 for auto, or 4 up to the model's maximum (15 for Seedance "
            "2.0, 30 for 2.5). omni_reference_task_type 'edit' requires -1 or omitted."
        ),
    )
    omni_reference_task_type: OmniReferenceTaskType | None = Field(
        None, description=OMNI_REFERENCE_TASK_TYPE_DESCRIPTION
    )
    output_format: Literal["mp4", "mov"] | None = Field(
        None,
        description=(
            "Container for the output video; Seedance 2.5 models only. 'mov' uses H.264, "
            "YUV 4:4:4 and PCM audio for post-production, and some players cannot play it."
        ),
    )
    generate_audio: bool | None = Field(
        None, description="Whether to generate an audio track for the video."
    )
    watermark: bool | None = Field(
        False, description="Whether to apply an AIGC watermark to the video."
    )
    return_last_frame: bool | None = Field(
        None, description="Whether to return the last frame as a separate image output."
    )
    execution_expires_after: int | None = Field(
        None,
        ge=3600,
        le=259200,
        description="Seconds before an unfinished task expires (3600-259200, i.e. 1 hour to 3 days).",
    )
    priority: int | None = Field(
        None,
        ge=0,
        le=9,
        description="Task priority (0-9); the model's range is checked at runtime.",
    )
    safety_identifier: str | None = Field(
        None,
        max_length=64,
        description="Optional identifier for content safety tracking (max 64 characters).",
    )
    draft: bool | None = Field(
        None,
        description=(
            "Draft mode step 1 (Seedance 2.5 models): when true, generates a low-cost 480p "
            "preview billed as a normal 480p video. Once it succeeds, pass its task_id as "
            "draft_task_id to render the final video. Mutually exclusive with draft_task_id."
        ),
    )
    draft_task_id: str | None = Field(
        None,
        min_length=1,
        description=(
            "Draft mode step 2: task ID of a succeeded Draft task (created with draft=true, at "
            "most 7 days ago) to render as the final video. The provider reuses the Draft's "
            "model, prompt, media, duration, ratio, seed, generate_audio and "
            "omni_reference_task_type, so those must be omitted. resolution must be omitted or "
            "the model's Draft final resolution (1080p for Seedance 2.5, 4k for Premium). "
            "return_last_frame, watermark, execution_expires_after, priority, safety_identifier "
            "and output_format may be set again. Requires variations=1."
        ),
    )
    variations: int = Field(
        1,
        ge=1,
        le=MAX_VARIATIONS,
        description=(
            "Number of tasks to create in parallel (1-5). Each variation is a separate billed "
            "task; partial failures are reported per variation."
        ),
    )
    variation_prompts: list[Annotated[str, Field(min_length=1, max_length=32000)]] | None = Field(
        None,
        description=(
            "One prompt per variation (each 1-32,000 characters), overriding prompt. Must have "
            "exactly `variations` entries."
        ),
    )
    dry_run: bool = Field(
        False,
        description=(
            "Validate the request and return the provider request bodies and a cost estimate "
            "without submitting or billing anything."
        ),
    )

    @model_validator(mode="after")
    def validate_duration_floor(self) -> SeedanceCreateTaskInput:
        """Seedance accepts -1 (auto) or at least 4 seconds."""
        if self.duration is not None and self.duration != -1 and self.duration < 4:
            raise ValueError(
                f"duration must be -1 (auto) or at least 4 seconds; got {self.duration}."
            )
        return self

    @model_validator(mode="after")
    def validate_draft_mode(self) -> SeedanceCreateTaskInput:
        """Enforce the provider's Draft mode parameter rules before spending quota."""
        if self.draft_task_id is not None:
            if self.draft is not None:
                raise ValueError(
                    "draft must be omitted with draft_task_id (they are mutually exclusive). "
                    "Use draft=true to create a Draft preview, then draft_task_id (without "
                    "draft) to render the final video."
                )
            reused = [
                name
                for name in DRAFT_REUSED_FIELDS
                if getattr(self, name) is not None and getattr(self, name) != []
            ]
            if reused:
                raise ValueError(
                    f"{', '.join(reused)} must be omitted with draft_task_id: the provider "
                    "reuses the Draft task's values and rejects them even when they match."
                )
            if self.variations != 1 or self.variation_prompts:
                raise ValueError(
                    "draft_task_id renders exactly one final video; omit variations and "
                    "variation_prompts."
                )
        elif self.draft:
            if self.resolution is None:
                self.resolution = DRAFT_RESOLUTION
            elif self.resolution != DRAFT_RESOLUTION:
                raise ValueError(
                    f"Draft tasks only support resolution '{DRAFT_RESOLUTION}'; got "
                    f"'{self.resolution}'. Omit resolution or set it to '{DRAFT_RESOLUTION}'."
                )
        return self

    @model_validator(mode="after")
    def validate_inputs_present(self) -> SeedanceCreateTaskInput:
        """Require something to generate from, and consistent variation prompts."""
        if self.variation_prompts is not None and len(self.variation_prompts) != self.variations:
            raise ValueError(
                f"variation_prompts must have exactly {self.variations} entries "
                f"(one per variation); got {len(self.variation_prompts)}."
            )
        if self.draft_task_id is not None:
            return self
        has_media = bool(self.images or self.videos or self.audios)
        if not (self.prompt or self.variation_prompts or has_media):
            raise ValueError("At least one of prompt, images, videos, or audios is required.")
        if self.variations > 1 and not (self.prompt or self.variation_prompts):
            raise ValueError("Either prompt or variation_prompts is required with variations > 1.")
        return self

    @model_validator(mode="after")
    def validate_task_type_constraints(self) -> SeedanceCreateTaskInput:
        """Reject task-type combinations the provider fails with TaskTypeConstraint."""
        violations = task_type_constraint_violations(
            task_type=self.omni_reference_task_type,
            ratio=self.ratio,
            duration=self.duration,
            has_videos=bool(self.videos),
            image_roles=[image.role for image in self.images or []],
        )
        if violations:
            raise ValueError(" ".join(violations))
        return self


class SeedanceTaskRef(BaseModel):
    """One submitted Seedance task (or a failed submission)."""

    index: int = Field(..., description="0-based variation index (0 for a single request).")
    task_id: str | None = Field(
        None,
        description=(
            "Provider task ID; poll it with gen_task_get. For draft=true, pass it as "
            "draft_task_id once the Draft has succeeded. Null when submission failed."
        ),
    )
    request_id: str | None = Field(
        None, description="Provider request ID for this submission, when returned."
    )
    error: VariationError | None = Field(
        None, description="Why this submission failed; null when it was accepted."
    )


class SeedanceCreatePreview(BaseModel):
    """What a dry run would have submitted."""

    requests: list[dict[str, JsonValue]] = Field(
        ...,
        description=(
            "Provider request bodies that would be sent, one per variation. HTTPS media URLs "
            "are reduced to scheme and host, inline Base64 data to its size, and asset:// "
            "references are kept."
        ),
    )
    estimated_cost_usd: float = Field(
        ...,
        description="Static estimate in USD from the server's price table for all requests; not a provider quote.",
    )


class SeedanceCreateTaskOutput(BaseModel):
    """Output model for ``seedance_create_task``."""

    model: str = Field(..., description="Resolved model ID.")
    family: str = Field(..., description="Resolved model family.")
    dry_run: bool = Field(False, description="True when nothing was submitted; see preview.")
    tasks: list[SeedanceTaskRef] = Field(
        default_factory=list,
        description="One entry per requested variation, in order; empty for a dry run.",
    )
    submitted: int = Field(0, description="Number of tasks the provider accepted.")
    failed: int = Field(0, description="Number of submissions that failed.")
    recommended_poll_after_ms: int | None = Field(
        None,
        description="Suggested delay in milliseconds before the first gen_task_get poll; null for a dry run.",
    )
    preview: SeedanceCreatePreview | None = Field(
        None, description="Request bodies and cost estimate; set only for a dry run."
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}


async def seedance_create_task(
    input: SeedanceCreateTaskInput, ctx: Context
) -> SeedanceCreateTaskOutput | ToolResult:
    """Create asynchronous Seedance video tasks for any configured Seedance model.

    Call ark_list_models(kind='video') for the configured model IDs and each
    model's limits (resolutions, duration range, reference counts, Draft
    support, task types, output formats); requests outside a model's limits
    are rejected with every problem listed. Set variations (up to 5) to
    create several tasks in parallel. Draft mode (Seedance 2.5): draft=true
    makes a cheap 480p preview, then draft_task_id renders it as the final
    video. dry_run=true validates and returns the request bodies and a cost
    estimate without billing. Run this tool through ark_job_submit (or native
    task augmentation); a dry run finishes on the first ark_job_get. Poll the
    returned task IDs with gen_task_get and never resubmit to poll.
    """
    settings = get_settings()
    if not settings.has_modelark:
        raise ValueError(
            "BYTEPLUS_MODELARK_API_KEY is not configured. Set it in .env to enable Seedance tools."
        )
    await context_log(ctx, "info", "Creating Seedance video generation task")
    await ctx.report_progress(progress=10, total=100)

    if input.draft_task_id is not None:
        draft_task = await load_draft_task(input.draft_task_id, ctx)
        if isinstance(draft_task, ToolResult):
            return draft_task
        if input.model is not None and input.model != draft_task.model:
            raise ValueError(
                f"Model '{input.model}' does not match the Draft task's model "
                f"'{draft_task.model}'. Omit model to reuse the Draft task's model."
            )
        input = input.model_copy(update={"model": draft_task.model})

    caps = get_capability_registry().resolve_video_model(input.model)
    input = _apply_draft_final_resolution(input, caps)
    raise_on_violations(
        check_video_request(input, caps),
        model_id=caps.model_id,
        family=caps.family.value,
        kind="video",
    )
    media = media_payloads(input)
    await preflight_seedance_assets(ctx, media.asset_ids)
    prompts: list[str | None] = (
        list(input.variation_prompts)
        if input.variation_prompts
        else [input.prompt] * input.variations
    )

    if input.dry_run:
        return _preview(input, caps, media, prompts)
    if input.variations == 1:
        return await _create_single(input, ctx, caps, media)
    summary = await run_seedance_variations(input, ctx, caps, media, prompts)
    await ctx.report_progress(progress=100, total=100)
    return _variations_output(caps, summary)


def _apply_draft_final_resolution(
    input: SeedanceCreateTaskInput, caps: VideoCapabilities
) -> SeedanceCreateTaskInput:
    if input.draft_task_id is None or caps.draft_final_resolution is None:
        return input
    final = caps.draft_final_resolution
    if input.resolution is None:
        return input.model_copy(update={"resolution": final})
    if input.resolution != final:
        raise ValueError(
            f"Resolution '{input.resolution}' is not supported for a final video rendered "
            f"from a Draft task on '{caps.model_id}'. Omit resolution or set it to '{final}'."
        )
    return input


async def _create_single(
    input: SeedanceCreateTaskInput,
    ctx: Context,
    caps: VideoCapabilities,
    media: MediaPayloads,
) -> SeedanceCreateTaskOutput | ToolResult:
    result = await execute_seedance_create(input, ctx, caps, media)
    if isinstance(result, ToolResult):
        return result
    task_id, request_id = result
    log_info("seedance_create_task_complete", task_id=task_id, model=caps.model_id)
    return SeedanceCreateTaskOutput(
        model=caps.model_id,
        family=caps.family.value,
        tasks=[SeedanceTaskRef(index=0, task_id=task_id, request_id=request_id)],
        submitted=1,
        failed=0,
        recommended_poll_after_ms=RECOMMENDED_POLL_AFTER_MS,
    )


def _variations_output(
    caps: VideoCapabilities, summary: VariationSummary
) -> SeedanceCreateTaskOutput:
    return SeedanceCreateTaskOutput(
        model=caps.model_id,
        family=caps.family.value,
        tasks=[
            SeedanceTaskRef(
                index=result.index,
                task_id=result.task_id,
                request_id=result.request_id,
                error=result.error,
            )
            for result in summary.variations
        ],
        submitted=summary.succeeded,
        failed=summary.failed,
        recommended_poll_after_ms=RECOMMENDED_POLL_AFTER_MS,
    )


def _preview(
    input: SeedanceCreateTaskInput,
    caps: VideoCapabilities,
    media: MediaPayloads,
    prompts: list[str | None],
) -> SeedanceCreateTaskOutput:
    requests = [
        _redact(
            build_seedance_request(input, caps, media, prompt=prompt).model_dump(exclude_none=True)
        )
        for prompt in prompts
    ]
    cost = estimate_cost(product="video", variations=len(prompts), video_family=caps.family)
    return SeedanceCreateTaskOutput(
        model=caps.model_id,
        family=caps.family.value,
        dry_run=True,
        preview=SeedanceCreatePreview(requests=requests, estimated_cost_usd=cost),
    )


def _redact(body: dict[str, JsonValue]) -> dict[str, JsonValue]:
    content = body.get("content")
    if isinstance(content, list):
        body["content"] = [_redact_part(part) for part in content]
    return body


def _redact_part(part: JsonValue) -> JsonValue:
    if not isinstance(part, dict):
        return part
    redacted: dict[str, JsonValue] = dict(part)
    for key in ("image_url", "video_url", "audio_url"):
        media = redacted.get(key)
        if isinstance(media, dict) and isinstance(media.get("url"), str):
            redacted[key] = {**media, "url": _redact_url(str(media["url"]))}
    return redacted


def _redact_url(url: str) -> str:
    if url.startswith("data:"):
        return f"<base64: {len(url)} bytes>"
    if url.startswith("https://"):
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"
    return url
