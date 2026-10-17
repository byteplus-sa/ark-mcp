"""Per-operation VOD AI MediaKit task fetchers and shared output persistence.

Each fetcher polls one MediaKit task through the matching provider service
and maps it to a ``VodTaskSnapshot``. ``persist_vod_outputs`` copies the
expiring output URLs of a succeeded snapshot into the artifact store once per
task, shared by every operation.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, Protocol
from urllib.parse import urlsplit

from pydantic import AnyUrl

from ark_mcp.artifacts.store import ArtifactPersistenceError
from ark_mcp.domain.artifacts import ArtifactPersistenceIssue, ArtifactRef, MediaType
from ark_mcp.domain.errors import ProviderError
from ark_mcp.domain.models import TaskError, TaskOutputFile, TaskOutputRole, VodTaskStatus
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.providers.vod_mediakit.enhancement import VodMediaKitEnhancementService
from ark_mcp.providers.vod_mediakit.separate_voice import VodMediaKitSeparateVoiceService
from ark_mcp.providers.vod_mediakit.subtitles import (
    VodMediaKitSubtitleBurnInService,
    VodMediaKitSubtitleRemovalService,
)
from ark_mcp.providers.vod_mediakit.transcode import VodMediaKitTranscodeService
from ark_mcp.runtime import get_runtime
from ark_mcp.security.media_policy import get_media_limits
from ark_mcp.tools._task_execution import context_log
from ark_mcp.tools._vod_shared import VodOperation

if TYPE_CHECKING:
    from fastmcp import Context

    from ark_mcp.runtime import RuntimeServices
    from ark_mcp.security.auth_context import PrincipalContext

_PROVIDER_KEY = "vod-mediakit"

_AUDIO_MIME_BY_SUFFIX = {
    ".aac": "audio/aac",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
}

_AUDIO_TRACKS: tuple[tuple[TaskOutputRole, str], ...] = (
    ("voice", "voice_url"),
    ("background", "background_url"),
    ("music", "music_url"),
    ("sfx", "sfx_url"),
)


@dataclass(frozen=True, slots=True)
class VodOutputSource:
    """One expiring provider output URL of a succeeded task."""

    role: TaskOutputRole
    url: AnyUrl
    media_type: MediaType
    mime_type: str


@dataclass(frozen=True, slots=True)
class VodTaskSnapshot:
    """Operation-independent view of one polled MediaKit task."""

    task_id: str
    status: VodTaskStatus
    provider_status: str | None = None
    request_id: str | None = None
    created_at: str | None = None
    finished_at: str | None = None
    duration_seconds: float | None = None
    resolution: str | None = None
    fps: float | None = None
    video_codec: str | None = None
    tool_version: str | None = None
    source_expires_at: str | None = None
    sources: tuple[VodOutputSource, ...] = ()
    error: TaskError | None = None


@dataclass(frozen=True, slots=True)
class VodFetcher:
    """Poll adapter for one VOD operation."""

    label: str
    fetch: Callable[[str], Awaitable[VodTaskSnapshot]]


class _TaskService[TaskT](Protocol):
    async def get(self, task_id: str) -> TaskT: ...

    async def close(self) -> None: ...


class _VideoTask(Protocol):
    task_id: str
    status: Literal["processing", "succeeded", "failed"]
    provider_status: str | None
    request_id: str | None
    output_url: AnyUrl | None
    duration_seconds: float | None
    resolution: str | None
    created_at: str | None
    finished_at: str | None
    source_expires_at: str | None
    failure_code: str | None
    failure_message: str | None


def operation_mismatch_error(exc: ProviderError, operation: VodOperation) -> ProviderError:
    """Add an operation hint to a not-found or unreadable task response."""
    code = (exc.code or "").replace("_", "").replace("-", "").lower()
    if exc.http_status != 404 and "notfound" not in code and code != "invalidresponse":
        return exc
    hint = (
        f" Check that operation ('{operation}') matches the operation returned by the vod_* "
        "create tool that created this task."
    )
    return ProviderError(exc.error.model_copy(update={"message": exc.message + hint}))


async def _get_task[TaskT](
    service: _TaskService[TaskT], task_id: str, operation: VodOperation
) -> TaskT:
    try:
        return await call_with_retry(lambda: service.get(task_id))
    except ProviderError as exc:
        raise operation_mismatch_error(exc, operation) from exc
    finally:
        await service.close()


def _failure(code: str | None, message: str | None, label: str) -> TaskError:
    return TaskError(code=code, message=message or f"MediaKit reported the {label} task failed.")


def _video_snapshot(
    task: _VideoTask,
    label: str,
    *,
    fps: float | None = None,
    video_codec: str | None = None,
    tool_version: str | None = None,
) -> VodTaskSnapshot:
    common = VodTaskSnapshot(
        task_id=task.task_id,
        status=task.status,
        provider_status=task.provider_status,
        request_id=task.request_id,
        created_at=task.created_at,
        finished_at=task.finished_at,
    )
    if task.status == "failed":
        return replace(common, error=_failure(task.failure_code, task.failure_message, label))
    if task.status != "succeeded" or task.output_url is None:
        return common
    return replace(
        common,
        duration_seconds=task.duration_seconds,
        resolution=task.resolution,
        fps=fps,
        video_codec=video_codec,
        tool_version=tool_version,
        source_expires_at=task.source_expires_at,
        sources=(VodOutputSource("video", task.output_url, MediaType.VIDEO, "video/mp4"),),
    )


async def _fetch_enhance(task_id: str) -> VodTaskSnapshot:
    task = await _get_task(VodMediaKitEnhancementService(), task_id, "enhance")
    return _video_snapshot(task, "enhancement", fps=task.fps, tool_version=task.tool_version)


async def _fetch_transcode(task_id: str) -> VodTaskSnapshot:
    task = await _get_task(VodMediaKitTranscodeService(), task_id, "transcode")
    return _video_snapshot(task, "transcode", video_codec=task.video_codec)


async def _fetch_add_subtitles(task_id: str) -> VodTaskSnapshot:
    task = await _get_task(VodMediaKitSubtitleBurnInService(), task_id, "add_subtitles")
    return _video_snapshot(task, "subtitle burn-in")


async def _fetch_remove_subtitles(task_id: str) -> VodTaskSnapshot:
    task = await _get_task(VodMediaKitSubtitleRemovalService(), task_id, "remove_subtitles")
    return _video_snapshot(task, "subtitle removal")


def _track_mime_type(url: AnyUrl) -> str:
    path = urlsplit(str(url)).path
    suffix = path[path.rfind(".") :].lower() if "." in path else ""
    return _AUDIO_MIME_BY_SUFFIX.get(suffix, "audio/aac")


async def _fetch_separate_audio(task_id: str) -> VodTaskSnapshot:
    task = await _get_task(VodMediaKitSeparateVoiceService(), task_id, "separate_audio")
    common = VodTaskSnapshot(
        task_id=task.task_id,
        status=task.status,
        provider_status=task.provider_status,
        request_id=task.request_id,
        created_at=task.created_at,
        finished_at=task.finished_at,
    )
    if task.status == "failed":
        return replace(
            common, error=_failure(task.failure_code, task.failure_message, "separation")
        )
    if task.status != "succeeded":
        return common
    sources = tuple(
        VodOutputSource(role, url, MediaType.AUDIO, _track_mime_type(url))
        for role, attribute in _AUDIO_TRACKS
        if (url := getattr(task, attribute)) is not None
    )
    return replace(
        common,
        duration_seconds=task.duration_seconds,
        source_expires_at=task.source_expires_at,
        sources=sources,
    )


_VOD_FETCHERS: dict[VodOperation, VodFetcher] = {
    "enhance": VodFetcher("enhancement", _fetch_enhance),
    "transcode": VodFetcher("transcode", _fetch_transcode),
    "separate_audio": VodFetcher("audio separation", _fetch_separate_audio),
    "add_subtitles": VodFetcher("subtitle burn-in", _fetch_add_subtitles),
    "remove_subtitles": VodFetcher("subtitle removal", _fetch_remove_subtitles),
}


def _limit_bytes(media_type: MediaType) -> int:
    limits = get_media_limits()
    return limits.audio_max_bytes if media_type == MediaType.AUDIO else limits.video_max_bytes


async def _copy_source(
    ctx: Context,
    runtime: RuntimeServices,
    owner: PrincipalContext,
    source: VodOutputSource,
    source_expires_at: str | None,
    label: str,
) -> ArtifactRef | ArtifactPersistenceIssue:
    try:
        return await runtime.artifact_store.copy_from_trusted_url(
            url=str(source.url),
            media_type=source.media_type,
            mime_type=source.mime_type,
            source_expires_at=source_expires_at,
            auth=owner,
        )
    except ArtifactPersistenceError as exc:
        await context_log(
            ctx, "warning", f"VOD {label} output persistence failed: {exc.safe_message}"
        )
        return ArtifactPersistenceIssue(
            code=exc.code,
            message=exc.safe_message,
            retryable=exc.retryable,
            artifact_limit_bytes=_limit_bytes(source.media_type),
        )
    except Exception:
        await context_log(
            ctx,
            "warning",
            f"VOD {label} output persistence failed due to an internal storage error.",
        )
        return ArtifactPersistenceIssue(
            code="storage_failed",
            message="Provider output could not be written to artifact storage.",
            retryable=True,
            artifact_limit_bytes=_limit_bytes(source.media_type),
        )


async def _cached_artifacts(
    ctx: Context, runtime: RuntimeServices, task_id: str, label: str
) -> dict[str, ArtifactRef]:
    try:
        cached = await runtime.task_artifact_cache.get(_PROVIDER_KEY, task_id)
    except Exception:
        await context_log(ctx, "warning", f"VOD {label} artifact cache lookup failed.")
        return {}
    return {role: ref for role, ref in (cached or {}).items() if ref is not None}


def _output_file(
    source: VodOutputSource,
    source_expires_at: str | None,
    outcome: ArtifactRef | ArtifactPersistenceIssue,
) -> TaskOutputFile:
    if isinstance(outcome, ArtifactRef):
        return TaskOutputFile(
            role=source.role,
            artifact=outcome,
            source_url=source.url,
            source_expires_at=source_expires_at,
            persistence="persisted",
        )
    return TaskOutputFile(
        role=source.role,
        source_url=source.url,
        source_expires_at=source_expires_at,
        persistence="failed",
        persistence_issue=outcome,
    )


async def persist_vod_outputs(
    ctx: Context,
    owner: PrincipalContext,
    snapshot: VodTaskSnapshot,
    *,
    persist_output: bool,
    label: str,
) -> list[TaskOutputFile]:
    """Describe each output of a succeeded task, persisting it once per task when asked."""
    if not persist_output:
        return [
            TaskOutputFile(
                role=source.role,
                source_url=source.url,
                source_expires_at=snapshot.source_expires_at,
                persistence="not_requested",
            )
            for source in snapshot.sources
        ]

    runtime = get_runtime(ctx)
    async with runtime.task_artifact_locks.acquire(_PROVIDER_KEY, snapshot.task_id) as singleflight:
        known = {role: ref for role, ref in (singleflight.artifacts or {}).items() if ref}
        if any(source.role not in known for source in snapshot.sources):
            known = {**await _cached_artifacts(ctx, runtime, snapshot.task_id, label), **known}

        outputs: list[TaskOutputFile] = []
        created = False
        for source in snapshot.sources:
            outcome: ArtifactRef | ArtifactPersistenceIssue | None = known.get(source.role)
            if outcome is None:
                outcome = await _copy_source(
                    ctx, runtime, owner, source, snapshot.source_expires_at, label
                )
                if isinstance(outcome, ArtifactRef):
                    known[source.role] = outcome
                    created = True
            outputs.append(_output_file(source, snapshot.source_expires_at, outcome))

        singleflight.artifacts = dict(known)
        if created:
            try:
                await runtime.task_artifact_cache.set(_PROVIDER_KEY, snapshot.task_id, dict(known))
            except Exception:
                await context_log(
                    ctx,
                    "warning",
                    f"VOD {label} artifact cache update failed; the artifact remains available.",
                )
    return outputs
