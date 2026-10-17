"""Model-dependent request validation driven by the capability registry.

Pydantic input models enforce rules that hold for every model. These pure
functions check a request against one resolved model's capabilities and
return every violation at once, so a caller fixes a request in one round
trip instead of discovering limits one rejection at a time.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from ark_mcp.config.model_capabilities import VideoCapabilities


class VideoRequest(Protocol):
    """Fields of a Seedance create request that depend on the model."""

    resolution: str | None
    ratio: str | None
    duration: int | None
    priority: int | None
    execution_expires_after: int | None
    images: Sequence[object] | None
    videos: Sequence[object] | None
    audios: Sequence[object] | None
    draft: bool | None
    draft_task_id: str | None
    omni_reference_task_type: str | None
    output_format: str | None


def check_video_request(request: VideoRequest, caps: VideoCapabilities) -> list[str]:
    """Return every way the request exceeds the video model's capabilities."""
    violations: list[str] = []
    if request.resolution and request.resolution not in caps.supported_resolutions:
        violations.append(
            f"resolution '{request.resolution}' is not supported; allowed: "
            f"{list(caps.supported_resolutions)}"
        )
    if request.ratio and request.ratio not in caps.supported_ratios:
        violations.append(
            f"ratio '{request.ratio}' is not supported; allowed: {list(caps.supported_ratios)}"
        )
    low, high = caps.duration_range
    if request.duration not in (None, -1) and not low <= request.duration <= high:
        violations.append(
            f"duration {request.duration}s is outside [{low}, {high}]; use -1 for auto"
        )
    for label, value, (low, high) in (
        ("priority", request.priority, caps.priority_range),
        (
            "execution_expires_after",
            request.execution_expires_after,
            caps.execution_expires_after_range,
        ),
    ):
        if value is not None and not low <= value <= high:
            violations.append(f"{label} {value} is outside [{low}, {high}]")
    for label, items, limit in (
        ("reference images", request.images, caps.max_reference_images),
        ("reference videos", request.videos, caps.max_reference_videos),
        ("reference audios", request.audios, caps.max_reference_audios),
    ):
        if items and len(items) > limit:
            violations.append(f"{len(items)} {label} exceed the maximum of {limit}")
    if (request.draft or request.draft_task_id) and not caps.supports_draft:
        violations.append("Draft mode (draft, draft_task_id) is not supported")
    audio_only = bool(request.audios) and not (request.images or request.videos)
    if audio_only and not caps.supports_audio_only_input:
        violations.append("audio cannot be the only media input; add an image or video")
    if request.omni_reference_task_type and (
        request.omni_reference_task_type not in caps.supported_task_types
    ):
        violations.append(
            "omni_reference_task_type is not supported; allowed: "
            f"{list(caps.supported_task_types) or 'none'}"
        )
    if request.output_format and request.output_format not in caps.supported_output_formats:
        violations.append(
            f"output_format '{request.output_format}' is not supported; allowed: "
            f"{list(caps.supported_output_formats) or 'none'}"
        )
    return violations


def raise_on_violations(violations: list[str], *, model_id: str, family: str, kind: str) -> None:
    """Raise one error listing every violation and where to look up the limits."""
    if not violations:
        return
    bullet_list = "\n".join(f"- {violation}" for violation in violations)
    raise ValueError(
        f"Request is not valid for model '{model_id}' ({family}):\n{bullet_list}\n"
        f"Call ark_list_models(kind='{kind}') for per-model limits."
    )
