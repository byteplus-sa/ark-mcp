"""FastMCP server factory and module-level deployment entrypoint.

Implements ``plans/PLAN_CODEBASE_GAP_REMEDIATION.md`` and
``plans/PLAN_MCP_TASK_COMPATIBILITY.md``. Runtime resources are created once by
the FastMCP lifespan; importing this module only assembles the declarative
server surface.
"""

from __future__ import annotations

import contextlib
import os
import subprocess  # nosec B404
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

import truststore

truststore.inject_into_ssl()

from fastmcp import Context, FastMCP  # noqa: E402
from fastmcp.resources import ResourceContent, ResourceResult  # noqa: E402
from fastmcp.utilities.tasks import TaskConfig  # noqa: E402
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest  # noqa: E402
from starlette.middleware import Middleware  # noqa: E402
from starlette.responses import JSONResponse, Response  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastmcp.server.auth import AuthCheck, AuthProvider
    from starlette.requests import Request

from ark_mcp.background_jobs import (  # noqa: E402
    background_tool_spec,
    required_background_tool_names,
)
from ark_mcp.config.env import (  # noqa: E402
    Settings,
    get_settings,
    resolve_env_file,
)
from ark_mcp.observability.logger import info as log_info  # noqa: E402
from ark_mcp.observability.logger import set_level  # noqa: E402
from ark_mcp.observability.logger import warning as log_warning  # noqa: E402
from ark_mcp.observability.metrics import (  # noqa: E402
    MetricsMiddleware,
    instrument_tool_execution,
)
from ark_mcp.runtime import (  # noqa: E402
    RuntimeFactory,
    RuntimeState,
    build_lifespan,
    create_runtime_services,
    get_principal,
    get_runtime,
)
from ark_mcp.security.http_auth import (  # noqa: E402
    build_auth_provider,
    component_auth,
)
from ark_mcp.security.http_middleware import (  # noqa: E402
    RateLimitMiddleware,
    RequestBodyLimitMiddleware,
)
from ark_mcp.security.tasks import TenantTasksExtension, guard_task_execution  # noqa: E402

_REQUIRED_BACKGROUND_TASK_NAMES = required_background_tool_names()
_BACKGROUND_TASK_POLL_INTERVAL = timedelta(seconds=2)


def _task_config(tool_name: str) -> TaskConfig | None:
    spec = background_tool_spec(tool_name)
    if spec is None:
        return None
    return TaskConfig(mode=spec.mode, poll_interval=_BACKGROUND_TASK_POLL_INTERVAL)


def _tool_auth(
    settings: Settings,
    tool_name: str,
    fallback_scope: str,
) -> AuthCheck | None:
    spec = background_tool_spec(tool_name)
    return component_auth(
        settings,
        spec.required_scope if spec is not None else fallback_scope,
    )


def _task_handler[**P, R](
    name: str, handler: Callable[P, Awaitable[R]]
) -> Callable[P, Awaitable[R]]:
    if name in _REQUIRED_BACKGROUND_TASK_NAMES:
        handler = guard_task_execution(handler)
    if _task_config(name) is not None:
        handler = instrument_tool_execution(handler, tool_name=name)
    return handler


