"""``ark_asset_delete`` tool — irreversibly delete one asset or one asset group."""

from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import ToolResult

from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.assets import AssetService
from ark_mcp.tools._asset_models import AssetDeleteInput, AssetDeleteResult
from ark_mcp.tools._asset_shared import asset_call, retrying
from ark_mcp.tools._errors import provider_error_result


async def ark_asset_delete(input: AssetDeleteInput, ctx: Context) -> AssetDeleteResult | ToolResult:
    """Permanently delete one asset (asset_id) or one asset group (group_id). Irreversible.

    Requires confirm=true. A non-empty group is refused unless cascade=true, which also deletes
    its assets. Only registered when BYTEPLUS_MODELARK_ASSETS_ALLOW_DELETE=true.
    """
    asset_id, group_id = input.asset_id, input.group_id

    async def _delete(service: AssetService) -> AssetDeleteResult:
        if asset_id:
            await service.delete_asset(asset_id, input.project_name)
            log_info("asset_deleted", asset_id=asset_id)
            return AssetDeleteResult(id=asset_id, kind="asset", deleted=True)
        target = group_id or ""
        if not input.cascade:
            await _refuse_non_empty_group(service, target, input.project_name)
        await service.delete_group(target, input.project_name)
        log_info("asset_group_deleted", group_id=target, cascade=input.cascade)
        return AssetDeleteResult(id=target, kind="group", deleted=True)

    try:
        return await asset_call(ctx, _delete)
    except ProviderError as exc:
        return provider_error_result(exc)


async def _refuse_non_empty_group(
    service: AssetService, group_id: str, project_name: str | None
) -> None:
    group = await retrying(lambda: service.get_group(group_id, project_name))
    assets, _ = await retrying(
        lambda: service.list_assets(
            group_ids=[group_id],
            group_type=group.group_type or "AIGC",
            statuses=None,
            name=None,
            max_results=1,
            next_token=None,
            sort_order=None,
            project_name=project_name,
        )
    )
    if assets:
        raise ValueError(
            f"asset_group_not_empty: group {group_id} still holds assets, and deleting it would "
            "delete them too. Delete the assets first, or pass cascade=true."
        )


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
}
