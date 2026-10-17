"""Static MCP tool catalog: the single inventory of registered tools.

Each entry names the tool, the module that implements it, its authorization
scope, its background-job mode, and the settings gate that decides whether it
is registered. Entries hold metadata only, so importing the catalog never
imports a tool module; ``load_tool_definitions`` imports the modules whose
gate passes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from pydantic import BaseModel

    from ark_mcp.config.env import Settings

BackgroundMode = Literal["required", "optional"]


def _always(_settings: Settings) -> bool:
    return True


def _modelark(settings: Settings) -> bool:
    return settings.has_modelark


def _seed3d(settings: Settings) -> bool:
    return settings.has_seed3d


def _seed_audio(settings: Settings) -> bool:
    return settings.has_seed_audio


def _stt(settings: Settings) -> bool:
    return settings.has_stt


def _object_storage(settings: Settings) -> bool:
    return settings.has_object_storage


def _vod(settings: Settings) -> bool:
    return settings.has_vod_mediakit


def _omnihuman(settings: Settings) -> bool:
    return settings.has_omnihuman


def _assets(settings: Settings) -> bool:
    return settings.has_modelark_openapi


def _asset_delete(settings: Settings) -> bool:
    return settings.has_modelark_openapi and settings.modelark_assets_allow_delete


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """Registration metadata for one MCP tool."""

    name: str
    module: str
    output: str
    scope: str | None
    background: BackgroundMode | None = None
    gate: Callable[[Settings], bool] = _always
    annotations: str = "TOOL_ANNOTATIONS"


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Runtime objects a catalog entry resolves to."""

    handler: Callable[..., Awaitable[Any]]
    output_model: type[BaseModel]
    annotations: Mapping[str, bool]


def _entry(
    name: str,
    output: str,
    scope: str | None,
    *,
    background: BackgroundMode | None = None,
    gate: Callable[[Settings], bool] = _always,
    module: str | None = None,
    annotations: str = "TOOL_ANNOTATIONS",
) -> CatalogEntry:
    return CatalogEntry(
        name=name,
        module=f"ark_mcp.tools.{module or name}",
        output=output,
        scope=scope,
        background=background,
        gate=gate,
        annotations=annotations,
    )


