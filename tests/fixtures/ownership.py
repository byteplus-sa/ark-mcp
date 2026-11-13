"""Ownership-ledger assertions shared by tool integration tests."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ark_mcp.security.auth_context import AuthContext


async def owned_task_ids(runtime: Any, provider: str, owner: AuthContext | None = None) -> set[str]:
    """Return every task ID ``owner`` (the local principal by default) owns for ``provider``."""
    owned = await runtime.ownership_store.list_owned_tasks(
        provider,
        owner or AuthContext(),
        since=datetime(2000, 1, 1, tzinfo=UTC),
        before=None,
        limit=1000,
    )
    return {task.task_id for task in owned}
