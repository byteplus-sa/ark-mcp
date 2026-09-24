"""``ark_asset_create`` tool — add one subject's files to an asset group.

Registers each source with ``CreateAsset`` (asynchronous, rate-limited per
``BYTEPLUS_MODELARK_ASSET_CREATE_QPM``) and optionally waits until every asset
is ``Active`` or ``Failed``. Per-source failures are reported inline.
"""

from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import ToolResult

from ark_mcp.domain.assets import asset_uri
from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.assets import AssetService, AssetTypeName
from ark_mcp.security.url_policy import UrlValidationError, validate_url
from ark_mcp.tools._asset_models import AssetCreateInput, AssetCreateItem, AssetCreateOutput
from ark_mcp.tools._asset_shared import (
    asset_call,
    infer_asset_type,
    presign_object_key,
    resolve_subject_group,
    wait_until_terminal,
)
from ark_mcp.tools._errors import provider_error_result
from ark_mcp.tools._task_execution import context_log

# CreateAsset downloads asynchronously and may queue; keep presigned sources
# valid well beyond the wait window.
_SOURCE_URL_TTL_SECONDS = 6 * 3600


async def ark_asset_create(input: AssetCreateInput, ctx: Context) -> AssetCreateOutput | ToolResult:
    """Add files of ONE subject to a private asset group and return asset:// references.

    Target either group_id (AIGC or a verified LivenessFace person) or subject
    (reuses/creates the AIGC group with that exact name). Each source is a
    public HTTPS url or a media_upload object_key. Real-person groups reject
    faces that do not match the verified person. With wait_until_active (default)
    the tool polls until each asset is Active or Failed. Use the returned
    asset_uri values ('asset://...') as Seedance image/video/audio references
    and refer to them in prompts as 'Image 1', 'Video 1', etc. Requires Dreamina
    Seedance Advanced Creation Rights and BytePlus AK/SK.
    """
    urls: list[str | None] = []
    types: list[AssetTypeName | None] = []
    for source in input.sources:
        asset_type = source.asset_type or infer_asset_type(
            url=source.url, object_key=source.object_key
        )
        types.append(asset_type)
        if source.url:
            try:
                validate_url(source.url)
            except UrlValidationError as exc:
                raise ValueError(UrlValidationError.safe_message) from exc
            urls.append(source.url)
        else:
            urls.append(None)

    for index, asset_type in enumerate(types):
        if asset_type is None:
            raise ValueError(
                f"sources[{index}]: cannot infer asset_type; set 'Image', 'Video', or 'Audio'."
            )

    for index, source in enumerate(input.sources):
        if source.object_key:
            urls[index] = await presign_object_key(ctx, source.object_key, _SOURCE_URL_TTL_SECONDS)

    async def _run(service: AssetService) -> AssetCreateOutput:
        group_created = False
        if input.subject:
            group, group_created = await resolve_subject_group(
                service,
                subject=input.subject,
                description=None,
                project_name=input.project_name,
                create_if_missing=True,
            )
            group_id = group.group_id
        else:
            group_id = input.group_id or ""

        items: list[AssetCreateItem] = []
        for index, source in enumerate(input.sources):
            try:
                asset_id = await service.create_asset(
                    group_id=group_id,
                    url=urls[index] or "",
                    asset_type=types[index],  # type: ignore[arg-type]
                    name=source.name,
                    skip_moderation=input.skip_moderation,
                    project_name=input.project_name,
                )
            except ProviderError as exc:
                items.append(
                    AssetCreateItem(
                        index=index,
                        error=f"{exc.error.code or 'error'}: {exc.error.message}",
                    )
                )
                continue
            items.append(
                AssetCreateItem(
                    index=index,
                    asset_id=asset_id,
                    asset_uri=asset_uri(asset_id),
                    status="Processing",
                )
            )
            log_info("asset_created", group_id=group_id, asset_id=asset_id)

        created_ids = [item.asset_id for item in items if item.asset_id]
        if input.wait_until_active and created_ids:
            await context_log(ctx, "info", f"Waiting for {len(created_ids)} asset(s) to process")
            latest = await wait_until_terminal(
                service,
                created_ids,
                project_name=input.project_name,
                timeout_seconds=input.wait_timeout_seconds,
            )
            for item in items:
                if item.asset_id and item.asset_id in latest:
                    item.status = latest[item.asset_id].status or item.status
                    if item.status == "Failed":
                        item.error = "Asset preprocessing or content review failed."

        return AssetCreateOutput(
            group_id=group_id,
            group_created=group_created,
            items=items,
            all_active=bool(items) and all(item.status == "Active" for item in items),
        )

    try:
        return await asset_call(ctx, _run)
    except ProviderError as exc:
        return provider_error_result(exc)


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
