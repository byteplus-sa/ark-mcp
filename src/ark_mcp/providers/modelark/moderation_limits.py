"""Runtime-owned quota guard for specs/SPEC_MODELARK_MODERATION_CONTRACT.md."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


class ModerationQueryLimiter:
    """Admit at most ten requests per rolling minute without quota waits."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = asyncio.Lock()
        self._requests: deque[float] = deque()

    async def try_reserve(self) -> float:
        """Return zero on admission, otherwise seconds until quota is available."""
        async with self._lock:
            now = self._clock()
            while self._requests and self._requests[0] <= now - 60.0:
                self._requests.popleft()
            if len(self._requests) >= 10:
                return self._requests[0] + 60.0 - now
            self._requests.append(now)
            return 0.0
