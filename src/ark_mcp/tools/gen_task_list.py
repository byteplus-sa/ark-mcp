"""``gen_task_list`` tool — list the caller's ModelArk generation tasks, newest first.

The provider list API takes task IDs as repeated query parameters, so sending
every owned ID overflows the URL for active principals. Non-local principals
page through their owned IDs from the ownership store instead, a bounded
slice at a time, and the cursor is a keyset position in that store.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
from datetime import UTC, datetime, timedelta

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import BaseModel, Field

from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.models import GenTaskStatus, TaskError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.generation_tasks import GenerationTaskService, task_timestamp
from ark_mcp.providers.modelark.schemas import GenerationTaskResponse
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import OwnedTask, get_principal, get_runtime
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._generation_tasks_shared import TaskKind, classify_task
from ark_mcp.tools._task_execution import context_log

LIST_WINDOW = timedelta(days=7)
OWNED_SLICE_SIZE = 100
MAX_SLICES_PER_PAGE = 5
_PROVIDER_PAGE_SIZE = 100
_CURSOR_VERSION = 1


class GenTaskListInput(BaseModel):
    """Input model for ``gen_task_list``."""

    page_size: int = Field(20, ge=1, le=100, description="Maximum number of tasks to return.")
    cursor: str | None = Field(
        None,
        description="Opaque next_cursor from a previous call made with the same filters.",
    )
    status: GenTaskStatus | None = Field(
        None,
        description="Only return tasks in this status: queued, running, succeeded, failed, cancelled, or expired.",
    )
    model: str | None = Field(None, description="Only return tasks run by this exact model ID.")
    task_ids: list[str] | None = Field(
        None,
        max_length=100,
        description="Only return these task IDs (up to 100); IDs the caller does not own are skipped.",
    )


class GenTaskSummary(BaseModel):
    """Summary of one ModelArk generation task."""

    task_id: str = Field(..., description="Provider task ID; pass it to gen_task_get for outputs.")
    model: str = Field(..., description="Model ID that ran the task.")
    kind: TaskKind = Field(
        ..., description="Output kind from the registry binding for model: video, 3d, or unknown."
    )
    status: GenTaskStatus = Field(..., description="Task status.")
    created_at: str = Field(..., description="ISO-8601 task creation time.")
    error: TaskError | None = Field(None, description="Failure details when the task failed.")


class GenTaskPage(BaseModel):
    """Output model for ``gen_task_list``."""

    tasks: list[GenTaskSummary] = Field(..., description="Matching tasks, newest first.")
    next_cursor: str | None = Field(
        None,
        description=(
            "Pass back as cursor to continue; null when there are no more tasks. A page may "
            "hold fewer than page_size tasks while next_cursor is non-null."
        ),
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}


async def gen_task_list(input: GenTaskListInput, ctx: Context) -> GenTaskPage | ToolResult:
    """List your ModelArk generation tasks (Seedance video and 3D), newest first.

    Covers tasks created in the last 7 days, the provider's list window. Filter
    by status, model, or task_ids, and follow next_cursor until it is null;
    keep the same filters while paging. Use gen_task_get for outputs.
    """
    await context_log(ctx, "info", "Listing generation tasks")
    owner = get_principal(ctx)
    filter_hash = _filter_hash(input)
    service = GenerationTaskService()
    try:
        if owner.is_local:
            page = await _list_local(service, input, filter_hash)
        else:
            page = await _list_owned(service, input, filter_hash, ctx)
    except ProviderError as exc:
        await context_log(ctx, "error", f"Failed to list tasks: {exc.message}")
        return provider_error_result(exc)
    finally:
        await service.close()
    log_info("generation_tasks_listed", returned=len(page.tasks), more=page.next_cursor is not None)
    return page


async def _list_local(
    service: GenerationTaskService, input: GenTaskListInput, filter_hash: str
) -> GenTaskPage:
    cursor = _decode_cursor(input.cursor, filter_hash)
    page_num = cursor.get("page", 1) if cursor else 1
    if not isinstance(page_num, int) or page_num < 1:
        raise ValueError("cursor is not a valid gen_task_list cursor.")
    response, _ = await call_with_retry(
        lambda: service.list_tasks(
            page=page_num,
            page_size=input.page_size,
            status=input.status,
            task_ids=input.task_ids,
            model=input.model,
        )
    )
    more = (
        response.has_more
        if response.has_more is not None
        else (len(response.data) == input.page_size)
    )
    next_cursor = _encode_cursor({"page": page_num + 1}, filter_hash) if more else None
    return GenTaskPage(tasks=[_summary(task) for task in response.data], next_cursor=next_cursor)


async def _list_owned(
    service: GenerationTaskService, input: GenTaskListInput, filter_hash: str, ctx: Context
) -> GenTaskPage:
    store = get_runtime(ctx).ownership_store
    owner = get_principal(ctx)
    before = _owned_position(_decode_cursor(input.cursor, filter_hash))
    since = datetime.now(UTC) - LIST_WINDOW
    wanted = set(input.task_ids) if input.task_ids is not None else None

    tasks: list[GenTaskSummary] = []
    for _ in range(MAX_SLICES_PER_PAGE):
        owned = await store.list_owned_tasks(
            "modelark", owner, since=since, before=before, limit=OWNED_SLICE_SIZE
        )
        if not owned:
            return GenTaskPage(tasks=tasks, next_cursor=None)
        candidates = [row for row in owned if wanted is None or row.task_id in wanted]
        found = await _fetch_slice(service, input, [row.task_id for row in candidates])
        for row in candidates:
            task = found.get(row.task_id)
            if task is None:
                continue
            tasks.append(_summary(task))
            if len(tasks) == input.page_size:
                return GenTaskPage(tasks=tasks, next_cursor=_owned_cursor(row, filter_hash))
        before = (owned[-1].created_at, owned[-1].task_id)
        if len(owned) < OWNED_SLICE_SIZE:
            return GenTaskPage(tasks=tasks, next_cursor=None)
    return GenTaskPage(tasks=tasks, next_cursor=_owned_cursor(owned[-1], filter_hash))


async def _fetch_slice(
    service: GenerationTaskService, input: GenTaskListInput, task_ids: list[str]
) -> dict[str, GenerationTaskResponse]:
    if not task_ids:
        return {}
    found: dict[str, GenerationTaskResponse] = {}
    page_num = 1
    while True:
        response, _ = await call_with_retry(
            lambda page_num=page_num: service.list_tasks(  # type: ignore[misc]
                page=page_num,
                page_size=_PROVIDER_PAGE_SIZE,
                status=input.status,
                task_ids=task_ids,
                model=input.model,
            )
        )
        found.update({task.id: task for task in response.data if task.id in task_ids})
        if len(response.data) < _PROVIDER_PAGE_SIZE or len(found) >= len(task_ids):
            return found
        page_num += 1


def _summary(task: GenerationTaskResponse) -> GenTaskSummary:
    _family, kind = classify_task(task.model)
    error = None
    if task.error and (task.error.code or task.error.message):
        error = TaskError(code=task.error.code or None, message=task.error.message)
    return GenTaskSummary(
        task_id=task.id,
        model=task.model,
        kind=kind,
        status=GenTaskStatus(task.status),
        created_at=task_timestamp(task.created_at),
        error=error,
    )


def _filter_hash(input: GenTaskListInput) -> str:
    filters = {
        "status": input.status,
        "model": input.model,
        "task_ids": sorted(input.task_ids) if input.task_ids is not None else None,
        "page_size": input.page_size,
    }
    return hashlib.sha256(json.dumps(filters, sort_keys=True).encode()).hexdigest()[:16]


def _owned_cursor(row: OwnedTask, filter_hash: str) -> str:
    return _encode_cursor(
        {"created_at": row.created_at.isoformat(), "task_id": row.task_id}, filter_hash
    )


def _encode_cursor(position: dict[str, object], filter_hash: str) -> str:
    payload = {"v": _CURSOR_VERSION, "filters": filter_hash, **position}
    return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode()


def _owned_position(cursor: dict[str, object] | None) -> tuple[datetime, str] | None:
    if cursor is None:
        return None
    created_at, task_id = cursor.get("created_at"), cursor.get("task_id")
    if not isinstance(created_at, str) or not isinstance(task_id, str):
        raise ValueError("cursor is not a valid gen_task_list cursor.")
    try:
        return datetime.fromisoformat(created_at), task_id
    except ValueError:
        raise ValueError("cursor is not a valid gen_task_list cursor.") from None


def _decode_cursor(cursor: str | None, filter_hash: str) -> dict[str, object] | None:
    if cursor is None:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        raise ValueError("cursor is not a valid gen_task_list cursor.") from None
    if not isinstance(payload, dict) or payload.get("v") != _CURSOR_VERSION:
        raise ValueError("cursor is not a valid gen_task_list cursor.")
    if payload.get("filters") != filter_hash:
        raise ValueError(
            "cursor was issued for different filters; repeat the original status, model, "
            "task_ids and page_size, or start again without a cursor."
        )
    return payload
