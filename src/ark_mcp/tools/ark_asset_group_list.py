"""``ark_asset_group_list`` tool — list asset groups."""

from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import ToolResult

from ark_mcp.domain.assets import AssetGroup
from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.modelark.assets import AssetService
from ark_mcp.tools._asset_models import AssetGroupListInput, AssetGroupPage
from ark_mcp.tools._asset_shared import asset_call, retrying
from ark_mcp.tools._errors import provider_error_result


async def ark_asset_group_list(
    input: AssetGroupListInput, ctx: Context
) -> AssetGroupPage | ToolResult:
    """List private asset groups, filtered by fuzzy name, IDs, or type, with pagination.

    group_type 'AIGC' = virtual portraits (Virtual Portrait tab), 'LivenessFace'
    = verified real people (Real-human tab). Pass next_token to page. Requires
    BytePlus AK/SK.
    """

    async def _list(service: AssetService) -> tuple[list[AssetGroup], str | None]:
        return await retrying(
            lambda: service.list_groups(
                name=input.name,
                group_ids=input.group_ids,
                group_type=input.group_type,
                max_results=input.max_results,
                next_token=input.next_token,
                sort_order=input.sort_order,
                project_name=input.project_name,
            )
        )

    try:
        groups, next_token = await asset_call(ctx, _list)
    except ProviderError as exc:
        return provider_error_result(exc)
    return AssetGroupPage(groups=groups, next_token=next_token)


TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
