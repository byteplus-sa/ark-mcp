"""Cached pricing must not turn model discovery into a provider request."""

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from ark_mcp.config.env import get_settings
from ark_mcp.config.model_capabilities import refresh_capability_registry
from ark_mcp.domain.pricing import ChargeItem, ModelPricing
from ark_mcp.providers.modelark.pricing import catalog_name, get_price_book
from ark_mcp.tools.ark_list_models import ArkListModelsInput, ark_list_models


@pytest.mark.parametrize("max_age", [-1, 3600])
async def test_discovery_reads_cached_prices_without_network(
    monkeypatch: pytest.MonkeyPatch, max_age: float
) -> None:
    monkeypatch.setenv("BYTEPLUS_MODELARK_API_KEY", "not-a-real-key")
    monkeypatch.setenv("BYTEPLUS_MODELARK_ACCESS_KEY", "AKLTtest")
    monkeypatch.setenv("BYTEPLUS_MODELARK_SECRET_KEY", "fixture-only")  # pragma: allowlist secret
    get_settings.cache_clear()
    refresh_capability_registry()
    settings = get_settings()
    name = catalog_name(settings.seedream_default_model)
    price = ModelPricing(
        catalog_name=name,
        display_name="Seedream",
        available=True,
        charge_items=[
            ChargeItem(charge_type="ToICompletion", price=0.04, list_price=0.05, unit="Piece")
        ],
    )
    get_price_book().update({name: price}, max_age_seconds=max_age)
    fetch = AsyncMock(side_effect=AssertionError("Discovery must not query providers"))
    monkeypatch.setattr("ark_mcp.providers.modelark.pricing.PricingService.fetch", fetch)
    try:
        result = await ark_list_models(ArkListModelsInput(kind="image"), cast("Any", None))
        assert result.models
        model = next(
            item for item in result.models if item.model_id == settings.seedream_default_model
        )
        assert model.tool == "seedream_generate_image"
        assert model.image is not None
        assert result.services
        assert result.pricing_status == ("live" if max_age > 0 else "unavailable")
        assert model.pricing == (price if max_age > 0 else None)
        fetch.assert_not_awaited()
    finally:
        get_settings.cache_clear()
        refresh_capability_registry()


async def test_missing_credentials_explain_unavailable_prices() -> None:
    result = await ark_list_models(ArkListModelsInput(), cast("Any", None))
    assert result.pricing_status == "unavailable"
    assert "AK/SK" in (result.pricing_note or "")
