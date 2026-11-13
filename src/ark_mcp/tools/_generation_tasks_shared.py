"""Shared logic for the ``gen_task_*`` tools (Seedance, Hyper3D, Hitem3d tasks).

Tasks are classified by the registry binding for the model that ran them.
Outputs are persisted once per task under a per-task single-flight lock, so
overlapping polls never download the same file twice.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from ark_mcp.config.model_capabilities import VideoCapabilities, get_capability_registry
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

TaskKind = Literal["video", "3d", "unknown"]

TERMINAL_STATUSES = frozenset({"succeeded", "failed", "expired", "cancelled"})
RECOMMENDED_POLL_AFTER_MS = 5000
SOURCE_URL_LIFETIME = timedelta(hours=24)


@dataclass(frozen=True, slots=True)
class OutputSpec:
    role: TaskOutputRole
    cache_key: str
    media_type: MediaType


_VIDEO_OUTPUTS = (
    OutputSpec("video", "video", MediaType.VIDEO),
    OutputSpec("last_frame", "last_frame", MediaType.IMAGE),
)
_3D_OUTPUTS = (OutputSpec("model_file", "file", MediaType.THREE_D),)
_OUTPUTS_BY_KIND: dict[TaskKind, tuple[OutputSpec, ...]] = {
    "video": _VIDEO_OUTPUTS,
    "3d": _3D_OUTPUTS,
    "unknown": _VIDEO_OUTPUTS + _3D_OUTPUTS,
}

_3D_MIME_BY_EXT: dict[str, str] = {
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".obj": "text/plain",
    ".stl": "application/octet-stream",
    ".fbx": "application/octet-stream",
    ".usdz": "model/vnd.usdz+zip",
    ".usd": "model/vnd.usdz+zip",
    ".zip": "application/zip",
}


class GenTask(BaseModel):
    """Status and outputs of one ModelArk generation task."""

    task_id: str = Field(..., description="Provider task ID.")
    model: str = Field(..., description="Model ID that ran the task.")
    family: str | None = Field(
        None,
        description="Registry family of the model; null when the model is not configured on this server.",
    )
    kind: TaskKind = Field(
        ...,
        description="Output kind from the registry binding for model: video, 3d, or unknown.",
    )
    status: GenTaskStatus = Field(
        ...,
        description="Task status: queued, running, succeeded, failed, cancelled, or expired.",
    )
    created_at: str = Field(..., description="ISO-8601 task creation time.")
    updated_at: str = Field(..., description="ISO-8601 time of the last status change.")
    outputs: list[TaskOutputFile] = Field(
        ...,
        description=(
            "Finished outputs: video and last_frame for video tasks, model_file for 3D tasks. "
            "Empty until the task succeeds."
        ),
    )
    error: TaskError | None = Field(None, description="Provider failure details when failed.")
    usage: TaskUsage | None = Field(None, description="Token usage reported by the provider.")
    draft: bool | None = Field(
        None,
        description=(
            "True for a Seedance Draft task (480p preview). Once it succeeds, pass task_id as "
            "draft_task_id to the Seedance create tool within 7 days of created_at."
        ),
    )
    settings: SeedanceTaskSettings | None = Field(
        None,
        description="Video generation settings echoed by the provider; null for 3D tasks.",
    )
    queue: SeedanceQueueInfo | None = Field(
        None,
        description=(
            "Server-derived queue timing for a queued or running video task. ModelArk "
            "publishes no queue position or ETA. Null once finished and for 3D tasks."
        ),
    )


def classify_task(model: str) -> tuple[str | None, TaskKind]:
    """Return ``(family, kind)`` for the model that ran a task."""
    caps = get_capability_registry().resolve_task_model(model)
    if caps is None:
        return None, "unknown"
    kind: TaskKind = "video" if isinstance(caps, VideoCapabilities) else "3d"
    return caps.family.value, kind


def output_urls(task: GenerationTaskResponse, kind: TaskKind) -> dict[str, str]:
    """Return the provider URL for each output role the task produced."""
    urls = {
        "video": task.video_url,
        "last_frame": task.last_frame_url,
        "model_file": task.file_url,
    }
    return {
        spec.role: url for spec in _OUTPUTS_BY_KIND[kind] if (url := urls[spec.role]) is not None
    }


def needs_full_fetch(task: GenerationTaskResponse, kind: TaskKind) -> bool:
    """Whether a list entry lacks outputs that a succeeded task should carry."""
    if task.status != "succeeded":
        return False
    if kind == "video":
        return task.video_url is None
    if kind == "3d":
        return task.file_url is None
    return task.video_url is None and task.file_url is None


def infer_3d_mime(file_url: str) -> str:
    """Infer a MIME type for a generated 3D file from its URL extension."""
    lowered = file_url.lower().split("?")[0]
    for extension, mime in _3D_MIME_BY_EXT.items():
        if lowered.endswith(extension):
            return mime
    return "application/zip"


def _mime_type(spec: OutputSpec, url: str) -> str:
    if spec.media_type is MediaType.THREE_D:
        return infer_3d_mime(url)
    if spec.media_type is MediaType.VIDEO:
        return "video/quicktime" if urlsplit(url).path.lower().endswith(".mov") else "video/mp4"
    return "image/jpeg"


def _is_durable(ref: ArtifactRef | None) -> bool:
    return ref is not None and ref.persistence_error is None


async def persist_task_outputs(
    runtime: RuntimeServices,
    owner: PrincipalContext,
    task: GenerationTaskResponse,
    kind: TaskKind,
) -> dict[str, ArtifactRef | None]:
    """Persist a succeeded task's outputs exactly once, keyed by output role.

    Durable results are cached individually. Missing outputs are added on
    later polls; failed outputs retain their temporary provider URL and
    ``persistence_error`` and are retried without copying durable siblings.
    """
    specs = _OUTPUTS_BY_KIND[kind]
    async with runtime.task_artifact_locks.acquire("modelark", task.id) as singleflight:
        cached = singleflight.artifacts
        if cached is None:
            stored = await runtime.task_artifact_cache.get("modelark", task.id)
            cached = dict(stored) if stored else {}
        artifacts = {key: ref for key, ref in cached.items() if _is_durable(ref)}
        singleflight.artifacts = dict(artifacts)

        urls = output_urls(task, kind)
        source_expiry = (datetime.now(UTC) + SOURCE_URL_LIFETIME).isoformat()
        refs: dict[str, ArtifactRef | None] = {}
        for spec in specs:
            ref = artifacts.get(spec.cache_key)
            if _is_durable(ref):
                refs[spec.role] = ref
                continue
            url = urls.get(spec.role)
            refs[spec.role] = (
                await persist_from_url(
                    runtime.artifact_store,
                    url=url,
                    media_type=spec.media_type,
                    mime_type=_mime_type(spec, url),
                    source_expires_at=source_expiry,
                    auth=owner,
                )
                if url
                else None
            )
            if _is_durable(refs[spec.role]):
                artifacts[spec.cache_key] = refs[spec.role]
                singleflight.artifacts = dict(artifacts)
                await runtime.task_artifact_cache.set("modelark", task.id, dict(artifacts))

        if any(not _is_durable(refs[spec.role]) for spec in specs if urls.get(spec.role)):
            log_warning("artifact_persist_failed", task_id=task.id, provider="modelark")
        return refs


def build_outputs(
    task: GenerationTaskResponse,
    kind: TaskKind,
    persisted: dict[str, ArtifactRef | None] | None,
) -> list[TaskOutputFile]:
    """Describe each produced output, with its persistence outcome."""
    if task.status != "succeeded":
        return []
    source_expiry = (datetime.now(UTC) + SOURCE_URL_LIFETIME).isoformat()
    outputs: list[TaskOutputFile] = []
    for role, url in output_urls(task, kind).items():
        ref = persisted.get(role) if persisted is not None else None
        if persisted is None:
            outputs.append(
                TaskOutputFile(
                    role=role,  # type: ignore[arg-type]
                    source_url=url,  # type: ignore[arg-type]
                    source_expires_at=source_expiry,
                    persistence="not_requested",
                )
            )
        elif _is_durable(ref):
            outputs.append(
                TaskOutputFile(
                    role=role,  # type: ignore[arg-type]
                    artifact=ref,
                    source_url=url,  # type: ignore[arg-type]
                    source_expires_at=ref.source_expires_at if ref else source_expiry,
                    persistence="persisted",
                )
            )
        else:
            outputs.append(
                TaskOutputFile(
                    role=role,  # type: ignore[arg-type]
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
    family: str | None,
    kind: TaskKind,
    outputs: list[TaskOutputFile],
) -> GenTask:
    """Normalize a provider task into the ``GenTask`` output shape."""
    error = None
    if task.error and (task.error.code or task.error.message):
        error = TaskError(code=task.error.code or None, message=task.error.message)
    is_video = kind != "3d"
    return GenTask(
        task_id=task.id,
        model=task.model,
        family=family,
        kind=kind,
        status=GenTaskStatus(task.status),
        created_at=task_timestamp(task.created_at),
        updated_at=task_timestamp(task.updated_at),
        outputs=outputs,
        error=error,
        usage=task_usage(task),
        draft=task.draft if is_video else None,
        settings=SeedanceTaskSettings.model_validate(task.content or {}) if is_video else None,
        queue=queue_info(task) if is_video else None,
    )
