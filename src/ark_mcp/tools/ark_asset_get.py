"""``ark_asset_get`` tool — read one asset and its processing status."""

from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import ToolResult

from ark_mcp.domain.assets import Asset
from ark_mcp.domain.errors import ProviderError
from ark_mcp.providers.modelark.assets import AssetService
from ark_mcp.tools._asset_models import AssetGetInput, AssetResult
from ark_mcp.tools._asset_shared import asset_call, retrying
from ark_mcp.tools._errors import provider_error_result


async def ark_asset_get(input: AssetGetInput, ctx: Context) -> AssetResult | ToolResult:
    """Get one private asset: status (Processing/Active/Failed), type, group, and asset:// URI.

    Only Active assets can be used in generation. preview_url is a temporary
    (~12 h) download link for viewing only; always pass asset_uri to Seedance.
    Assets authorized from another account are not visible here but can still
    be used by ID. Requires BytePlus AK/SK.
    """

    async def _get(service: AssetService) -> Asset:
        return await retrying(lambda: service.get_asset(input.asset_id, input.project_name))

    try:
        asset = await asset_call(ctx, _get)
    except ProviderError as exc:
        return provider_error_result(exc)
    return AssetResult(asset=asset)


TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
