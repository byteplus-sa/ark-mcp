"""Shared logic for the Seedance task tools.

Outputs are persisted once per task under a per-task single-flight lock, so
overlapping polls never download the same file twice.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from ark_mcp.domain.artifacts import ArtifactRef, MediaType
from ark_mcp.domain.models import (
    GenTaskStatus,
    SeedanceQueueInfo,
    SeedanceTaskSettings,
    TaskError,
    TaskOutputFile,
    TaskOutputRole,
    TaskUsage,
)
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.providers.modelark.generation_tasks import task_timestamp, task_usage
from ark_mcp.providers.modelark.seedance_queue import queue_info
from ark_mcp.tools._persistence import persist_from_url

if TYPE_CHECKING:
    from ark_mcp.providers.modelark.schemas import GenerationTaskResponse
    from ark_mcp.runtime import RuntimeServices
    from ark_mcp.security.auth_context import PrincipalContext

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "expired", "cancelled"})
RECOMMENDED_POLL_AFTER_MS = 5000
SOURCE_URL_LIFETIME = timedelta(hours=24)


_OUTPUT_MEDIA_TYPES: dict[TaskOutputRole, MediaType] = {
    "video": MediaType.VIDEO,
    "last_frame": MediaType.IMAGE,
}


class GenTask(BaseModel):
    """Status and outputs of one ModelArk generation task."""

    task_id: str = Field(..., description="Provider task ID.")
    model: str = Field(..., description="Model ID that ran the task.")
    family: str = Field(..., description="Configured Seedance model family.")
    kind: Literal["video"] = Field("video", description="Output kind for Seedance tasks.")
    status: GenTaskStatus = Field(
        ...,
        description="Task status: queued, running, succeeded, failed, cancelled, or expired.",
    )
    created_at: str = Field(..., description="ISO-8601 task creation time.")
    updated_at: str = Field(..., description="ISO-8601 time of the last status change.")
    outputs: list[TaskOutputFile] = Field(
        ...,
        description=(
            "Finished outputs: video and an optional last_frame. Empty until the task succeeds."
        ),
    )
    error: TaskError | None = Field(None, description="Provider failure details when failed.")
    usage: TaskUsage | None = Field(None, description="Token usage reported by the provider.")
    draft: bool | None = Field(
        None,
        description=(
            "True for a Seedance Draft task (480p preview). Once it succeeds, pass task_id as "
            "draft_task_id to the Seedance create tool within 14 days of created_at "
            "(7 if created before 2026-10-28 10:00 UTC+8)."
        ),
    )
    settings: SeedanceTaskSettings | None = Field(
        None,
        description="Video generation settings echoed by the provider.",
    )
    queue: SeedanceQueueInfo | None = Field(
        None,
        description=(
            "Server-derived queue timing for a queued or running video task. ModelArk "
            "publishes no queue position or ETA. Null once finished."
        ),
    )


def output_urls(task: GenerationTaskResponse) -> dict[TaskOutputRole, str]:
    """Return the provider URL for each output role the task produced."""
    urls: dict[TaskOutputRole, str | None] = {
        "video": task.video_url,
        "last_frame": task.last_frame_url,
    }
    return {role: url for role, url in urls.items() if url is not None}


def _mime_type(media_type: MediaType, url: str) -> str:
    if media_type is MediaType.VIDEO:
        return "video/quicktime" if urlsplit(url).path.lower().endswith(".mov") else "video/mp4"
    return "image/jpeg"


def _is_durable(ref: ArtifactRef | None) -> bool:
    return ref is not None and ref.persistence_error is None


async def persist_task_outputs(
    runtime: RuntimeServices,
    owner: PrincipalContext,
    task: GenerationTaskResponse,
) -> dict[str, ArtifactRef | None]:
    """Persist a succeeded task's outputs exactly once, keyed by output role.

    Durable results are cached individually. Missing outputs are added on
    later polls; failed outputs retain their temporary provider URL and
    ``persistence_error`` and are retried without copying durable siblings.
    """
    async with runtime.task_artifact_locks.acquire("modelark", task.id) as singleflight:
        cached = singleflight.artifacts
        if cached is None:
            stored = await runtime.task_artifact_cache.get("modelark", task.id)
            cached = dict(stored) if stored else {}
        artifacts = {key: ref for key, ref in cached.items() if _is_durable(ref)}
        singleflight.artifacts = dict(artifacts)

        urls = output_urls(task)
        source_expiry = (datetime.now(UTC) + SOURCE_URL_LIFETIME).isoformat()
        refs: dict[str, ArtifactRef | None] = {}
        for role, media_type in _OUTPUT_MEDIA_TYPES.items():
            ref = artifacts.get(role)
            if _is_durable(ref):
                refs[role] = ref
                continue
            url = urls.get(role)
            refs[role] = (
                await persist_from_url(
                    runtime.artifact_store,
                    url=url,
                    media_type=media_type,
                    mime_type=_mime_type(media_type, url),
                    source_expires_at=source_expiry,
                    auth=owner,
                )
                if url
                else None
            )
            if _is_durable(refs[role]):
                artifacts[role] = refs[role]
                singleflight.artifacts = dict(artifacts)
                await runtime.task_artifact_cache.set("modelark", task.id, dict(artifacts))

        if any(not _is_durable(refs[role]) for role, url in urls.items() if url):
            log_warning("artifact_persist_failed", task_id=task.id, provider="modelark")
        return refs


def build_outputs(
    task: GenerationTaskResponse,
    persisted: dict[str, ArtifactRef | None] | None,
) -> list[TaskOutputFile]:
    """Describe each produced output, with its persistence outcome."""
    if task.status != "succeeded":
        return []
    source_expiry = (datetime.now(UTC) + SOURCE_URL_LIFETIME).isoformat()
    outputs: list[TaskOutputFile] = []
    for role, url in output_urls(task).items():
        ref = persisted.get(role) if persisted is not None else None
        if persisted is None:
            outputs.append(
                TaskOutputFile(
                    role=role,
                    source_url=url,  # type: ignore[arg-type]
                    source_expires_at=source_expiry,
                    persistence="not_requested",
                )
            )
        elif _is_durable(ref):
            outputs.append(
                TaskOutputFile(
                    role=role,
                    artifact=ref,
                    source_url=url,  # type: ignore[arg-type]
                    source_expires_at=ref.source_expires_at if ref else source_expiry,
                    persistence="persisted",
                )
            )
        else:
            outputs.append(
                TaskOutputFile(
                    role=role,
                    source_url=url,  # type: ignore[arg-type]
                    source_expires_at=source_expiry,
                    persistence="failed",
                    persistence_issue=ref.persistence_error if ref else None,
                )
            )
    return outputs


def build_gen_task(
    task: GenerationTaskResponse,
    *,
    family: str,
    outputs: list[TaskOutputFile],
) -> GenTask:
    """Normalize a provider task into the ``GenTask`` output shape."""
    error = None
    if task.error and (task.error.code or task.error.message):
        error = TaskError(code=task.error.code or None, message=task.error.message)
    return GenTask(
        task_id=task.id,
        model=task.model,
        family=family,
        status=GenTaskStatus(task.status),
        created_at=task_timestamp(task.created_at),
        updated_at=task_timestamp(task.updated_at),
        outputs=outputs,
        error=error,
        usage=task_usage(task),
        draft=task.draft,
        settings=SeedanceTaskSettings.model_validate(task.content or {}),
        queue=queue_info(task),
    )