def register_tools(server: FastMCP, settings: Settings) -> None:
    """Register configured tools on one server instance."""

    from ark_mcp.domain.background_jobs import (
        BackgroundJobAccepted,
        BackgroundJobCancelled,
        BackgroundJobCapabilities,
        BackgroundJobSnapshot,
    )
    from ark_mcp.tools.background_jobs import (
        CANCEL_TOOL_ANNOTATIONS,
        CAPABILITIES_TOOL_ANNOTATIONS,
        GET_TOOL_ANNOTATIONS,
        SUBMIT_TOOL_ANNOTATIONS,
        ark_job_cancel,
        ark_job_capabilities,
        ark_job_get,
        ark_job_submit,
    )
    from ark_mcp.tools.seed_media_export_artifact import (
        TOOL_ANNOTATIONS as export_artifact_annotations,
    )
    from ark_mcp.tools.seed_media_export_artifact import (
        SeedMediaExportArtifactOutput,
        seed_media_export_artifact,
    )
    from ark_mcp.tools.seed_media_get_artifact import (
        TOOL_ANNOTATIONS as get_artifact_annotations,
    )
    from ark_mcp.tools.seed_media_get_artifact import (
        SeedMediaGetArtifactOutput,
        seed_media_get_artifact,
    )
    from ark_mcp.tools.seed_media_persist_url import (
        TOOL_ANNOTATIONS as persist_url_annotations,
    )
    from ark_mcp.tools.seed_media_persist_url import (
        SeedMediaPersistUrlOutput,
        seed_media_persist_url,
    )

    server.tool(
        name="ark_job_capabilities",
        annotations={**CAPABILITIES_TOOL_ANNOTATIONS},
        output_schema=BackgroundJobCapabilities.model_json_schema(),
        auth=component_auth(settings),
    )(ark_job_capabilities)
    server.tool(
        name="ark_job_submit",
        annotations={**SUBMIT_TOOL_ANNOTATIONS},
        output_schema=BackgroundJobAccepted.model_json_schema(),
        auth=component_auth(settings),
    )(ark_job_submit)
    server.tool(
        name="ark_job_get",
        annotations={**GET_TOOL_ANNOTATIONS},
        output_schema=BackgroundJobSnapshot.model_json_schema(),
        auth=component_auth(settings),
    )(ark_job_get)
    server.tool(
        name="ark_job_cancel",
        annotations={**CANCEL_TOOL_ANNOTATIONS},
        output_schema=BackgroundJobCancelled.model_json_schema(),
        auth=component_auth(settings),
    )(ark_job_cancel)
    server.tool(
        name="seed_media_get_artifact",
        annotations={**get_artifact_annotations},
        output_schema=SeedMediaGetArtifactOutput.model_json_schema(),
        auth=component_auth(settings, "artifacts:read"),
    )(seed_media_get_artifact)
    server.tool(
        name="seed_media_export_artifact",
        annotations={**export_artifact_annotations},
        output_schema=SeedMediaExportArtifactOutput.model_json_schema(),
        auth=component_auth(settings, "artifacts:read"),
    )(seed_media_export_artifact)
    server.tool(
        name="seed_media_persist_url",
        annotations={**persist_url_annotations},
        output_schema=SeedMediaPersistUrlOutput.model_json_schema(),
        task=_task_config("seed_media_persist_url"),
        auth=_tool_auth(settings, "seed_media_persist_url", "media:upload"),
    )(_task_handler("seed_media_persist_url", seed_media_persist_url))

    if settings.has_seed_audio:
        from ark_mcp.tools.seed_audio_generate import (
            TOOL_ANNOTATIONS as audio_annotations,
        )
        from ark_mcp.tools.seed_audio_generate import (
            SeedAudioGenerateOutput,
            seed_audio_generate,
        )
        from ark_mcp.tools.seed_audio_generate_variations import (
            TOOL_ANNOTATIONS as audio_var_annotations,
        )
        from ark_mcp.tools.seed_audio_generate_variations import (
            SeedAudioVariationsOutput,
            seed_audio_generate_variations,
        )

        server.tool(
            name="seed_audio_generate",
            annotations={**audio_annotations},
            output_schema=SeedAudioGenerateOutput.model_json_schema(),
            task=_task_config("seed_audio_generate"),
            auth=_tool_auth(settings, "seed_audio_generate", "seed:audio:generate"),
        )(_task_handler("seed_audio_generate", seed_audio_generate))
        server.tool(
            name="seed_audio_generate_variations",
            annotations={**audio_var_annotations},
            output_schema=SeedAudioVariationsOutput.model_json_schema(),
            task=_task_config("seed_audio_generate_variations"),
            auth=_tool_auth(
                settings,
                "seed_audio_generate_variations",
                "seed:audio:generate",
            ),
        )(_task_handler("seed_audio_generate_variations", seed_audio_generate_variations))

    if settings.has_object_storage:
        from ark_mcp.tools.media_presign import (
            TOOL_ANNOTATIONS as presign_annotations,
        )
        from ark_mcp.tools.media_presign import (
            MediaPresignOutput,
            media_presign,
        )
        from ark_mcp.tools.media_presign_batch import (
            TOOL_ANNOTATIONS as presign_batch_annotations,
        )
        from ark_mcp.tools.media_presign_batch import (
            MediaPresignBatchOutput,
            media_presign_batch,
        )
        from ark_mcp.tools.media_upload import (
            TOOL_ANNOTATIONS as upload_annotations,
        )
        from ark_mcp.tools.media_upload import (
            MediaUploadOutput,
            media_upload,
        )

        server.tool(
            name="media_upload",
            annotations={**upload_annotations},
            output_schema=MediaUploadOutput.model_json_schema(),
            task=_task_config("media_upload"),
            auth=_tool_auth(settings, "media_upload", "media:upload"),
        )(_task_handler("media_upload", media_upload))
        server.tool(
            name="media_presign",
            annotations={**presign_annotations},
            output_schema=MediaPresignOutput.model_json_schema(),
            auth=component_auth(settings, "media:presign"),
        )(media_presign)
        server.tool(
            name="media_presign_batch",
            annotations={**presign_batch_annotations},
            output_schema=MediaPresignBatchOutput.model_json_schema(),
            auth=component_auth(settings, "media:presign"),
        )(media_presign_batch)

    if settings.has_stt:
        from ark_mcp.tools.speech_to_text import (
            TOOL_ANNOTATIONS as stt_annotations,
        )
        from ark_mcp.tools.speech_to_text import (
            SpeechToTextOutput,
            speech_to_text,
        )

        server.tool(
            name="speech_to_text",
            annotations={**stt_annotations},
            output_schema=SpeechToTextOutput.model_json_schema(),
            task=_task_config("speech_to_text"),
            auth=_tool_auth(settings, "speech_to_text", "seed:asr:transcribe"),
        )(_task_handler("speech_to_text", speech_to_text))

    if settings.has_vod_mediakit:
        from ark_mcp.tools.vod_add_subtitles import (
            TOOL_ANNOTATIONS as vod_add_subtitles_annotations,
        )
        from ark_mcp.tools.vod_add_subtitles import (
            VodAddSubtitlesOutput,
            vod_add_subtitles,
        )
        from ark_mcp.tools.vod_enhance_video import (
            TOOL_ANNOTATIONS as vod_enhance_annotations,
        )
        from ark_mcp.tools.vod_enhance_video import (
            VodEnhanceVideoOutput,
            vod_enhance_video,
        )
        from ark_mcp.tools.vod_get_audio_separation import (
            TOOL_ANNOTATIONS as vod_get_audio_separation_annotations,
        )
        from ark_mcp.tools.vod_get_audio_separation import (
            VodAudioSeparationTaskOutput,
            vod_get_audio_separation,
        )
        from ark_mcp.tools.vod_get_enhancement_task import (
            TOOL_ANNOTATIONS as vod_get_enhancement_annotations,
        )
        from ark_mcp.tools.vod_get_enhancement_task import (
            VodEnhancementTaskOutput,
            vod_get_enhancement_task,
        )
        from ark_mcp.tools.vod_get_subtitle_addition_task import (
            TOOL_ANNOTATIONS as vod_get_subtitle_addition_annotations,
        )
        from ark_mcp.tools.vod_get_subtitle_addition_task import (
            VodSubtitleAdditionTaskOutput,
            vod_get_subtitle_addition_task,
        )
        from ark_mcp.tools.vod_get_subtitle_removal_task import (
            TOOL_ANNOTATIONS as vod_get_subtitle_removal_annotations,
        )
        from ark_mcp.tools.vod_get_subtitle_removal_task import (
            VodSubtitleRemovalTaskOutput,
            vod_get_subtitle_removal_task,
        )
        from ark_mcp.tools.vod_get_transcode_task import (
            TOOL_ANNOTATIONS as vod_get_transcode_annotations,
        )
        from ark_mcp.tools.vod_get_transcode_task import (
            VodTranscodeTaskOutput,
            vod_get_transcode_task,
        )
        from ark_mcp.tools.vod_remove_subtitles import (
            TOOL_ANNOTATIONS as vod_remove_subtitles_annotations,
        )
        from ark_mcp.tools.vod_remove_subtitles import (
            VodRemoveSubtitlesOutput,
            vod_remove_subtitles,
        )
        from ark_mcp.tools.vod_separate_audio import (
            TOOL_ANNOTATIONS as vod_separate_audio_annotations,
        )
        from ark_mcp.tools.vod_separate_audio import (
            VodSeparateAudioOutput,
            vod_separate_audio,
        )
        from ark_mcp.tools.vod_transcode_video import (
            TOOL_ANNOTATIONS as vod_transcode_annotations,
        )
        from ark_mcp.tools.vod_transcode_video import (
            VodTranscodeVideoOutput,
            vod_transcode_video,
        )

        server.tool(
            name="vod_enhance_video",
            annotations={**vod_enhance_annotations},
            output_schema=VodEnhanceVideoOutput.model_json_schema(),
            task=_task_config("vod_enhance_video"),
            auth=_tool_auth(settings, "vod_enhance_video", "vod:enhance"),
        )(_task_handler("vod_enhance_video", vod_enhance_video))
        server.tool(
            name="vod_get_enhancement_task",
            annotations={**vod_get_enhancement_annotations},
            output_schema=VodEnhancementTaskOutput.model_json_schema(),
            task=_task_config("vod_get_enhancement_task"),
            auth=_tool_auth(settings, "vod_get_enhancement_task", "vod:read"),
        )(_task_handler("vod_get_enhancement_task", vod_get_enhancement_task))
        server.tool(
            name="vod_transcode_video",
            annotations={**vod_transcode_annotations},
            output_schema=VodTranscodeVideoOutput.model_json_schema(),
            task=_task_config("vod_transcode_video"),
            auth=_tool_auth(settings, "vod_transcode_video", "vod:transcode"),
        )(_task_handler("vod_transcode_video", vod_transcode_video))
        server.tool(
            name="vod_get_transcode_task",
            annotations={**vod_get_transcode_annotations},
            output_schema=VodTranscodeTaskOutput.model_json_schema(),
            task=_task_config("vod_get_transcode_task"),
            auth=_tool_auth(settings, "vod_get_transcode_task", "vod:read"),
        )(_task_handler("vod_get_transcode_task", vod_get_transcode_task))
        server.tool(
            name="vod_separate_audio",
            annotations={**vod_separate_audio_annotations},
            output_schema=VodSeparateAudioOutput.model_json_schema(),
            task=_task_config("vod_separate_audio"),
            auth=_tool_auth(settings, "vod_separate_audio", "vod:extract"),
        )(_task_handler("vod_separate_audio", vod_separate_audio))
        server.tool(
            name="vod_get_audio_separation",
            annotations={**vod_get_audio_separation_annotations},
            output_schema=VodAudioSeparationTaskOutput.model_json_schema(),
            task=_task_config("vod_get_audio_separation"),
            auth=_tool_auth(settings, "vod_get_audio_separation", "vod:read"),
        )(_task_handler("vod_get_audio_separation", vod_get_audio_separation))
        server.tool(
            name="vod_add_subtitles",
            annotations={**vod_add_subtitles_annotations},
            output_schema=VodAddSubtitlesOutput.model_json_schema(),
            task=_task_config("vod_add_subtitles"),
            auth=_tool_auth(settings, "vod_add_subtitles", "vod:subtitle:add"),
        )(_task_handler("vod_add_subtitles", vod_add_subtitles))
        server.tool(
            name="vod_get_subtitle_addition_task",
            annotations={**vod_get_subtitle_addition_annotations},
            output_schema=VodSubtitleAdditionTaskOutput.model_json_schema(),
            task=_task_config("vod_get_subtitle_addition_task"),
            auth=_tool_auth(settings, "vod_get_subtitle_addition_task", "vod:read"),
        )(_task_handler("vod_get_subtitle_addition_task", vod_get_subtitle_addition_task))
        server.tool(
            name="vod_remove_subtitles",
            annotations={**vod_remove_subtitles_annotations},
            output_schema=VodRemoveSubtitlesOutput.model_json_schema(),
            task=_task_config("vod_remove_subtitles"),
            auth=_tool_auth(settings, "vod_remove_subtitles", "vod:subtitle:remove"),
        )(_task_handler("vod_remove_subtitles", vod_remove_subtitles))
        server.tool(
            name="vod_get_subtitle_removal_task",
            annotations={**vod_get_subtitle_removal_annotations},
            output_schema=VodSubtitleRemovalTaskOutput.model_json_schema(),
            task=_task_config("vod_get_subtitle_removal_task"),
            auth=_tool_auth(settings, "vod_get_subtitle_removal_task", "vod:read"),
        )(_task_handler("vod_get_subtitle_removal_task", vod_get_subtitle_removal_task))

    if not settings.has_modelark:
        log_info("tools_skipped", reason="BYTEPLUS_MODELARK_API_KEY not configured")
        return

    from ark_mcp.tools.seed_understand import (
        TOOL_ANNOTATIONS as understand_annotations,
    )
    from ark_mcp.tools.seed_understand import (
        SeedUnderstandOutput,
        seed_understand,
    )
    from ark_mcp.tools.seedance_2_5_create_task import (
        TOOL_ANNOTATIONS as create_2_5_annotations,
    )
    from ark_mcp.tools.seedance_2_5_create_task import (
        Seedance25CreateTaskOutput,
        seedance_2_5_create_task,
    )
    from ark_mcp.tools.seedance_2_5_create_task_variations import (
        TOOL_ANNOTATIONS as seedance_2_5_var_annotations,
    )
    from ark_mcp.tools.seedance_2_5_create_task_variations import (
        Seedance25VariationsOutput,
        seedance_2_5_create_task_variations,
    )
    from ark_mcp.tools.seedance_cancel_or_delete_task import (
        TOOL_ANNOTATIONS as cancel_annotations,
    )
    from ark_mcp.tools.seedance_cancel_or_delete_task import (
        SeedanceCancelOrDeleteOutput,
        seedance_cancel_or_delete_task,
    )
    from ark_mcp.tools.seedance_create_task import (
        TOOL_ANNOTATIONS as create_annotations,
    )
    from ark_mcp.tools.seedance_create_task import (
        SeedanceCreateTaskOutput,
        seedance_create_task,
    )
    from ark_mcp.tools.seedance_create_task_variations import (
        TOOL_ANNOTATIONS as seedance_var_annotations,
    )
    from ark_mcp.tools.seedance_create_task_variations import (
        SeedanceVariationsOutput,
        seedance_create_task_variations,
    )
    from ark_mcp.tools.seedance_get_task import TOOL_ANNOTATIONS as get_annotations
    from ark_mcp.tools.seedance_get_task import SeedanceTaskOutput, seedance_get_task
    from ark_mcp.tools.seedance_list_tasks import TOOL_ANNOTATIONS as list_annotations
    from ark_mcp.tools.seedance_list_tasks import SeedanceTaskPage, seedance_list_tasks
    from ark_mcp.tools.seedream_edit_image import (
        TOOL_ANNOTATIONS as seedream_edit_annotations,
    )
    from ark_mcp.tools.seedream_edit_image import (
        SeedreamEditOutput,
        seedream_edit_image,
    )
    from ark_mcp.tools.seedream_generate_image import (
        TOOL_ANNOTATIONS as seedream_annotations,
    )
    from ark_mcp.tools.seedream_generate_image import (
        SeedreamGenerateOutput,
        seedream_generate_image,
    )
    from ark_mcp.tools.seedream_generate_image_variations import (
        TOOL_ANNOTATIONS as seedream_var_annotations,
    )
    from ark_mcp.tools.seedream_generate_image_variations import (
        SeedreamVariationsOutput,
        seedream_generate_image_variations,
    )

    registrations = (
        (
            "seedream_generate_image",
            seedream_annotations,
            SeedreamGenerateOutput,
            "seedream:generate",
            seedream_generate_image,
        ),
        (
            "seedream_edit_image",
            seedream_edit_annotations,
            SeedreamEditOutput,
            "seedream:generate",
            seedream_edit_image,
        ),
        (
            "seedream_generate_image_variations",
            seedream_var_annotations,
            SeedreamVariationsOutput,
            "seedream:generate",
            seedream_generate_image_variations,
        ),
        (
            "seedance_create_task",
            create_annotations,
            SeedanceCreateTaskOutput,
            "seedance:create",
            seedance_create_task,
        ),
        (
            "seedance_create_task_variations",
            seedance_var_annotations,
            SeedanceVariationsOutput,
            "seedance:create",
            seedance_create_task_variations,
        ),
        (
            "seedance_2_5_create_task",
            create_2_5_annotations,
            Seedance25CreateTaskOutput,
            "seedance:create",
            seedance_2_5_create_task,
        ),
        (
            "seedance_2_5_create_task_variations",
            seedance_2_5_var_annotations,
            Seedance25VariationsOutput,
            "seedance:create",
            seedance_2_5_create_task_variations,
        ),
        (
            "seedance_get_task",
            get_annotations,
            SeedanceTaskOutput,
            "seedance:read",
            seedance_get_task,
        ),
        (
            "seedance_list_tasks",
            list_annotations,
            SeedanceTaskPage,
            "seedance:read",
            seedance_list_tasks,
        ),
        (
            "seedance_cancel_or_delete_task",
            cancel_annotations,
            SeedanceCancelOrDeleteOutput,
            "seedance:delete",
            seedance_cancel_or_delete_task,
        ),
        (
            "seed_understand",
            understand_annotations,
            SeedUnderstandOutput,
            "understanding:read",
            seed_understand,
        ),
    )
    for name, tool_annotations, output_model, scope, handler in registrations:
        server.tool(
            name=name,
            annotations={**tool_annotations},
            output_schema=output_model.model_json_schema(),
            task=_task_config(name),
            auth=_tool_auth(settings, name, scope),
        )(_task_handler(name, handler))

    if settings.has_seed3d:
        from ark_mcp.tools._seed3d_shared import (
            Seed3DCancelOrDeleteOutput,
            Seed3DCreateTaskOutput,
            Seed3DTaskOutput,
            Seed3DTaskPage,
        )
        from ark_mcp.tools.hitem3d_cancel_or_delete_task import (
            TOOL_ANNOTATIONS as hitem3d_cancel_annotations,
        )
        from ark_mcp.tools.hitem3d_cancel_or_delete_task import (
            hitem3d_cancel_or_delete_task,
        )
        from ark_mcp.tools.hitem3d_create_task import (
            TOOL_ANNOTATIONS as hitem3d_create_annotations,
        )
        from ark_mcp.tools.hitem3d_create_task import hitem3d_create_task
        from ark_mcp.tools.hitem3d_get_task import (
            TOOL_ANNOTATIONS as hitem3d_get_annotations,
        )
        from ark_mcp.tools.hitem3d_get_task import hitem3d_get_task
        from ark_mcp.tools.hitem3d_list_tasks import (
            TOOL_ANNOTATIONS as hitem3d_list_annotations,
        )
        from ark_mcp.tools.hitem3d_list_tasks import hitem3d_list_tasks
        from ark_mcp.tools.hyper3d_cancel_or_delete_task import (
            TOOL_ANNOTATIONS as hyper3d_cancel_annotations,
        )
        from ark_mcp.tools.hyper3d_cancel_or_delete_task import (
            hyper3d_cancel_or_delete_task,
        )
        from ark_mcp.tools.hyper3d_create_task import (
            TOOL_ANNOTATIONS as hyper3d_create_annotations,
        )
        from ark_mcp.tools.hyper3d_create_task import hyper3d_create_task
        from ark_mcp.tools.hyper3d_get_task import (
            TOOL_ANNOTATIONS as hyper3d_get_annotations,
        )
        from ark_mcp.tools.hyper3d_get_task import hyper3d_get_task
        from ark_mcp.tools.hyper3d_list_tasks import (
            TOOL_ANNOTATIONS as hyper3d_list_annotations,
        )
        from ark_mcp.tools.hyper3d_list_tasks import hyper3d_list_tasks

        seed3d_registrations = (
            (
                "hyper3d_create_task",
                hyper3d_create_annotations,
                Seed3DCreateTaskOutput,
                "hyper3d:create",
                hyper3d_create_task,
            ),
            (
                "hyper3d_get_task",
                hyper3d_get_annotations,
                Seed3DTaskOutput,
                "hyper3d:read",
                hyper3d_get_task,
            ),
            (
                "hyper3d_list_tasks",
                hyper3d_list_annotations,
                Seed3DTaskPage,
                "hyper3d:read",
                hyper3d_list_tasks,
            ),
            (
                "hyper3d_cancel_or_delete_task",
                hyper3d_cancel_annotations,
                Seed3DCancelOrDeleteOutput,
                "hyper3d:delete",
                hyper3d_cancel_or_delete_task,
            ),
            (
                "hitem3d_create_task",
                hitem3d_create_annotations,
                Seed3DCreateTaskOutput,
                "hitem3d:create",
                hitem3d_create_task,
            ),
            (
                "hitem3d_get_task",
                hitem3d_get_annotations,
                Seed3DTaskOutput,
                "hitem3d:read",
                hitem3d_get_task,
            ),
            (
                "hitem3d_list_tasks",
                hitem3d_list_annotations,
                Seed3DTaskPage,
                "hitem3d:read",
                hitem3d_list_tasks,
            ),
            (
                "hitem3d_cancel_or_delete_task",
                hitem3d_cancel_annotations,
                Seed3DCancelOrDeleteOutput,
                "hitem3d:delete",
                hitem3d_cancel_or_delete_task,
            ),
        )
        for (
            seed3d_name,
            seed3d_annotations,
            seed3d_output_model,
            seed3d_scope,
            seed3d_handler,
        ) in seed3d_registrations:
            server.tool(
                name=seed3d_name,
                annotations={**seed3d_annotations},
                output_schema=seed3d_output_model.model_json_schema(),
                task=_task_config(seed3d_name),
                auth=_tool_auth(settings, seed3d_name, seed3d_scope),
            )(_task_handler(seed3d_name, seed3d_handler))