CATALOG: tuple[CatalogEntry, ...] = (
    _entry(
        "ark_job_capabilities",
        "BackgroundJobCapabilities",
        None,
        module="background_jobs",
        annotations="CAPABILITIES_TOOL_ANNOTATIONS",
    ),
    _entry(
        "ark_job_submit",
        "BackgroundJobAccepted",
        None,
        module="background_jobs",
        annotations="SUBMIT_TOOL_ANNOTATIONS",
    ),
    _entry(
        "ark_job_get",
        "BackgroundJobSnapshot",
        None,
        module="background_jobs",
        annotations="GET_TOOL_ANNOTATIONS",
    ),
    _entry(
        "ark_job_cancel",
        "BackgroundJobCancelled",
        None,
        module="background_jobs",
        annotations="CANCEL_TOOL_ANNOTATIONS",
    ),
    _entry("ark_list_models", "ArkListModelsOutput", None),
    _entry("seed_media_get_artifact", "SeedMediaGetArtifactOutput", "artifacts:read"),
    _entry("seed_media_export_artifact", "SeedMediaExportArtifactOutput", "artifacts:read"),
    _entry(
        "seed_media_persist_url",
        "SeedMediaPersistUrlOutput",
        "media:upload",
        background="optional",
    ),
    _entry(
        "seed_audio_generate",
        "SeedAudioGenerateOutput",
        "seed:audio:generate",
        background="required",
        gate=_seed_audio,
    ),
    _entry(
        "seed_audio_generate_variations",
        "SeedAudioVariationsOutput",
        "seed:audio:generate",
        background="required",
        gate=_seed_audio,
    ),
    _entry(
        "media_upload",
        "MediaUploadOutput",
        "media:upload",
        background="required",
        gate=_object_storage,
    ),
    _entry(
        "media_upload_batch",
        "MediaUploadBatchOutput",
        "media:upload",
        background="required",
        gate=_object_storage,
    ),
    _entry("media_presign", "MediaPresignOutput", "media:presign", gate=_object_storage),
    _entry(
        "media_presign_batch",
        "MediaPresignBatchOutput",
        "media:presign",
        gate=_object_storage,
    ),
    _entry(
        "speech_to_text",
        "SpeechToTextOutput",
        "seed:asr:transcribe",
        background="required",
        gate=_stt,
    ),
    _entry(
        "vod_enhance_video",
        "VodEnhanceVideoOutput",
        "vod:enhance",
        background="required",
        gate=_vod,
    ),
    _entry(
        "vod_transcode_video",
        "VodTranscodeVideoOutput",
        "vod:transcode",
        background="required",
        gate=_vod,
    ),
    _entry(
        "vod_separate_audio",
        "VodSeparateAudioOutput",
        "vod:extract",
        background="required",
        gate=_vod,
    ),
    _entry(
        "vod_add_subtitles",
        "VodAddSubtitlesOutput",
        "vod:subtitle:add",
        background="required",
        gate=_vod,
    ),
    _entry(
        "vod_remove_subtitles",
        "VodRemoveSubtitlesOutput",
        "vod:subtitle:remove",
        background="required",
        gate=_vod,
    ),
    _entry(
        "vod_get_task",
        "VodTaskOutput",
        "vod:read",
        background="optional",
        gate=_vod,
    ),
    _entry("ark_asset_group_ensure", "AssetGroupResult", "assets:write", gate=_assets),
    _entry("ark_asset_group_create", "AssetGroupResult", "assets:write", gate=_assets),
    _entry("ark_asset_group_get", "AssetGroupResult", "assets:read", gate=_assets),
    _entry("ark_asset_group_list", "AssetGroupPage", "assets:read", gate=_assets),
    _entry("ark_asset_group_update", "AssetGroupResult", "assets:write", gate=_assets),
    _entry(
        "ark_asset_create",
        "AssetCreateOutput",
        "assets:write",
        background="optional",
        gate=_assets,
    ),
    _entry("ark_asset_get", "AssetResult", "assets:read", gate=_assets),
    _entry("ark_asset_list", "AssetPage", "assets:read", gate=_assets),
    _entry("ark_asset_update", "AssetResult", "assets:write", gate=_assets),
    _entry(
        "ark_asset_verification_start",
        "VerificationStartOutput",
        "assets:verify",
        gate=_assets,
    ),
    _entry(
        "ark_asset_verification_result",
        "VerificationResultOutput",
        "assets:verify",
        background="optional",
        gate=_assets,
    ),
    _entry("ark_asset_delete", "DeleteOutput", "assets:delete", gate=_asset_delete),
    _entry("ark_asset_group_delete", "DeleteOutput", "assets:delete", gate=_asset_delete),
    _entry(
        "omnihuman_create_task",
        "OmniHumanCreateTaskOutput",
        "omnihuman:create",
        background="required",
        gate=_omnihuman,
    ),
    _entry(
        "omnihuman_get_task",
        "OmniHumanTaskOutput",
        "omnihuman:read",
        background="optional",
        gate=_omnihuman,
    ),
    _entry(
        "omnihuman_cancel_task",
        "OmniHumanCancelTaskOutput",
        "omnihuman:cancel",
        gate=_omnihuman,
    ),
    _entry(
        "seedream_generate_image",
        "SeedreamGenerateOutput",
        "seedream:generate",
        background="required",
        gate=_modelark,
    ),
    _entry(
        "seedream_edit_image",
        "SeedreamEditOutput",
        "seedream:generate",
        background="required",
        gate=_modelark,
    ),
    _entry(
        "seedream_generate_image_variations",
        "SeedreamVariationsOutput",
        "seedream:generate",
        background="required",
        gate=_modelark,
    ),
    _entry(
        "seedance_create_task",
        "SeedanceCreateTaskOutput",
        "seedance:create",
        background="required",
        gate=_modelark,
    ),
    _entry(
        "gen_task_get",
        "GenTaskGetOutput",
        "gen:read",
        background="optional",
        gate=_modelark,
    ),
    _entry("gen_task_list", "GenTaskPage", "gen:read", gate=_modelark),
    _entry(
        "gen_task_cancel_or_delete",
        "GenTaskCancelOrDeleteOutput",
        "gen:delete",
        gate=_modelark,
    ),
    _entry(
        "seed_understand",
        "SeedUnderstandOutput",
        "understanding:read",
        background="required",
        gate=_modelark,
    ),
    _entry(
        "seed_audio_understand",
        "SeedAudioUnderstandOutput",
        "understanding:read",
        background="required",
        gate=_modelark,
    ),
    _entry(
        "hyper3d_create_task",
        "Seed3DCreateTaskOutput",
        "hyper3d:create",
        background="required",
        gate=_seed3d,
    ),
    _entry(
        "hitem3d_create_task",
        "Seed3DCreateTaskOutput",
        "hitem3d:create",
        background="required",
        gate=_seed3d,
    ),
)


def catalog_entry(name: str) -> CatalogEntry | None:
    """Return the catalog entry for a tool name, if the tool exists."""
    return _BY_NAME.get(name)


def resolve_tool_definition(entry: CatalogEntry) -> ToolDefinition:
    """Import the entry's module and return its handler, output model and annotations."""
    module = import_module(entry.module)
    return ToolDefinition(
        handler=getattr(module, entry.name),
        output_model=getattr(module, entry.output),
        annotations=getattr(module, entry.annotations),
    )


def load_tool_definitions(settings: Settings) -> list[tuple[CatalogEntry, ToolDefinition]]:
    """Resolve every entry whose gate passes, in catalog order."""
    return [(entry, resolve_tool_definition(entry)) for entry in CATALOG if entry.gate(settings)]


_BY_NAME: Mapping[str, CatalogEntry] = {entry.name: entry for entry in CATALOG}
