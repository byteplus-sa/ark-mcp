"""``ark_asset_list`` tool — list assets with filters, or read them by ID."""

from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import ToolResult

from ark_mcp.domain.assets import Asset
from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.modelark.assets import AssetService
from ark_mcp.tools._asset_models import AssetListInput, AssetPage
from ark_mcp.tools._asset_shared import asset_call, lookup_each, retrying
from ark_mcp.tools._errors import provider_error_result


async def ark_asset_list(input: AssetListInput, ctx: Context) -> AssetPage | ToolResult:
    """List private assets by group, type, status or name, or read up to 20 by asset_ids.

    Returns each asset's asset:// URI and status; only Active assets can be used in generation.
    preview_url is a temporary (~12 h) viewing link; pass asset_uri to Seedance. Assets
    authorized from another account are not listed but usable by ID. Requires AK/SK.
    """
    asset_ids = input.asset_ids

    async def _list(service: AssetService) -> AssetPage:
        if asset_ids:
            assets, errors = await lookup_each(
                asset_ids, lambda asset_id: service.get_asset(asset_id, input.project_name)
            )
            return AssetPage(assets=assets, errors=errors)
        assets_page: tuple[list[Asset], str | None] = await retrying(
            lambda: service.list_assets(
                group_ids=input.group_ids,
                group_type=input.group_type,
                statuses=list(input.statuses) if input.statuses else None,
                name=input.name,
                max_results=input.max_results,
                next_token=input.next_token,
                sort_order=input.sort_order,
                project_name=input.project_name,
            )
        )
        return AssetPage(assets=assets_page[0], next_token=assets_page[1])

    try:
        return await asset_call(ctx, _list)
    except ProviderError as exc:
        return provider_error_result(exc)


TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