class HardenedFastMCP(FastMCP):
    """FastMCP subclass that applies ASGI body and rate-limit middleware for HTTP."""

    def __init__(
        self,
        *args: Any,
        app_settings: Settings | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._app_settings = app_settings

    def http_app(self, *args: Any, **kwargs: Any) -> Any:
        if self._app_settings is not None and self._app_settings.mcp_transport == "http":
            asgi_middleware = [
                Middleware(
                    RequestBodyLimitMiddleware,
                    max_bytes=self._app_settings.mcp_http_max_body_bytes,
                ),
            ]
            if self._app_settings.rate_limit_rpm > 0:
                asgi_middleware.append(
                    Middleware(
                        RateLimitMiddleware,
                        rpm=self._app_settings.rate_limit_rpm,
                        burst=(
                            self._app_settings.rate_limit_burst or self._app_settings.rate_limit_rpm
                        ),
                        trust_proxy_headers=self._app_settings.rate_limit_trust_proxy_headers,
                    )
                )
            asgi_middleware.extend(kwargs.get("middleware") or [])
            kwargs["middleware"] = asgi_middleware
        return super().http_app(*args, **kwargs)


def create_server(
    settings: Settings | None = None,
    *,
    runtime_factory: RuntimeFactory = create_runtime_services,
    auth_provider: AuthProvider | None = None,
) -> FastMCP:
    """Build an isolated server with a once-per-server runtime lifespan."""
    resolved_settings = settings or get_settings()
    set_level(resolved_settings.log_level)
    runtime_state = RuntimeState()
    server: FastMCP = HardenedFastMCP(
        "Ark Seed Multimodal",
        instructions=(
            "BytePlus multimodal generation server. Provides Seed Audio, Seedream, "
            "Seedance, Seed 2.1 multimodal understanding, and Speech-to-Text tools. "
            "BytePlus VOD AI MediaKit enhancement, video transcoding, subtitle burn-in, "
            "subtitle or text removal, and voice and background audio separation are "
            "available when the MediaKit API key is "
            "configured. Generated media is persisted as durable MCP resources. Run "
            "long-running tools through ark_job_capabilities, ark_job_submit, ark_job_get, "
            "and ark_job_cancel; these ordinary tools are the default path because most "
            "clients do not yet support MCP tasks. A client that does negotiate the "
            "io.modelcontextprotocol/tasks extension may instead call those tools with task "
            "augmentation and poll tasks/get."
        ),
        auth=auth_provider or build_auth_provider(resolved_settings),
        lifespan=build_lifespan(resolved_settings, runtime_factory, runtime_state),
        middleware=[MetricsMiddleware()],
        app_settings=resolved_settings,
    )
    server.add_extension(TenantTasksExtension(resolved_settings))

    @server.resource(
        "seed-media://artifacts/{artifact_id}",
        auth=component_auth(resolved_settings, "artifacts:read"),
    )
    async def get_artifact(artifact_id: str, ctx: Context) -> ResourceResult:
        """Return persisted media after canonical ID and ownership checks."""
        try:
            parsed_artifact_id = UUID(artifact_id, version=4)
        except (ValueError, AttributeError) as exc:
            raise ValueError(f"Invalid artifact ID format: '{artifact_id}'") from exc
        if str(parsed_artifact_id) != artifact_id:
            raise ValueError(f"Invalid artifact ID format: '{artifact_id}'")

        runtime = get_runtime(ctx)
        artifact = await runtime.artifact_store.get(
            artifact_id,
            auth=get_principal(ctx),
        )
        log_info(
            "artifact_served",
            artifact_id=artifact_id,
            media_type=artifact.media_type,
            mime_type=artifact.mime_type,
            bytes=len(artifact.data),
        )
        return ResourceResult(
            contents=[ResourceContent(content=artifact.data, mime_type=artifact.mime_type)],
            meta={"artifact_id": artifact_id, "media_type": artifact.media_type},
        )

    @server.resource("seed-health://status")
    async def health_status() -> str:
        """Return a credential-free MCP health summary."""
        stamp = _build_stamp()
        return (
            "Ark Seed MCP Server\n"
            f"Build: {stamp}\n"
            "Status: healthy\n"
            f"ModelArk configured: {resolved_settings.has_modelark}\n"
            f"Seed 3D configured: {resolved_settings.has_seed3d}\n"
            f"Seed Audio configured: {resolved_settings.has_seed_audio}\n"
            f"VOD AI MediaKit configured: {resolved_settings.has_vod_mediakit}\n"
            f"TOS configured: {resolved_settings.has_tos}\n"
            f"S3 configured: {resolved_settings.has_s3}\n"
            f"Object storage backend: {resolved_settings.object_storage_backend}\n"
            f"STT configured: {resolved_settings.has_stt}\n"
            f"Artifact backend: {resolved_settings.artifact_backend}\n"
            f"Transport: {resolved_settings.mcp_transport}\n"
        )

    @server.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        return JSONResponse({"status": "healthy"})

    @server.custom_route("/ready", methods=["GET"])
    async def ready(_request: Request) -> JSONResponse:
        runtime = runtime_state.runtime
        if runtime is None:
            return JSONResponse({"status": "not_ready"}, status_code=503)
        try:
            await runtime.ownership_store.ping()
            artifact_root = Path(runtime.settings.artifact_dir).expanduser().resolve()
            if not artifact_root.is_dir() or not os.access(artifact_root, os.W_OK):
                raise RuntimeError("Artifact storage is not writable.")
        except Exception:
            return JSONResponse({"status": "not_ready"}, status_code=503)

        if not resolved_settings.readiness_check_providers:
            return JSONResponse({"status": "ready"})

        providers: dict[str, str] = {}
        timeout = resolved_settings.readiness_provider_timeout_seconds

        if resolved_settings.has_modelark:
            from ark_mcp.providers.modelark.client import ModelArkGateway

            modelark_gw = ModelArkGateway(
                api_key=resolved_settings.modelark_api_key,
                base_url=resolved_settings.modelark_base_url,
                timeout=resolved_settings.request_timeout_ms / 1000,
                connect_timeout=resolved_settings.connect_timeout_ms / 1000,
            )
            try:
                providers["modelark"] = (
                    "reachable"
                    if await modelark_gw.health_check(timeout_seconds=timeout)
                    else "unreachable"
                )
            finally:
                await modelark_gw.close()

        if resolved_settings.has_seed_audio:
            from ark_mcp.providers.seed_speech.client import SeedSpeechGateway

            audio_gw = SeedSpeechGateway(
                api_key=resolved_settings.seed_speech_api_key,
                base_url=resolved_settings.seed_audio_base_url,
                timeout=resolved_settings.request_timeout_ms / 1000,
                connect_timeout=resolved_settings.connect_timeout_ms / 1000,
            )
            try:
                providers["seed_audio"] = (
                    "reachable"
                    if await audio_gw.health_check(timeout_seconds=timeout)
                    else "unreachable"
                )
            finally:
                await audio_gw.close()

        if resolved_settings.has_stt:
            from ark_mcp.providers.seed_speech.asr_http import SeedSpeechAsrHttpGateway

            stt_gw = SeedSpeechAsrHttpGateway(
                api_key=resolved_settings.seed_speech_api_key,
                base_url=resolved_settings.seed_speech_asr_base_url,
                timeout=resolved_settings.request_timeout_ms / 1000,
                connect_timeout=resolved_settings.connect_timeout_ms / 1000,
            )
            try:
                providers["stt"] = (
                    "reachable"
                    if await stt_gw.health_check(timeout_seconds=timeout)
                    else "unreachable"
                )
            finally:
                await stt_gw.close()

        if resolved_settings.has_vod_mediakit:
            from ark_mcp.providers.vod_mediakit.client import VodMediaKitGateway

            vod_gw = VodMediaKitGateway(
                api_key=resolved_settings.vod_mediakit_api_key,
                base_url=resolved_settings.vod_mediakit_base_url,
                timeout=resolved_settings.request_timeout_ms / 1000,
                connect_timeout=resolved_settings.connect_timeout_ms / 1000,
            )
            try:
                providers["vod_mediakit"] = (
                    "reachable"
                    if await vod_gw.health_check(timeout_seconds=timeout)
                    else "unreachable"
                )
            finally:
                await vod_gw.close()

        all_reachable = all(v == "reachable" for v in providers.values())
        if all_reachable:
            return JSONResponse({"status": "ready", "providers": providers})
        return JSONResponse(
            {"status": "degraded", "providers": providers},
            status_code=503,
        )

    @server.custom_route("/metrics", methods=["GET"])
    async def metrics(_request: Request) -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    register_tools(server, resolved_settings)
    if not any(
        (
            resolved_settings.has_modelark,
            resolved_settings.has_seed_audio,
            resolved_settings.has_seed3d,
            resolved_settings.has_stt,
            resolved_settings.has_vod_mediakit,
        )
    ):
        log_warning(
            "no_provider_credentials_configured",
            detail=(
                "No provider credentials resolved, so only the always-on job and "
                "artifact tools are registered and ark_job_capabilities returns no "
                "targets. The env file is read relative to the server's working "
                "directory; set ARK_MCP_ENV_FILE to an absolute path, or launch the "
                "server with its working directory at the repository root."
            ),
            env_file=resolve_env_file(),
            cwd=str(Path.cwd()),
        )
    log_info(
        "server_ready",
        transport=resolved_settings.mcp_transport,
        auth_mode=resolved_settings.mcp_auth_mode.value,
        modelark_configured=resolved_settings.has_modelark,
        seed_audio_configured=resolved_settings.has_seed_audio,
        seed3d_configured=resolved_settings.has_seed3d,
        stt_configured=resolved_settings.has_stt,
        vod_mediakit_configured=resolved_settings.has_vod_mediakit,
        object_storage_backend=resolved_settings.object_storage_backend,
        artifact_backend=resolved_settings.artifact_backend,
        log_level=resolved_settings.log_level,
    )
    return server


mcp: FastMCP = create_server()


def _build_stamp() -> str:
    """Return a short build-identity stamp (git SHA + import time)."""
    sha = _git_sha()
    installed = datetime.now(UTC).isoformat()
    if sha:
        return f"git:{sha} installed:{installed}"
    return f"installed:{installed}"


def _git_sha() -> str | None:
    """Return the short (7-char) git SHA of HEAD, or None on failure."""
    with contextlib.suppress(Exception):
        result = subprocess.run(  # nosec B603, B607
            ["git", "rev-parse", "--short=7", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            cwd=Path(__file__).resolve().parent.parent,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    return None
