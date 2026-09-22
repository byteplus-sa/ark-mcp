"""Server-derived queue timing for Seedance tasks.

ModelArk publishes no queue position or ETA, so the server reports only what
it can derive honestly from a task's own timestamps and expiry setting.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ark_mcp.domain.models import SeedanceQueueInfo

if TYPE_CHECKING:
    from ark_mcp.providers.modelark.schemas import SeedanceTaskResponse

# Provider default for execution_expires_after when a task does not report it.
DEFAULT_EXECUTION_EXPIRES_AFTER_SECONDS = 172_800  # 48 hours

_ACTIVE_STATUSES = frozenset({"queued", "running"})


def _parse(raw: int | float | str | None) -> datetime | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, tz=UTC)
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.fromtimestamp(float(raw), tz=UTC)
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _minutes(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def queue_info(
    task: SeedanceTaskResponse, *, now: datetime | None = None
) -> SeedanceQueueInfo | None:
    """Return derived queue timing for a queued/running task, or None when finished."""
    if task.status not in _ACTIVE_STATUSES:
        return None
    current = now or datetime.now(UTC)
    created = _parse(task.created_at)
    updated = _parse(task.updated_at)
    tier = task.service_tier

    queued_seconds: int | None = None
    running_seconds: int | None = None
    if created is not None:
        if task.status == "queued":
            queued_seconds = max(0, int((current - created).total_seconds()))
        elif updated is not None:
            queued_seconds = max(0, int((updated - created).total_seconds()))
    if task.status == "running" and updated is not None:
        running_seconds = max(0, int((current - updated).total_seconds()))

    expires_after = task.execution_expires_after
    estimated = expires_after is None
    expires_at: str | None = None
    if created is not None:
        seconds = expires_after or DEFAULT_EXECUTION_EXPIRES_AFTER_SECONDS
        expires_at = (created + timedelta(seconds=seconds)).isoformat()

    parts: list[str] = []
    if task.status == "queued" and queued_seconds is not None:
        parts.append(f"Queued {_minutes(queued_seconds)}")
    elif task.status == "running":
        parts.append(
            f"Running {_minutes(running_seconds)}" if running_seconds is not None else "Running"
        )
    if tier:
        parts[-1:] = [f"{parts[-1]} on {tier} tier"] if parts else [f"{tier} tier"]
    if expires_at:
        parts.append(
            f"provider fails it if unfinished by {expires_at}"
            + (" (assumed 48h default)" if estimated else "")
        )
    parts.append("ModelArk does not publish queue position or ETA")
    return SeedanceQueueInfo(
        queued_seconds=queued_seconds,
        running_seconds=running_seconds,
        service_tier=tier,
        expires_at=expires_at,
        expires_at_estimated=estimated,
        hint="; ".join(parts) + ".",
    )
