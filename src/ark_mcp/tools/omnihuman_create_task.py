"""``omnihuman_create_task`` tool — submit an OmniHuman 2.0 image-plus-audio video task."""

from __future__ import annotations

from typing import Literal

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field, ValidationError

from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.vision_cv.omnihuman import OmniHumanService
from ark_mcp.providers.vision_cv.schemas import PROMPT_MAX_LENGTH, OmniHumanSubmitRequest
from ark_mcp.runtime import get_principal, get_runtime
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._omnihuman_shared import (
    MediaRefError,
    resolve_media_ref,
)
from ark_mcp.tools._task_execution import context_log

RECOMMENDED_POLL_AFTER_MS = 10_000


class OmniHumanCreateTaskInput(BaseModel):
    """Input for ``omnihuman_create_task``."""

    image: str = Field(
        description=(
            "Portrait image of the person to animate: a public HTTPS URL or a "
            "seed-media://artifacts/<id> artifact (needs object-storage artifacts; otherwise "
            "upload with media_upload and pass the presigned URL). jpeg, png, webp, bmp, tiff, "
            "gif, heic or heif; under 30 MB; aspect ratio (width/height) between 0.4 and 2.5; "
            "each side 300-6000 px."
        )
    )
    audio: str = Field(
        description=(
            "Speech or audio that drives the performance: a public HTTPS URL or a "
            "seed-media://artifacts/<id> artifact. wav or mp3; under 15 MB; 4-300 seconds. "
            "The video length follows the audio length."
        )
    )
    prompt: str | None = Field(
        default=None,
        max_length=PROMPT_MAX_LENGTH,
        description=(
            "Optional text that steers the scene, movements and camera. Chinese, English, "
            f"Japanese, Korean, Mexican Spanish and Indonesian are supported. Up to "
            f"{PROMPT_MAX_LENGTH} characters."
        ),
    )
    watermark: bool = Field(
        default=True,
        description="Whether the provider adds its watermark to the video. Defaults to true.",
    )


class OmniHumanCreateTaskOutput(BaseModel):
    """Accepted OmniHuman 2.0 task."""

    provider: Literal["byteplus-vision-cv"] = Field(
        default="byteplus-vision-cv", description="Provider surface that accepted the task."
    )
    status: Literal["accepted"] = Field(
        default="accepted", description="The task was accepted and is queued for generation."
    )
    task_id: str = Field(
        description="Task ID to pass to omnihuman_get_task or omnihuman_cancel_task."
    )
    request_id: str | None = Field(
        default=None, description="Provider diagnostic request ID, when returned."
    )
    recommended_poll_after_ms: int = Field(
        description="Suggested delay before polling omnihuman_get_task, in milliseconds; a heuristic."
    )


async def omnihuman_create_task(
    input: OmniHumanCreateTaskInput, ctx: Context
) -> OmniHumanCreateTaskOutput | ToolResult:
    """Generate a talking or performing video of a person from one image and one audio clip.

    Uses BytePlus OmniHuman 2.0. Returns a task ID immediately; poll it with
    omnihuman_get_task, which persists the finished video as a durable artifact
    (the provider's video URL expires after one hour). The submission is never
    retried automatically because completion can be ambiguous after a timeout.
    Image and audio accept public HTTPS URLs or seed-media artifacts. Requires
    MCP task-augmented execution (use ark_job_submit when the client lacks it).
    """
    runtime = get_runtime(ctx)
    settings = runtime.settings
    if not settings.has_omnihuman:
        raise ValueError(
            "OmniHuman is not enabled. Set BYTEPLUS_OMNIHUMAN_ENABLED=true and BYTEPLUS_CV_API_KEY."
        )

    owner = get_principal(ctx)
    try:
        image_url = await resolve_media_ref(
            input.image, "image", store=runtime.artifact_store, owner=owner
        )
        audio_url = await resolve_media_ref(
            input.audio, "audio", store=runtime.artifact_store, owner=owner
        )
        request = OmniHumanSubmitRequest.model_validate(
            {
                "image_url": image_url,
                "audio_url": audio_url,
                "prompt": input.prompt,
                "enable_watermark": input.watermark,
            }
        )
    except MediaRefError as exc:
        return ToolResult(content=[{"type": "text", "text": str(exc)}], is_error=True)
    except ValidationError:
        return ToolResult(
            content=[
                {
                    "type": "text",
                    "text": "The image or audio reference did not resolve to an HTTPS URL.",
                }
            ],
            is_error=True,
        )
    except ProviderError as exc:
        return provider_error_result(exc)

    service = OmniHumanService()
    await context_log(ctx, "info", "Submitting OmniHuman 2.0 task")
    await ctx.report_progress(progress=20, total=100)
    try:
        async with runtime.provider_limiters.acquire("vision-cv", owner):
            submission = await service.create(request)
    except ProviderError as exc:
        await context_log(ctx, "error", f"OmniHuman submission failed: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()

    await runtime.ownership_store.record("vision-cv", submission.task_id, owner)
    await ctx.report_progress(progress=100, total=100)
    log_info("omnihuman_create_task_accepted", task_id=submission.task_id)
    return OmniHumanCreateTaskOutput(
        task_id=submission.task_id,
        request_id=submission.request_id,
        recommended_poll_after_ms=RECOMMENDED_POLL_AFTER_MS,
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
