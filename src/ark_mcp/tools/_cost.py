"""Cost estimation and rate limiting for parallel generation.

Provides cost estimation constants and logging for billable operations.
Rate limiting is handled via ``asyncio.Semaphore`` directly in each
variation tool handler.
"""

from __future__ import annotations

from ark_mcp.observability.logger import info as log_info

# Provider cost estimates (USD per unit).
COST_PER_IMAGE = 0.03
COST_PER_AUDIO_SECOND = 0.0031
COST_PER_VIDEO_TASK = 0.07
COST_PER_VIDEO_TASK_2_5 = 0.35
COST_PER_STT_SECOND = 0.0006
COST_UNDERSTANDING_INPUT_PER_MTOK = 0.50
COST_UNDERSTANDING_OUTPUT_PER_MTOK = 3.00
COST_PER_3D_TASK_HYPER3D = 0.25
COST_PER_3D_TASK_HITEM3D = 1.0

# Default max concurrent provider calls.
DEFAULT_MAX_CONCURRENT = 5

# Model-specific cost overrides for video tasks.
_VIDEO_COST_OVERRIDES: dict[str, float] = {}


def register_video_cost_override(model_id: str, cost_per_task: float) -> None:
    """Register a model-specific cost-per-task override for video generation."""
    _VIDEO_COST_OVERRIDES[model_id] = cost_per_task


_SEEDANCE_2_5_FAMILIES = frozenset({"seedance_2_5", "seedance_2_5_premium"})


def _video_cost_for_model(model_id: str | None, family: str | None = None) -> float:
    """Return the cost-per-task for a video model, keyed on its registry family.

    A model-specific override wins. Pricing follows the family so that a
    custom endpoint ID bound to Seedance 2.5 is priced as 2.5.
    """
    if model_id and model_id in _VIDEO_COST_OVERRIDES:
        return _VIDEO_COST_OVERRIDES[model_id]
    if family in _SEEDANCE_2_5_FAMILIES:
        return COST_PER_VIDEO_TASK_2_5
    return COST_PER_VIDEO_TASK


def _3d_cost_for_model(model_id: str | None, family: str | None = None) -> float:
    """Return the cost-per-task for a given 3D model, keyed on family."""
    if family is not None:
        return COST_PER_3D_TASK_HITEM3D if family == "seed3d_hitem3d" else COST_PER_3D_TASK_HYPER3D
    if model_id and "hitem3d" in model_id:
        return COST_PER_3D_TASK_HITEM3D
    return COST_PER_3D_TASK_HYPER3D


def estimate_cost(
    *,
    product: str,
    variations: int,
    duration_seconds: float = 0.0,
    prompt_tokens: int | None = None,
    max_tokens: int | None = None,
    model_id: str | None = None,
    seed3d_family: str | None = None,
    video_family: str | None = None,
) -> float:
    """Estimate the cost of a parallel generation batch.

    Args:
        product: "image", "audio", "video", "3d", "stt", or "understanding".
        variations: Number of variations.
        duration_seconds: Expected output duration (audio only).
        prompt_tokens: Estimated input tokens (understanding only).
        max_tokens: Maximum output tokens (understanding only).
        model_id: Model ID for model-specific pricing (video only).
        seed3d_family: Family for 3D pricing ("seed3d_hyper3d" or "seed3d_hitem3d").
        video_family: Registry family for video pricing (e.g. "seedance_2_5").

    Returns:
        Estimated cost in USD.
    """
    if product == "image":
        return round(variations * COST_PER_IMAGE, 2)
    if product == "audio":
        return round(variations * max(duration_seconds, 10) * COST_PER_AUDIO_SECOND, 2)
    if product == "video":
        return round(variations * _video_cost_for_model(model_id, video_family), 2)
    if product == "3d":
        return round(variations * _3d_cost_for_model(model_id, seed3d_family), 2)
    if product == "stt":
        return round(variations * max(duration_seconds, 10) * COST_PER_STT_SECOND, 2)
    if product == "understanding":
        out = (max_tokens or 0) / 1_000_000 * COST_UNDERSTANDING_OUTPUT_PER_MTOK
        inp = (prompt_tokens or 0) / 1_000_000 * COST_UNDERSTANDING_INPUT_PER_MTOK
        return round(variations * (inp + out), 4)
    return 0.0


def log_cost_estimate(
    *,
    product: str,
    variations: int,
    duration_seconds: float = 0.0,
    prompt_tokens: int | None = None,
    max_tokens: int | None = None,
    model_id: str | None = None,
    seed3d_family: str | None = None,
    video_family: str | None = None,
) -> float:
    """Log a cost estimate before dispatching a batch."""
    cost = estimate_cost(
        product=product,
        variations=variations,
        duration_seconds=duration_seconds,
        prompt_tokens=prompt_tokens,
        max_tokens=max_tokens,
        model_id=model_id,
        seed3d_family=seed3d_family,
        video_family=video_family,
    )
    log_info(
        "cost_estimate",
        product=product,
        variations=variations,
        estimated_cost_usd=cost,
    )
    return cost
