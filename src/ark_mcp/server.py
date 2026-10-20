"""FastMCP server factory and module-level deployment entrypoint.

Tools are registered from the static catalog in ``tools/_catalog.py``; the
background-job contract is ``specs/SPEC_BACKGROUND_JOBS.md``. Runtime resources
are created once by the FastMCP lifespan; importing this module only assembles
the declarative server surface.
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

    from fastmcp.server.auth import AuthProvider
    from starlette.requests import Request

from ark_mcp.background_jobs import (  # noqa: E402
    background_tool_spec,
    required_background_tool_names,
)
from ark_mcp.config.env import (  # noqa: E402
    AuthMode,
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
from ark_mcp.tools._catalog import (  # noqa: E402
    CATALOG,
    CatalogEntry,
    ToolDefinition,
    resolve_tool_definition,
)

SERVER_INSTRUCTIONS = (
    "BytePlus multimodal generation server: Seedance video, Seedream images, Seed Audio, "
    "Seed 3D (Hyper3D, Hitem3d), OmniHuman avatar video, Seed multimodal and audio "
    "understanding, Speech-to-Text, and (when configured) VOD AI MediaKit processing and a "
    "private asset library. Call ark_list_models first: `model` accepts only the exact "
    "configured IDs it returns, and it lists per-model limits and which optional services "
    "are enabled. Run long-running tools through ark_job_submit and ark_job_get; this is the "
    "default path because most clients do not support MCP tasks, and native task "
    "augmentation also works. Poll Seedance and 3D tasks with gen_task_get, VOD tasks with "
    "vod_get_task (pass the operation the create tool returned), and OmniHuman tasks with "
    "omnihuman_get_task; never resubmit a create tool to poll. "
    "Generated media is persisted as seed-media:// resources; reference private assets as "
    "asset://<asset_id> in Seedance."
)
_REQUIRED_BACKGROUND_TASK_NAMES = required_background_tool_names()
_BACKGROUND_TASK_POLL_INTERVAL = timedelta(seconds=2)


def _task_config(tool_name: str) -> TaskConfig | None:
    spec = background_tool_spec(tool_name)
    if spec is None:
        return None
    return TaskConfig(mode=spec.mode, poll_interval=_BACKGROUND_TASK_POLL_INTERVAL)


def _task_handler[**P, R](
    name: str, handler: Callable[P, Awaitable[R]]
) -> Callable[P, Awaitable[R]]:
    if name in _REQUIRED_BACKGROUND_TASK_NAMES:
        handler = guard_task_execution(handler)
    if _task_config(name) is not None:
        handler = instrument_tool_execution(handler, tool_name=name)
    return handler


def register_tools(server: FastMCP, settings: Settings) -> None:
    """Register every catalog tool whose settings gate passes."""
    skipped: list[str] = []
    for entry, definition in _gated_tool_definitions(settings, skipped):
        scopes = (entry.scope,) if entry.scope else ()
        server.tool(
            name=entry.name,
            annotations={**definition.annotations},
            output_schema=definition.output_model.model_json_schema(),
            task=_task_config(entry.name),
            auth=component_auth(settings, *scopes),
        )(_task_handler(entry.name, definition.handler))
    if skipped:
        log_info(
            "tools_skipped", tools=skipped, reason="provider not configured or feature disabled"
        )


def _gated_tool_definitions(
    settings: Settings, skipped: list[str]
) -> list[tuple[CatalogEntry, ToolDefinition]]:
    definitions: list[tuple[CatalogEntry, ToolDefinition]] = []
    for entry in CATALOG:
        if entry.gate(settings):
            definitions.append((entry, resolve_tool_definition(entry)))
        else:
            skipped.append(entry.name)
    return definitions


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


def _disabled_http_guardrails(settings: Settings) -> list[str]:
    if settings.mcp_transport != "http" or settings.mcp_auth_mode is not AuthMode.JWT:
        return []
    disabled = []
    if not settings.daily_budget_usd:
        disabled.append("DAILY_BUDGET_USD")
    if settings.rate_limit_rpm <= 0:
        disabled.append("RATE_LIMIT_RPM")
    return disabled


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
        instructions=SERVER_INSTRUCTIONS,
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
            f"Asset library configured: {resolved_settings.has_modelark_openapi}\n"
            f"Seed Audio configured: {resolved_settings.has_seed_audio}\n"
            f"VOD AI MediaKit configured: {resolved_settings.has_vod_mediakit}\n"
            f"OmniHuman configured: {resolved_settings.has_omnihuman}\n"
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

        if resolved_settings.has_omnihuman:
            from ark_mcp.providers.vision_cv.client import VisionCvGateway

            cv_gw = VisionCvGateway(
                api_key=resolved_settings.cv_api_key,
                base_url=resolved_settings.cv_base_url,
                timeout=resolved_settings.request_timeout_ms / 1000,
                connect_timeout=resolved_settings.connect_timeout_ms / 1000,
            )
            try:
                providers["vision_cv"] = (
                    "reachable"
                    if await cv_gw.health_check(timeout_seconds=timeout)
                    else "unreachable"
                )
            finally:
                await cv_gw.close()

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
            resolved_settings.has_omnihuman,
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
    disabled_guardrails = _disabled_http_guardrails(resolved_settings)
    if disabled_guardrails:
        log_warning(
            "http_guardrails_disabled",
            disabled=disabled_guardrails,
            detail="Set DAILY_BUDGET_USD and RATE_LIMIT_RPM for shared HTTP; see docs/self-hosting.md.",
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
        omnihuman_configured=resolved_settings.has_omnihuman,
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
