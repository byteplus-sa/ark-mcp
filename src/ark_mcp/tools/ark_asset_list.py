"""``ark_asset_list`` tool — list assets with filters and pagination."""

from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import ToolResult

from ark_mcp.domain.assets import Asset
from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.modelark.assets import AssetService
from ark_mcp.tools._asset_models import AssetListInput, AssetPage
from ark_mcp.tools._asset_shared import asset_call, retrying
from ark_mcp.tools._errors import provider_error_result


async def ark_asset_list(input: AssetListInput, ctx: Context) -> AssetPage | ToolResult:
    """List private assets filtered by group IDs, group type, status, or fuzzy name.

    Returns each asset's asset:// URI and status. Pass next_token to page.
    Requires BytePlus AK/SK.
    """

    async def _list(service: AssetService) -> tuple[list[Asset], str | None]:
        return await retrying(
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

    try:
        assets, next_token = await asset_call(ctx, _list)
    except ProviderError as exc:
        return provider_error_result(exc)
    return AssetPage(assets=assets, next_token=next_token)


TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
