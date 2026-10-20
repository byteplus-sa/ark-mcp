"""Unit tests for the pricing domain models, price book, and refresh logic."""

from __future__ import annotations

import pytest

from ark_mcp.domain.errors import NormalizedProviderError, ProviderError
from ark_mcp.domain.pricing import ChargeItem, ModelPricing
from ark_mcp.providers.modelark.pricing import (
    PriceBook,
    PricingService,
    catalog_name,
    configured_model_ids,
    ensure_fresh_price_book,
    get_price_book,
    live_pricing_enabled,
    parse_model_pricing,
    refresh_price_book,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _pricing(name: str = "dreamina-seedance-2-5") -> ModelPricing:
    return ModelPricing(
        catalog_name=name,
        display_name=name,
        available=True,
        charge_items=[
            ChargeItem(charge_type="ToICompletion", price=0.045, list_price=0.045, unit="Piece"),
            ChargeItem(
                charge_type="InferencePrompt", price=0.0004, list_price=0.0005, unit="K tokens"
            ),
        ],
    )


class FakeService(PricingService):
    def __init__(self, prices: dict[str, ModelPricing] | Exception) -> None:
        self._prices = prices
        self.requested: list[str] = []
        self.closed = False

    async def fetch(self, catalog_names):  # type: ignore[no-untyped-def]
        self.requested = sorted(set(catalog_names))
        if isinstance(self._prices, Exception):
            raise self._prices
        return self._prices

    async def close(self) -> None:
        self.closed = True


class TestCatalogName:
    @pytest.mark.parametrize(
        ("model_id", "expected"),
        [
            ("dreamina-seedance-2-5-260628", "dreamina-seedance-2-5"),
            ("dreamina-seedance-2-5-premium-260915", "dreamina-seedance-2-5-premium"),
            ("hitem3d-2-0-251223", "hitem3d-2-0"),
            ("hyper3d-gen2-260112", "hyper3d-gen2"),
            ("dola-seed-evolving", "dola-seed-evolving"),
            ("seedream-5-0", "seedream-5-0"),
            ("model-20260628", "model"),
        ],
    )
    def test_strips_trailing_version_date(self, model_id: str, expected: str) -> None:
        assert catalog_name(model_id) == expected


class TestChargeItem:
    def test_per_k_tokens_converts_to_per_million(self) -> None:
        item = ChargeItem(
            charge_type="InferencePrompt", price=0.0004, list_price=0.0005, unit="K tokens"
        )
        assert item.price_per_million_tokens == pytest.approx(0.4)
        assert item.price_per_piece is None

    def test_chinese_unit_label_is_per_thousand_tokens(self) -> None:
        item = ChargeItem(charge_type="Inference", price=1, list_price=1, unit="千tokens")
        assert item.price_per_million_tokens == 1000

    def test_piece_unit_is_per_item(self) -> None:
        item = ChargeItem(charge_type="ToICompletion", price=0.045, list_price=0.045, unit="Piece")
        assert item.price_per_piece == 0.045
        assert item.price_per_million_tokens is None

    def test_storage_unit_is_neither(self) -> None:
        item = ChargeItem(
            charge_type="ContextSessionStorage", price=1, list_price=1, unit="K tokens/hour"
        )
        assert item.price_per_million_tokens is None
        assert item.price_per_piece is None


class TestModelPricing:
    def test_charge_item_respects_priority_order(self) -> None:
        pricing = _pricing()
        item = pricing.charge_item("Missing", "InferencePrompt", "ToICompletion")
        assert item is not None
        assert item.charge_type == "InferencePrompt"

    def test_charge_item_returns_none_when_nothing_matches(self) -> None:
        assert _pricing().charge_item("Missing") is None


class TestParseModelPricing:
    def test_defaults_missing_optional_fields(self) -> None:
        pricing = parse_model_pricing(
            {
                "FoundationModelName": "m",
                "State": "Unavailable",
                "ChargeItems": [{"Type": "InferencePrompt", "Price": 2}],
            }
        )
        assert pricing.display_name == "m"
        assert pricing.available is False
        assert pricing.charge_items[0].list_price == 2
        assert pricing.charge_items[0].unit == ""

    def test_missing_charge_items_yields_empty_list(self) -> None:
        assert parse_model_pricing({"FoundationModelName": "m"}).charge_items == []


class TestPriceBook:
    def test_lookup_resolves_versioned_model_id(self) -> None:
        book = PriceBook(FakeClock())
        book.update({"dreamina-seedance-2-5": _pricing()}, max_age_seconds=100)
        assert book.lookup("dreamina-seedance-2-5-260628") is not None

    def test_lookup_none_before_first_update(self) -> None:
        assert PriceBook(FakeClock()).lookup("anything") is None

    def test_lookup_none_for_none_model(self) -> None:
        book = PriceBook(FakeClock())
        book.update({"a": _pricing("a")}, max_age_seconds=100)
        assert book.lookup(None) is None

    def test_entries_expire_after_max_age(self) -> None:
        clock = FakeClock()
        book = PriceBook(clock)
        book.update({"a": _pricing("a")}, max_age_seconds=100)
        clock.now += 100
        assert book.lookup("a") is not None
        clock.now += 1
        assert book.lookup("a") is None
        assert book.is_fresh() is False

    def test_unknown_model_returns_none(self) -> None:
        book = PriceBook(FakeClock())
        book.update({"a": _pricing("a")}, max_age_seconds=100)
        assert book.lookup("b") is None

    def test_clear_drops_snapshot(self) -> None:
        book = PriceBook(FakeClock())
        book.update({"a": _pricing("a")}, max_age_seconds=100)
        book.clear()
        assert book.lookup("a") is None


class TestRefresh:
    async def test_refresh_requests_catalog_names_and_populates_book(self) -> None:
        book = PriceBook()
        service = FakeService({"dreamina-seedance-2-5": _pricing()})
        assert await refresh_price_book(service=service, price_book=book) is True
        assert service.requested
        assert all(catalog_name(name) == name for name in service.requested)
        assert book.lookup("dreamina-seedance-2-5-260628") is not None

    async def test_refresh_failure_returns_false_and_keeps_previous_snapshot(self) -> None:
        book = PriceBook()
        book.update({"a": _pricing("a")}, max_age_seconds=3600)
        failure = ProviderError(
            NormalizedProviderError(
                provider="modelark-openapi",
                operation="ListModelChargeItems",
                code="InvalidAccessKey",
                message="bad key",
                retryable=False,
            )
        )
        assert await refresh_price_book(service=FakeService(failure), price_book=book) is False
        assert book.lookup("a") is not None

    async def test_refresh_does_not_close_caller_owned_service(self) -> None:
        service = FakeService({})
        await refresh_price_book(service=service, price_book=PriceBook())
        assert service.closed is False

    async def test_ensure_fresh_is_noop_without_openapi_credentials(self) -> None:
        assert live_pricing_enabled() is False
        assert await ensure_fresh_price_book() is False

    async def test_ensure_fresh_skips_refresh_when_snapshot_is_fresh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BYTEPLUS_MODELARK_ACCESS_KEY", "AKLTtest")
        monkeypatch.setenv(
            "BYTEPLUS_MODELARK_SECRET_KEY", "secret-test"
        )  # pragma: allowlist secret
        from ark_mcp.config.env import get_settings

        get_settings.cache_clear()
        get_price_book().update({"a": _pricing("a")}, max_age_seconds=3600)
        assert live_pricing_enabled() is True
        assert await ensure_fresh_price_book() is True

    def test_zero_refresh_interval_disables_live_pricing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("BYTEPLUS_MODELARK_ACCESS_KEY", "AKLTtest")
        monkeypatch.setenv(
            "BYTEPLUS_MODELARK_SECRET_KEY", "secret-test"
        )  # pragma: allowlist secret
        monkeypatch.setenv("BYTEPLUS_MODELARK_PRICING_REFRESH_SECONDS", "0")
        from ark_mcp.config.env import get_settings

        get_settings.cache_clear()
        assert live_pricing_enabled() is False


def test_configured_model_ids_are_unique() -> None:
    ids = configured_model_ids()
    assert ids
    assert len(ids) == len(set(ids))


class TestRefresher:
    async def test_refresher_repeats_until_cancelled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import asyncio

        from ark_mcp.config.env import get_settings
        from ark_mcp.providers.modelark.pricing import run_price_refresher

        calls = 0
        refreshed_twice = asyncio.Event()

        async def fake_refresh() -> bool:
            nonlocal calls
            calls += 1
            if calls == 2:
                refreshed_twice.set()
            return True

        monkeypatch.setattr("ark_mcp.providers.modelark.pricing.refresh_price_book", fake_refresh)
        monkeypatch.setattr(
            "ark_mcp.providers.modelark.pricing._MIN_REFRESH_INTERVAL_SECONDS", 0.01
        )
        monkeypatch.setenv("BYTEPLUS_MODELARK_PRICING_REFRESH_SECONDS", "0")
        task = asyncio.create_task(run_price_refresher(get_settings()))
        try:
            await asyncio.wait_for(refreshed_twice.wait(), timeout=2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert calls >= 2

    async def test_refresh_survives_unexpected_errors(self) -> None:
        book = PriceBook()
        service = FakeService(RuntimeError("boom"))
        assert await refresh_price_book(service=service, price_book=book) is False
        assert book.is_fresh() is False
