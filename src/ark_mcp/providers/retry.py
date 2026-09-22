"""Deterministic retry policy for explicitly safe provider failures."""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.observability.metrics import RETRY_ATTEMPTS

AsyncSleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.25
    max_delay_seconds: float = 4.0
    jitter_ratio: float = 0.2
    retry_timeouts: bool = True
    """When false, ``TIMEOUT`` errors are re-raised immediately even if retryable.

    Used for long, billed calls (for example chat completions) where a second
    full-length attempt would double latency and cost, while 429/5xx are still
    retried.
    """

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one.")
        if self.base_delay_seconds < 0 or self.max_delay_seconds < 0:
            raise ValueError("Retry delays cannot be negative.")
        if not 0 <= self.jitter_ratio <= 1:
            raise ValueError("jitter_ratio must be between zero and one.")


async def call_with_retry[T](
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy | None = None,
    sleep: AsyncSleep = asyncio.sleep,
    random_value: Callable[[], float] = random.random,
) -> T:
    """Retry only non-ambiguous provider errors explicitly marked retryable."""
    resolved_policy = policy or RetryPolicy()
    for attempt in range(1, resolved_policy.max_attempts + 1):
        try:
            return await operation()
        except ProviderError as exc:
            can_retry = (
                exc.retryable
                and not exc.ambiguous_completion
                and (resolved_policy.retry_timeouts or exc.code != "TIMEOUT")
                and attempt < resolved_policy.max_attempts
            )
            if not can_retry:
                raise

            RETRY_ATTEMPTS.labels(
                provider=exc.provider,
                operation=exc.operation,
            ).inc()

            retry_after = exc.error.retry_after_seconds
            if retry_after is not None:
                delay = min(retry_after, resolved_policy.max_delay_seconds)
            else:
                base = min(
                    resolved_policy.base_delay_seconds * (2 ** (attempt - 1)),
                    resolved_policy.max_delay_seconds,
                )
                jitter = base * resolved_policy.jitter_ratio * ((random_value() * 2) - 1)
                delay = max(0.0, base + jitter)
            log_info(
                "retry_attempt",
                provider=exc.provider,
                operation=exc.operation,
                attempt=attempt,
                max_attempts=resolved_policy.max_attempts,
                delay_seconds=round(delay, 3),
            )
            await sleep(delay)

    raise AssertionError("Retry loop exited unexpectedly.")
