"""Live model pricing from the ModelArk pricing catalog.

The catalog action ``ListModelChargeItems`` (service ``ark``, version
``2024-01-01``) returns each foundation model's final unit prices for the
calling account, with discounts applied. It is a control-plane action, so it
needs the AK/SK-signed gateway; a data-plane Bearer API key is rejected.

Configured model IDs carry a trailing version date (``dreamina-seedance-2-5-260628``)
while the catalog is keyed by the base name (``dreamina-seedance-2-5``).
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING, Any

from ark_mcp.config.env import Settings, get_settings
from ark_mcp.config.model_capabilities import get_capability_registry
from ark_mcp.domain.pricing import ChargeItem, ModelPricing
from ark_mcp.observability.logger import info as log_info
from ark_mcp.observability.logger import warning as log_warning
from ark_mcp.providers.modelark.openapi import ModelArkOpenApiGateway

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

_VERSION_SUFFIX = re.compile(r"-\d{6,8}$")
_PAGE_SIZE = 100
_MAX_PAGES = 10
_MIN_REFRESH_INTERVAL_SECONDS = 60.0


def catalog_name(model_id: str) -> str:
    """Return the pricing catalog name for a configured model ID (version date stripped)."""
    return _VERSION_SUFFIX.sub("", model_id)


def parse_model_pricing(item: Mapping[str, Any]) -> ModelPricing:
    """Convert one ``ListModelChargeItems`` item to a :class:`ModelPricing`."""
    charge_items = [
        ChargeItem(
            charge_type=str(raw["Type"]),
            price=float(raw["Price"]),
            list_price=float(raw.get("OriginalPrice", raw["Price"])),
            unit=str(raw.get("UnitCode", "")),
        )
        for raw in item.get("ChargeItems") or []
        if "Type" in raw and "Price" in raw
    ]
    name = str(item["FoundationModelName"])
    return ModelPricing(
        catalog_name=name,
        display_name=str(item.get("DisplayName") or name),
        available=item.get("State") == "Available",
        charge_items=charge_items,
    )


class PricingService:
    """Fetches model pricing from the ModelArk catalog."""

    def __init__(self, gateway: ModelArkOpenApiGateway | None = None) -> None:
        self._gateway = gateway or ModelArkOpenApiGateway()

    async def close(self) -> None:
        await self._gateway.close()

    async def fetch(self, catalog_names: Iterable[str]) -> dict[str, ModelPricing]:
        """Return pricing keyed by catalog name for the models the catalog knows."""
        names = sorted(set(catalog_names))
        if not names:
            return {}
        prices: dict[str, ModelPricing] = {}
        for page_number in range(1, _MAX_PAGES + 1):
            result = await self._gateway.call(
                "ListModelChargeItems",
                {
                    "Fields": ["Base", "Price"],
                    "Filter": {"FoundationModelNames": names},
                    "PageNumber": page_number,
                    "PageSize": _PAGE_SIZE,
                },
            )
            items = result.get("Items") or []
            for item in items:
                pricing = parse_model_pricing(item)
                prices[pricing.catalog_name] = pricing
            if not items or len(prices) >= int(result.get("TotalCount") or 0):
                break
        return prices


class PriceBook:
    """In-process snapshot of catalog prices with a freshness window."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._prices: dict[str, ModelPricing] = {}
        self._fetched_at: float | None = None
        self._max_age_seconds = 0.0

    def update(self, prices: Mapping[str, ModelPricing], *, max_age_seconds: float) -> None:
        """Replace the snapshot; entries are ignored once older than ``max_age_seconds``."""
        self._prices = dict(prices)
        self._fetched_at = self._clock()
        self._max_age_seconds = max_age_seconds

    def is_fresh(self) -> bool:
        """Whether a snapshot exists and has not outlived its freshness window."""
        if self._fetched_at is None:
            return False
        return self._clock() - self._fetched_at <= self._max_age_seconds

    def lookup(self, model_id: str | None) -> ModelPricing | None:
        """Return fresh pricing for a configured model ID, or None."""
        if model_id is None or not self.is_fresh():
            return None
        return self._prices.get(catalog_name(model_id))

    def clear(self) -> None:
        """Drop the snapshot."""
        self._prices = {}
        self._fetched_at = None


_PRICE_BOOK = PriceBook()


def get_price_book() -> PriceBook:
    """Return the process-wide price book consulted by cost estimation."""
    return _PRICE_BOOK


def configured_model_ids() -> list[str]:
    """Return every model ID bound in the capability registry, deduplicated."""
    registry = get_capability_registry()
    return list(
        dict.fromkeys(
            [
                *registry.list_image_models(),
                *registry.list_video_models(),
                *registry.list_seed3d_models(),
                *registry.list_understanding_models(),
            ]
        )
    )


def _refresh_interval(settings: Settings) -> float:
    return max(float(settings.modelark_pricing_refresh_seconds), _MIN_REFRESH_INTERVAL_SECONDS)


def live_pricing_enabled(settings: Settings | None = None) -> bool:
    """Whether live pricing is configured: AK/SK present and refresh not disabled."""
    resolved = settings or get_settings()
    return resolved.has_modelark_openapi and resolved.modelark_pricing_refresh_seconds > 0


async def refresh_price_book(
    *,
    service: PricingService | None = None,
    price_book: PriceBook | None = None,
) -> bool:
    """Refresh the price book from the catalog; return False (keeping old data) on failure."""
    settings = get_settings()
    book = price_book or _PRICE_BOOK
    owned = service is None
    pricing_service = service or PricingService()
    try:
        prices = await pricing_service.fetch(
            catalog_name(model_id) for model_id in configured_model_ids()
        )
    except Exception as exc:
        log_warning("pricing_refresh_failed", error_type=type(exc).__name__)
        return False
    finally:
        if owned:
            await pricing_service.close()
    book.update(prices, max_age_seconds=_refresh_interval(settings) * 2)
    log_info("pricing_refreshed", models=len(prices))
    return True


async def ensure_fresh_price_book() -> bool:
    """Refresh the price book when live pricing is enabled and the snapshot is stale."""
    if not live_pricing_enabled():
        return False
    if _PRICE_BOOK.is_fresh():
        return True
    return await refresh_price_book()


async def run_price_refresher(settings: Settings) -> None:
    """Refresh the price book now and then every refresh interval, until cancelled."""
    interval = _refresh_interval(settings)
    while True:
        await refresh_price_book()
        await asyncio.sleep(interval)
