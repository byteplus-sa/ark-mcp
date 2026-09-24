"""``ark_asset_delete`` tool — irreversibly delete one asset."""

from __future__ import annotations

from fastmcp import Context
from fastmcp.tools import ToolResult

from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.assets import AssetService
from ark_mcp.tools._asset_models import AssetDeleteInput, DeleteOutput
from ark_mcp.tools._asset_shared import asset_call
from ark_mcp.tools._errors import provider_error_result


async def ark_asset_delete(input: AssetDeleteInput, ctx: Context) -> DeleteOutput | ToolResult:
    """Permanently delete one asset. Irreversible; requires confirm=true.

    Check last_inference_time (ark_asset_get) and your own records before
    deleting. Only registered when BYTEPLUS_MODELARK_ASSETS_ALLOW_DELETE=true.
    """

    async def _delete(service: AssetService) -> None:
        await service.delete_asset(input.asset_id, input.project_name)

    try:
        await asset_call(ctx, _delete)
    except ProviderError as exc:
        return provider_error_result(exc)
    log_info("asset_deleted", asset_id=input.asset_id)
    return DeleteOutput(id=input.asset_id, deleted=True)


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": False,
    "openWorldHint": True,
}
