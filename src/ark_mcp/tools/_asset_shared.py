"""Shared helpers for the private asset library tools and ``asset://`` references.

- Running AK/SK-signed asset calls under the shared provider limiter.
- Resolving a subject name to exactly one ``AIGC`` asset group.
- Turning an upload source (URL or ``media_upload`` object key) into the
  publicly reachable URL ``CreateAsset`` needs.
- Waiting for assets to reach ``Active``.
- Handling ``asset://`` references for Seedance (preflight) and for
  Seedream / Seed Audio (``BYTEPLUS_MODELARK_ASSET_REFERENCE_MODE``).

See ``plans/PLAN_MODELARK_ASSET_LIBRARY.md``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Literal

from fastmcp import Context

from ark_mcp.config.env import get_settings
from ark_mcp.domain.assets import Asset, AssetGroup, is_asset_uri, parse_asset_uri
from ark_mcp.domain.errors import ProviderError
from ark_mcp.observability.logger import info as log_info
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.providers.modelark.assets import AssetService, AssetTypeName
from ark_mcp.providers.retry import call_with_retry
from ark_mcp.runtime import get_principal, get_runtime
from ark_mcp.security.url_policy import UrlValidationError, validate_url

_ASSET_TYPE_BY_EXTENSION: dict[str, AssetTypeName] = {
    ".jpg": "Image",
    ".jpeg": "Image",
    ".png": "Image",
    ".webp": "Image",
    ".bmp": "Image",
    ".tif": "Image",
    ".tiff": "Image",
    ".gif": "Image",
    ".heic": "Image",
    ".heif": "Image",
    ".mp4": "Video",
    ".mov": "Video",
    ".wav": "Audio",
    ".mp3": "Audio",
}
_OBJECT_KEY_TYPE_SEGMENTS: dict[str, AssetTypeName] = {
    "image": "Image",
    "video": "Video",
    "audio": "Audio",
}
TERMINAL_ASSET_STATUSES = frozenset({"Active", "Failed"})


def require_asset_library() -> None:
    """Raise a clear error when AK/SK for the asset library are not configured."""
    if not get_settings().has_modelark_openapi:
        raise ValueError(
            "The private asset library needs BytePlus AK/SK. Set BYTEPLUS_MODELARK_ACCESS_KEY "
            "and BYTEPLUS_MODELARK_SECRET_KEY (plus BYTEPLUS_MODELARK_SESSION_TOKEN for "
            "temporary AKTP... keys)."
        )


async def asset_call[T](ctx: Context, operation: Callable[[AssetService], Awaitable[T]]) -> T:
    """Run one or more asset operations under the shared ``modelark-openapi`` limiter.

    Only non-ambiguous retryable errors (throttling, 5xx on reads) are retried;
    ``CreateAsset`` / ``CreateAssetGroup`` 5xx responses are ambiguous and raise.
    """
    require_asset_library()
    runtime = get_runtime(ctx)
    service = AssetService()
    try:
        async with runtime.provider_limiters.acquire("modelark-openapi", get_principal(ctx)):
            return await operation(service)
    finally:
        await service.close()


async def retrying[T](operation: Callable[[], Awaitable[T]]) -> T:
    """Retry an asset call with the shared deterministic provider retry policy."""
    return await call_with_retry(operation)


async def resolve_subject_group(
    service: AssetService,
    *,
    subject: str,
    description: str | None,
    project_name: str | None,
    create_if_missing: bool,
) -> tuple[AssetGroup, bool]:
    """Resolve ``subject`` to exactly one ``AIGC`` group by exact name.

    Returns ``(group, created)``. Raises ``ValueError`` when two or more groups
    share the exact name, so the caller never writes into an arbitrary one.
    """
    matches: list[AssetGroup] = []
    next_token: str | None = None
    for _ in range(20):  # bounded scan: at most 20 pages of fuzzy matches
        token = next_token

        async def _page(token: str | None = token) -> tuple[list[AssetGroup], str | None]:
            return await service.list_groups(
                name=subject,
                group_ids=None,
                group_type="AIGC",
                max_results=100,
                next_token=token,
                sort_order=None,
                project_name=project_name,
            )

        groups, next_token = await retrying(_page)
        matches.extend(group for group in groups if group.name == subject)
        if not next_token:
            break

    if len(matches) > 1:
        ids = ", ".join(group.group_id for group in matches)
        raise ValueError(
            f"ambiguous_asset_group: {len(matches)} AIGC groups are named '{subject}' "
            f"({ids}). Pass group_id explicitly."
        )
    if matches:
        return matches[0], False
    if not create_if_missing:
        raise ValueError(f"No AIGC asset group is named '{subject}'.")

    group_id = await service.create_group(
        name=subject, description=description, project_name=project_name
    )
    log_info("asset_group_created", group_id=group_id)
    return (
        AssetGroup(
            group_id=group_id,
            name=subject,
            description=description or "",
            group_type="AIGC",
            project_name=project_name or get_settings().modelark_project_name,
        ),
        True,
    )


def infer_asset_type(*, url: str | None, object_key: str | None) -> AssetTypeName | None:
    """Infer Image/Video/Audio from a URL path extension or a media_upload object key."""
    if object_key:
        for segment in object_key.lower().split("/"):
            if segment in _OBJECT_KEY_TYPE_SEGMENTS:
                return _OBJECT_KEY_TYPE_SEGMENTS[segment]
    if url:
        path = url.split("?", 1)[0].split("#", 1)[0].lower()
        for extension, asset_type in _ASSET_TYPE_BY_EXTENSION.items():
            if path.endswith(extension):
                return asset_type
    return None


async def presign_object_key(ctx: Context, object_key: str, expires: int) -> str:
    """Mint a presigned GET URL for a caller-owned ``media_upload`` object key."""
    from ark_mcp.providers.object_storage import make_object_storage_gateway

    settings = get_settings()
    if not settings.has_object_storage:
        raise ValueError(
            "object_key sources need object storage. Set TOS_* or S3_* credentials, or pass "
            "a public HTTPS url instead."
        )
    await get_runtime(ctx).object_key_ownership_store.require_owner(object_key, get_principal(ctx))
    gateway = make_object_storage_gateway(settings)
    try:
        return await call_with_retry(lambda: gateway.presign_get(key=object_key, expires=expires))
    finally:
        await gateway.close()


async def _get_asset(service: AssetService, asset_id: str, project_name: str | None) -> Asset:
    async def _once() -> Asset:
        return await service.get_asset(asset_id, project_name)

    return await retrying(_once)


async def wait_until_terminal(
    service: AssetService,
    asset_ids: Iterable[str],
    *,
    project_name: str | None,
    timeout_seconds: float,
) -> dict[str, Asset]:
    """Poll ``GetAsset`` until every asset is Active/Failed or the timeout passes.

    Returns the last observed state per asset. Polling backs off from 1s to 5s.
    """
    pending = list(dict.fromkeys(asset_ids))
    latest: dict[str, Asset] = {}
    deadline = time.monotonic() + timeout_seconds
    delay = 1.0
    while pending:
        for asset_id in list(pending):
            try:
                asset = await _get_asset(service, asset_id, project_name)
            except ProviderError as exc:
                log_warning("asset_status_poll_failed", code=exc.error.code)
                continue
            latest[asset_id] = asset
            if asset.status in TERMINAL_ASSET_STATUSES:
                pending.remove(asset_id)
        if not pending or time.monotonic() + delay > deadline:
            break
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, 5.0)
    return latest


# --- asset:// references in generation tools ---------------------------------


def collect_asset_ids(*groups: Iterable[dict[str, Any]] | None) -> list[str]:
    """Return distinct asset IDs referenced by ``url`` fields of dumped media inputs."""
    ids: list[str] = []
    for group in groups:
        for item in group or []:
            url = item.get("url")
            if isinstance(url, str) and is_asset_uri(url):
                ids.append(parse_asset_uri(url))
    return list(dict.fromkeys(ids))


async def preflight_seedance_assets(ctx: Context, asset_ids: list[str]) -> None:
    """Stop a billed Seedance submit early when a referenced asset is not usable.

    Runs only when AK/SK are configured and ``SEEDANCE_ASSET_PREFLIGHT`` is on.
    ``Processing`` or ``Failed`` raises ``ValueError``. A not-found or any other
    lookup error does *not* block: console-authorized real-human assets and
    copyright IP assets belong to other accounts and cannot be looked up here.
    """
    settings = get_settings()
    if not asset_ids or not settings.has_modelark_openapi or not settings.seedance_asset_preflight:
        return

    async def _check(service: AssetService) -> list[tuple[str, str]]:
        async def _one(asset_id: str) -> tuple[str, str]:
            try:
                asset = await _get_asset(service, asset_id, None)
            except ProviderError as exc:
                log_info("seedance_asset_preflight_skipped", code=exc.error.code)
                return asset_id, ""
            return asset_id, asset.status

        return list(await asyncio.gather(*(_one(asset_id) for asset_id in asset_ids)))

    statuses = await asset_call(ctx, _check)
    blocked = [
        (asset_id, status) for asset_id, status in statuses if status in {"Processing", "Failed"}
    ]
    if blocked:
        details = ", ".join(f"{asset_id} is {status}" for asset_id, status in blocked)
        raise ValueError(
            f"asset_not_active: {details}. Only Active assets can be used; wait for "
            "Processing assets (ark_asset_get) or re-upload Failed ones."
        )


async def resolve_asset_references(
    ctx: Context,
    items: list[dict[str, Any]] | None,
    *,
    product: Literal["Seedream", "Seed Audio"],
    expected_type: AssetTypeName,
) -> list[dict[str, Any]] | None:
    """Apply ``BYTEPLUS_MODELARK_ASSET_REFERENCE_MODE`` to dumped media inputs.

    Seedream and Seed Audio reject ``asset://`` natively (HTTP 400 in the
    2026-09-24 live probe), so it is never forwarded unchanged:

    - ``off``: reject ``asset://`` locally with a clear message.
    - ``resolve``: replace it with the asset's temporary ``GetAsset`` URL after
      checking status ``Active`` and the expected asset type. Experimental: the
      model then receives a plain URL without asset-library trust guarantees.
    """
    if not items or not any(is_asset_uri(item.get("url")) for item in items):
        return items
    mode = get_settings().modelark_asset_reference_mode
    if mode == "off":
        raise ValueError(
            f"{product} does not accept asset:// references; only Seedance uses them "
            "natively. Set BYTEPLUS_MODELARK_ASSET_REFERENCE_MODE=resolve (experimental: "
            "sends the asset's temporary download URL) or pass a regular HTTPS URL."
        )
    wanted = [parse_asset_uri(item["url"]) for item in items if is_asset_uri(item.get("url"))]

    async def _resolve(service: AssetService) -> dict[str, Asset]:
        resolved: dict[str, Asset] = {}
        for asset_id in dict.fromkeys(wanted):
            try:
                resolved[asset_id] = await _get_asset(service, asset_id, None)
            except ProviderError as exc:
                raise ValueError(
                    f"asset_not_resolvable: {asset_id} could not be looked up "
                    f"({exc.error.code or exc.error.message}). Assets authorized by other "
                    "accounts and copyright IP assets can only be used with Seedance."
                ) from exc
        return resolved

    resolved = await asset_call(ctx, _resolve)
    output: list[dict[str, Any]] = []
    for item in items:
        url = item.get("url")
        if not isinstance(url, str) or not is_asset_uri(url):
            output.append(item)
            continue
        asset = resolved[parse_asset_uri(url)]
        if asset.status != "Active" or not asset.preview_url:
            raise ValueError(f"asset_not_active: {asset.asset_id} is {asset.status or 'unknown'}.")
        if asset.asset_type and asset.asset_type != expected_type:
            raise ValueError(
                f"asset_type_mismatch: {asset.asset_id} is {asset.asset_type}, but this "
                f"{product} input needs {expected_type}."
            )
        try:
            validate_url(asset.preview_url)
        except UrlValidationError as exc:
            raise ValueError(UrlValidationError.safe_message) from exc
        output.append({**item, "url": asset.preview_url})
    log_info("asset_references_resolved", product=product, count=len(wanted))
    return output
