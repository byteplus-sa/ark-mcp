"""``ark_asset_verification_start`` tool — start real-person (liveness) verification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from fastmcp import Context
from fastmcp.tools import ToolResult

from ark_mcp.config.env import get_settings
from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.providers.modelark.assets import AssetService
from ark_mcp.tools._asset_models import VerificationStartInput, VerificationStartOutput
from ark_mcp.tools._asset_shared import asset_call
from ark_mcp.tools._errors import provider_error_result

_TOKEN_VALIDITY = timedelta(minutes=30)


def _with_language(link: str, language: str) -> str:
    if not link or "lng=" in link:
        return link
    separator = "&" if "?" in link else "?"
    return f"{link}{separator}lng={language}"


async def ark_asset_verification_start(
    input: VerificationStartInput, ctx: Context
) -> VerificationStartOutput | ToolResult:
    """Start real-person verification so a real person's face can be used in Seedance.

    Returns an H5 verification link for the person to open themselves: they
    confirm consent and pass a liveness check. Then call
    ark_asset_verification_result with the returned verification_token to get
    their dedicated LivenessFace asset group, and upload only that person's
    media to it. Never verify someone without their consent. The link carries
    temporary credentials; share it only with that person. Requires Advanced
    Creation Rights (API real-person verification) and BytePlus AK/SK.
    """
    callback_url = input.callback_url or get_settings().modelark_asset_verify_callback_url
    if not callback_url:
        raise ValueError(
            "callback_url is required (or set BYTEPLUS_MODELARK_ASSET_VERIFY_CALLBACK_URL): "
            "the person is redirected there after verification."
        )
    parsed = urlsplit(callback_url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("callback_url must be an HTTPS URL.")

    async def _start(service: AssetService) -> dict[str, str]:
        return await service.create_verification_session(
            callback_url=callback_url, project_name=input.project_name
        )

    try:
        session = await asset_call(ctx, _start)
    except ProviderError as exc:
        return provider_error_result(exc)

    # Never log the link or token: the link embeds temporary credentials.
    log_info("asset_verification_started")
    return VerificationStartOutput(
        h5_link=_with_language(session["h5_link"], input.language),
        verification_token=session["byted_token"],
        expires_at=(datetime.now(UTC) + _TOKEN_VALIDITY).isoformat(),
        next_step=(
            "Send h5_link to the person being verified. After they finish, call "
            "ark_asset_verification_result with verification_token (use wait_seconds to poll)."
        ),
    )


TOOL_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "idempotentHint": False,
    "openWorldHint": True,
}
