"""Shared helpers for parallel variation generation.

Provides ``generate_seeds``, ``resolve_prompts``, ``gather_with_timeout``,
``run_variation_batch``, ``variation_batch_deadline``, ``VariationProgress``,
and ``DEFAULT_MAX_CONCURRENT`` used by the variation tool handlers.
"""

from __future__ import annotations

import asyncio
import math
import secrets
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from ark_mcp.config.env import Settings
from ark_mcp.domain.models import VariationError, VariationResult, VariationSummary
from ark_mcp.observability.logger import info as log_info
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.tools._cost import DEFAULT_MAX_CONCURRENT


def generate_seeds(base_seed: int | None, count: int) -> list[int | None]:
    """Generate distinct seeds for each variation.

    - ``base_seed=None`` → ``[None] * count`` (provider randomizes; seed
      is not recorded in VariationResult).
    - ``base_seed=-1`` → random seeds for each variation (client picks;
      recorded for reproducibility).
    - ``base_seed=N`` → ``[N, N+1, N+2, ...]`` (deterministic sequence,
      wrapped modulo 2147483648 to stay within the API's valid range).
    """
    if base_seed is None:
        return [None] * count
    if base_seed == -1:
        return [secrets.randbelow(2147483648) for _ in range(count)]
    return [(base_seed + i) % 2147483648 for i in range(count)]


def resolve_prompts(
    base_prompt: str | None,
    variation_prompts: list[str] | None,
    count: int,
) -> list[str]:
    """Resolve the prompt for each variation.

    If ``variation_prompts`` is provided, it must have ``count`` entries.
    Otherwise, ``base_prompt`` is used for all variations.
    """
    if variation_prompts:
        return list(variation_prompts)
    if base_prompt is None:
        raise ValueError("Either base_prompt or variation_prompts must be provided.")
    return [base_prompt] * count


async def gather_with_timeout(
    coros: Sequence[Awaitable[Any]],
    timeout: float,
) -> list[Any]:
    """Run N coroutines in parallel with a per-coroutine timeout.

    Wraps each coroutine in ``asyncio.wait_for``. Collects all results
    (including exceptions and timeouts) via ``asyncio.gather`` with
    ``return_exceptions=True``.
    """
    timed_coros = [asyncio.wait_for(coro, timeout=timeout) for coro in coros]
    results = await asyncio.gather(*timed_coros, return_exceptions=True)
    return list(results)


VariationPhase = Literal["queued", "generating", "persisting"]


@dataclass
class VariationProgress:
    """Mutable per-variation progress shared between a factory and the batch runner.

    The factory sets ``phase`` before each step. When the batch deadline
    cancels a variation, the runner reads ``phase`` to classify the failure
    and returns ``partial`` (for example an unpersisted provider-URL result)
    if the factory recorded one before persisting.
    """

    phase: VariationPhase = "queued"
    partial: VariationResult | None = None


def variation_batch_deadline(
    count: int,
    settings: Settings,
    *,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
    provider_attempts: int = 3,
    persists: bool = True,
) -> float:
    """Return a generous overall deadline (seconds) for a variation batch.

    Each phase is already bounded (httpx timeouts, the retry policy, and the
    downloader's per-attempt timeout), so this is only a safety net for a
    stuck batch. It allows every wave of ``max_concurrent`` variations to use
    its full provider retry budget plus its full download retry budget.
    """
    waves = max(1, math.ceil(count / max(1, max_concurrent)))
    per_variation = (settings.request_timeout_ms / 1000) * provider_attempts
    if persists:
        per_variation += (
            settings.artifact_download_timeout_seconds * settings.artifact_download_max_attempts
        )
    return waves * per_variation


def _deadline_error(idx: int, progress: VariationProgress) -> VariationResult:
    if progress.phase == "persisting" and progress.partial is not None:
        return progress.partial
    if progress.phase == "queued":
        error = VariationError(
            code="QUEUE_TIMEOUT",
            message=f"Variation {idx} never started before the batch deadline.",
            retryable=True,
            ambiguous_completion=False,
            phase="queued",
        )
    else:
        error = VariationError(
            code="TIMEOUT",
            message=(
                f"Variation {idx} hit the batch deadline while {progress.phase}. "
                "The provider may have completed it; do not retry blindly."
            ),
            retryable=False,
            ambiguous_completion=True,
            phase=progress.phase,
        )
    return VariationResult(index=idx, error=error)


async def run_variation_batch(
    count: int,
    factory: Callable[[int, VariationProgress], Awaitable[VariationResult]],
    *,
    batch_deadline: float,
    max_concurrent: int = DEFAULT_MAX_CONCURRENT,
) -> VariationSummary:
    """Run a batch of variation coroutines with bounded concurrency.

    Individual variations have no wall-clock timeout of their own: every
    phase (provider call, retries, download) is bounded where it happens, and
    time spent waiting for a concurrency slot must never count against a
    variation. ``batch_deadline`` bounds the whole batch; variations still
    running at the deadline are cancelled and classified by their phase.

    Args:
        count: Number of variations to generate.
        factory: Callable(index, progress) -> coroutine producing a VariationResult.
        batch_deadline: Overall deadline in seconds for the whole batch.
        max_concurrent: Maximum concurrent variations.

    Returns:
        VariationSummary with succeeded/failed counts and per-variation results.
    """
    limiter = asyncio.Semaphore(max_concurrent)
    progresses = [VariationProgress() for _ in range(count)]

    async def _guarded(idx: int) -> VariationResult:
        async with limiter:
            return await factory(idx, progresses[idx])

    tasks = [asyncio.create_task(_guarded(i)) for i in range(count)]
    try:
        await asyncio.wait(tasks, timeout=batch_deadline)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    variation_results: list[VariationResult] = []
    for i, task in enumerate(tasks):
        # Every task is done after the gather above, so classify from its own
        # state: a task cancelled at the deadline may still have returned a
        # real result, which must not be discarded.
        if task.cancelled():
            log_warning("variation_deadline", index=i, phase=progresses[i].phase)
            variation_results.append(_deadline_error(i, progresses[i]))
            continue
        exc = task.exception()
        if exc is not None:
            log_warning("variation_error", index=i, error=type(exc).__name__)
            variation_results.append(
                VariationResult(
                    index=i,
                    error=VariationError(code="GATHER_ERROR", message=str(exc)),
                )
            )
        else:
            variation_results.append(task.result())

    succeeded = sum(1 for r in variation_results if r.artifact is not None or r.task_id is not None)
    failed = count - succeeded

    log_info(
        "variation_batch_complete",
        total=count,
        succeeded=succeeded,
        failed=failed,
    )
    return VariationSummary(
        total=count,
        succeeded=succeeded,
        failed=failed,
        variations=variation_results,
    )
