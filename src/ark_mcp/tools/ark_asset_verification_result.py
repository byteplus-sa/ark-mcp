"""``ark_asset_verification_result`` tool — fetch the verified person's asset group."""

from __future__ import annotations

import asyncio
import time

from fastmcp import Context
from fastmcp.tools import ToolResult

from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.assets import AssetService
from ark_mcp.tools._asset_models import VerificationResultInput, VerificationResultOutput
from ark_mcp.tools._asset_shared import asset_call
from ark_mcp.tools._errors import provider_error_result

# GetVisualValidateResult is limited to 3 QPS per account; poll gently.
_POLL_INTERVAL_SECONDS = 5.0


async def ark_asset_verification_result(
    input: VerificationResultInput, ctx: Context
) -> VerificationResultOutput | ToolResult:
    """Get the LivenessFace asset group created by a finished real-person verification.

    Returns status 'verified' with group_id once the person has passed, or
    'pending' when no group exists yet. wait_seconds keeps polling (up to 600 s).
    The token expires about 30 minutes after ark_asset_verification_start. Upload
    only that person's media to the group with ark_asset_create(group_id=...).
    Requires BytePlus AK/SK.
    """
    deadline = time.monotonic() + input.wait_seconds

    async def _poll(service: AssetService) -> tuple[str | None, ProviderError | None]:
        last_error: ProviderError | None = None
        while True:
            try:
                group_id = await service.get_verification_result(
                    byted_token=input.verification_token, project_name=input.project_name
                )
                last_error = None
            except ProviderError as exc:
                group_id, last_error = None, exc
                if exc.error.http_status in {401, 403}:
                    return None, exc
            if group_id or time.monotonic() + _POLL_INTERVAL_SECONDS > deadline:
                return group_id, last_error
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)

    group_id, error = await asset_call(ctx, _poll)
    if group_id:
        log_info("asset_verification_completed", group_id=group_id)
        return VerificationResultOutput(
            status="verified",
            group_id=group_id,
            message="Verification passed. Upload this person's media with ark_asset_create.",
        )
    if error is not None and (input.wait_seconds == 0 or error.error.http_status in {401, 403}):
        return provider_error_result(error)
    detail = f" Last provider response: {error.error.message}" if error is not None else ""
    return VerificationResultOutput(
        status="pending",
        group_id=None,
        message=(
            "No verified group yet: the person may not have finished, or verification "
            f"failed and needs a new link.{detail}"
        ),
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": True,
}
